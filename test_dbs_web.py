"""Offline transport/privacy/state tests; provider calls are explicitly mocked."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import WSServerHandshakeError
from aiohttp.test_utils import TestClient, TestServer
from livekit import rtc

from dbs_flow import Intent, Participant, Prompt
from dbs_web import DemoSession, create_app


class SessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ws = Mock(closed=False, send_json=AsyncMock(), send_bytes=AsyncMock())
        self.session = DemoSession(self.ws, 'en')
        self.session.stream = Mock()

    def events(self, kind):
        return [c.args[0] for c in self.ws.send_json.call_args_list if c.args[0]['type'] == kind]

    async def test_real_identifier_never_leaks_into_debug_state(self):
        self.session.controller.flow.roster = [Participant('Alex', 'S1', ('private-biometric-test-fixture',))]
        state = self.session.state()
        self.assertTrue(state['roster'][0]['voice_enrolled'])
        self.assertNotIn('private-biometric-test-fixture', json.dumps(state))

    async def test_microphone_audio_is_zeroed_during_playback(self):
        data = b'\x01\x01' * 320
        await self.session.feed_audio(data)
        self.assertEqual(bytes(self.session.stream.push_frame.call_args.args[0].data), bytes(640))
        self.session.busy = False
        await self.session.feed_audio(data)
        self.assertEqual(bytes(self.session.stream.push_frame.call_args.args[0].data), data)
        self.assertEqual(self.session.accepted_bytes, 640)

    async def test_invalid_audio_and_flood_fail_closed(self):
        await self.session.feed_audio(b'x')
        self.assertTrue(self.session.closed.is_set())
        self.session.closed.clear()
        for _ in range(12):
            await self.session.feed_audio(bytes(6400))
        self.assertTrue(self.session.closed.is_set())
        self.assertTrue(self.events('error'))

    async def test_late_microphone_packets_after_stop_are_discarded(self):
        self.session.closed.set()
        self.session.stream.push_frame.side_effect = RuntimeError('closed stream')
        await self.session.feed_audio(bytes(3200))
        self.session.stream.push_frame.assert_not_called()
        self.assertEqual(self.events('error'), [])

    async def test_name_confirmation_cannot_be_forged_by_button(self):
        flow = self.session.controller.flow
        flow.phase = 'confirm_name'
        flow.pending = Participant('Alex', 'S1', ('test-only',))
        self.session.busy = False
        await self.session.handle_control('yes')
        self.assertEqual(flow.roster, [])
        self.assertTrue(self.session.queue.empty())
        self.assertEqual(self.events('error')[-1]['code'], 'voice_confirmation_required')

    async def test_button_waits_while_speaking_but_stop_is_immediate(self):
        await self.session.handle_control('next')
        self.assertTrue(self.session.queue.empty())
        await self.session.handle_control('stop')
        self.assertTrue(self.session.closed.is_set())
        self.assertTrue(self.events('cancel_audio'))

    async def test_output_waits_for_matching_playback_ack(self):
        frames = [rtc.AudioFrame(data=b'\x01\x00' * 320, sample_rate=16000, num_channels=1, samples_per_channel=320)]
        with patch('dbs_web.synthesize', new=AsyncMock(return_value=frames)):
            task = asyncio.create_task(self.session.speak([Prompt('welcome')]))
            for _ in range(100):
                if self.session.played:
                    break
                await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertTrue(self.events('audio'))
            self.session.played.set_result(True)
            await asyncio.wait_for(task, 1)
        self.assertEqual(self.session.remaining_prompts, [])
        self.assertEqual(self.ws.send_bytes.call_args.args[0], b'\x01\x00' * 320)

    async def test_tts_error_rolls_back_and_is_not_logged_to_browser(self):
        flow = self.session.controller.flow
        flow.phase, flow.index = 'confirm_next', 1
        with patch('dbs_web.synthesize', new=AsyncMock(side_effect=RuntimeError('secret-provider-fixture'))):
            await self.session.process((Intent.YES, '', '', 'button'))
        self.assertEqual(self.session.controller.flow.index, 1)
        self.assertEqual(self.session.controller.flow.phase, 'confirm_next')
        self.assertTrue(self.session.closed.is_set())
        self.assertNotIn('secret-provider-fixture', json.dumps([c.args[0] for c in self.ws.send_json.call_args_list]))

    async def test_cancel_and_resume_replays_interrupted_prompt(self):
        flow = self.session.controller.flow
        flow.phase, flow.index = 'lesson', 1
        self.session.remaining_prompts = [Prompt('f.002')]
        await self.session.enqueue((Intent.YES, '', '', 'button'))
        await self.session.handle_control('pause')
        self.assertEqual(self.session.queue.qsize(), 1)
        self.assertEqual(self.session.resume_prompts, [Prompt('f.002')])
        with patch.object(self.session, 'speak', new=AsyncMock()) as speak:
            await self.session.process(await self.session.queue.get())
            self.assertTrue(self.session.controller.flow.paused)
            await self.session.process((Intent.RESUME, '', '', 'button'))
            self.assertEqual(speak.call_args.args[0], [Prompt('resumed'), Prompt('f.002')])
        self.assertEqual(self.session.controller.flow.index, 1)


class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.origins = {'http://placeholder.invalid'}
        self.client = TestClient(TestServer(create_app(origins=self.origins)))
        await self.client.start_server()
        self.origin = str(self.client.make_url('/')).rstrip('/')
        self.origins.add(self.origin)

    async def asyncTearDown(self):
        await self.client.close()

    async def test_health_and_security_headers(self):
        result = await self.client.get('/health')
        self.assertEqual(result.status, 200)
        self.assertEqual((await result.json())['active_sessions'], 0)
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        self.assertEqual(result.headers['X-Frame-Options'], 'DENY')
        self.assertIn("frame-ancestors 'none'", result.headers['Content-Security-Policy'])

    async def test_no_directory_or_credential_exposure(self):
        for path in ('/.env', '/dbs/.env', '/dbs/dbs_agent.py', '/.speaker_profiles.json', '/requirements.txt'):
            response = await self.client.get(path)
            self.assertEqual(response.status, 404, path)
        response = await self.client.get('/health', headers={'Host': 'attacker.invalid'})
        self.assertEqual(response.status, 403)

    async def test_websocket_requires_exact_same_origin(self):
        for headers in ({}, {'Origin': 'https://attacker.invalid'}):
            with self.assertRaises(WSServerHandshakeError) as error:
                await self.client.ws_connect('/ws', headers=headers)
            self.assertEqual(error.exception.status, 403)

    async def test_consent_is_required_before_any_provider_start(self):
        with patch('dbs_web.DemoSession') as session:
            async with self.client.ws_connect('/ws', headers={'Origin': self.origin}) as ws:
                self.assertEqual((await ws.receive_json())['type'], 'hello')
                await ws.send_json({'type': 'start', 'language': 'en', 'consent': False})
                event = await ws.receive_json()
                self.assertEqual(event['code'], 'invalid_start')
                self.assertTrue(event['fatal'])
            session.assert_not_called()

    async def test_browser_bad_json_closes_without_starting_provider(self):
        with patch('dbs_web.DemoSession') as session:
            async with self.client.ws_connect('/dbs/ws', headers={'Origin': self.origin}) as ws:
                await ws.receive_json()
                await ws.send_str('not-json')
                await ws.receive()
            session.assert_not_called()

    async def test_prefix_redirect(self):
        response = await self.client.get('/dbs', allow_redirects=False)
        self.assertEqual(response.status, 308)
        self.assertEqual(response.headers['Location'], '/dbs/')


if __name__ == '__main__':
    unittest.main()
