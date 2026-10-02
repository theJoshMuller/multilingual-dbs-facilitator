"""Offline runtime wiring: model and speech providers are never contacted."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

from livekit import rtc

from dbs_agent import DiscoveryAgent, build_controller
from dbs_facilitator import Decision
from dbs_flow import Intent
from dbs_web import DemoSession


class Brain:
    provider = 'openrouter'
    model = 'offline/fixture'

    def __init__(self):
        self.decisions = []
        self.calls = []
        self.started = asyncio.Event()
        self.hold = None
        self.closed = False

    async def decide(self, text, context, *, source='voice', event='participant'):
        self.calls.append((text, context, source, event))
        self.started.set()
        if self.hold:
            await self.hold.wait()
        return self.decisions.pop(0) if self.decisions else Decision('listen')

    async def close(self):
        self.closed = True


class WiringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.brain = Brain()
        self.env = patch.dict(os.environ, {'DBS_FACILITATION_MODE': 'generative'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.factory = patch('dbs_agent.Facilitator', return_value=self.brain, create=True)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.ws = Mock(closed=False, send_json=AsyncMock(), send_bytes=AsyncMock())

    def browser(self):
        session = DemoSession(self.ws, 'en')
        self.assertEqual(getattr(session.controller, 'provider', 'rules'), 'openrouter')
        session.speak = AsyncMock()
        session.recognizer = AsyncMock()
        return session

    async def test_browser_defaults_to_generative_controller(self):
        os.environ.pop('DBS_FACILITATION_MODE')
        session = self.browser()
        self.assertEqual(session.state()['parser'], 'openrouter')
        self.assertEqual(session.state()['model'], 'offline/fixture')

    async def test_console_builder_selects_generative_and_text_rehearsal(self):
        _, controller = await build_controller(text_mode=True)
        self.assertEqual(getattr(controller, 'provider', 'rules'), 'openrouter')
        self.assertTrue(controller.flow.text_mode)
        await controller.close()
        self.assertTrue(self.brain.closed)

    async def test_opening_diagnostics_report_model_and_speech_source(self):
        session = self.browser()
        self.brain.decisions.append(Decision('respond', speech='Welcome. Who would like to start?'))
        await session.process('opening')
        events = [c.args[0] for c in self.ws.send_json.call_args_list if c.args[0]['type'] == 'decision']
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['action'], 'respond')
        self.assertEqual(events[0]['source'], 'opening')
        self.assertEqual(events[0]['parser'], 'openrouter')
        self.assertEqual(events[0]['model'], 'offline/fixture')
        self.assertEqual(events[0]['prompt_origins'], ['generated'])

    async def test_participant_response_reaches_speech_unchanged(self):
        session = self.browser()
        self.brain.decisions.append(Decision('respond', speech='Take a moment; we have room to listen.'))
        await session.process((Intent.DISCUSSION, '', '[Speaker S1] Could we slow down?', 'voice'))
        prompts = session.speak.call_args.args[0]
        self.assertEqual([session.controller.render(p) for p in prompts], ['Take a moment; we have room to listen.'])
        events = [c.args[0] for c in self.ws.send_json.call_args_list if c.args[0]['type'] == 'decision']
        self.assertEqual(events[-1]['action'], 'respond')
        self.assertEqual(events[-1]['prompt_origins'], ['generated'])
        self.assertEqual(self.brain.calls[-1][1]['speaker'], 'S1')

    async def test_natural_introduction_collects_actual_voice_evidence(self):
        session = self.browser()
        session.durations['S1'] = 8
        session.recognizer.get_speaker_ids.return_value = [{'label': 'S1', 'speaker_identifiers': ['private-test-evidence']}]
        self.brain.decisions.append(Decision('introduce', name='Anna'))
        await session.process((Intent.DISCUSSION, '', '[Speaker S1] Anna here. I am thankful for my family.', 'voice'))
        self.assertEqual(session.controller.flow.phase, 'introductions')
        self.assertIsNone(session.controller.flow.pending)
        self.assertEqual([p.name for p in session.controller.flow.roster], ['Anna'])
        self.assertEqual(session.controller.flow.roster[0].identifiers, ('private-test-evidence',))
        self.assertNotIn('private-test-evidence', str(session.state()))

    async def test_clear_introductions_stay_silent_until_seven_second_nudge(self):
        session = self.browser()
        session.started = session.last_input = 100
        session.speak = DemoSession.speak.__get__(session)
        with patch('dbs_web.synthesize', new=AsyncMock(side_effect=AssertionError('Unexpected introduction speech'))) as synthesize:
            for now, name, speaker in ((100, 'Anna', 'S1'), (104, 'Ben', 'S2')):
                session.durations[speaker] = 8
                session.recognizer.get_speaker_ids.return_value = [
                    {'label': speaker, 'speaker_identifiers': [f'evidence-{speaker}']}]
                self.brain.decisions.append(Decision(
                    'introduce', name=name, speech=f'Thanks, {name}. Anyone else?',
                    note='Acknowledged sharing and invited others.'))
                with patch('dbs_web.time.monotonic', return_value=now):
                    await session.process((Intent.DISCUSSION, '',
                        f'[Speaker {speaker}] I am {name}, thankful for my family.', 'voice'))
                synthesize.assert_not_awaited()
                self.assertFalse(session.busy)
                self.assertFalse(session.closed.is_set())
            synthesize.assert_not_awaited()
        self.assertEqual([p.name for p in session.controller.flow.roster], ['Anna', 'Ben'])
        self.assertTrue(all(turn['role'] == 'user' for turn in session.controller.history))
        decisions = [c.args[0] for c in self.ws.send_json.call_args_list
                     if c.args[0]['type'] == 'decision']
        self.assertEqual(len(decisions), 2)
        for decision in decisions:
            self.assertEqual(decision['prompts'], [])
            self.assertEqual(decision['prompt_origins'], [])
            self.assertIn('silent', decision['reason'])
        with patch('dbs_web.time.monotonic', return_value=110.9):
            await self.one_timer_cycle(session.tick(), 'dbs_web')
        self.assertTrue(session.queue.empty(), 'The second participant resets the silence timer')
        with patch('dbs_web.time.monotonic', return_value=111):
            await self.one_timer_cycle(session.tick(), 'dbs_web')
            self.assertEqual(session.queue.get_nowait(), 'idle')
            session.queue.task_done()
            self.brain.decisions.append(Decision('respond', speech='Anyone else ready to share?'))
            session.speak = AsyncMock()
            await session.process('idle')
        self.assertEqual(self.brain.calls[-1][3], 'idle')
        prompts = session.speak.call_args.args[0]
        self.assertEqual([session.controller.render(p) for p in prompts], ['Anyone else ready to share?'])
        self.assertEqual([session.controller.prompt_origin(p) for p in prompts], ['generated'])
        self.assertEqual(session.controller.flow.phase, 'introductions')

    async def test_button_next_and_previous_are_direct_and_skip_model(self):
        session = self.browser()
        session.busy = False
        session.controller.flow.phase = 'lesson'
        session.controller.flow.index = 1
        await session.handle_control('next')
        await session.process(session.queue.get_nowait())
        self.assertEqual(session.controller.flow.question_key, 'f.003')
        await session.handle_control('previous')
        await session.process(session.queue.get_nowait())
        self.assertEqual(session.controller.flow.question_key, 'f.002')
        self.assertEqual(self.brain.calls, [])

    async def test_procedural_reply_preserves_canonical_question_context(self):
        session = self.browser()
        session.controller.flow.phase = 'lesson'
        session.controller.flow.index = 1
        self.brain.decisions.append(Decision('respond', speech='Anyone can share or pass.'))
        await session.process((Intent.QUESTION, '', '[Speaker S2] William, may I pass?', 'voice'))
        state = session.state()
        self.assertEqual(state['question_key'], 'f.002')
        self.assertEqual(state['current_question'], session.controller.lesson.questions['f.002'])

    async def test_failed_playback_restores_conversation_history(self):
        session = self.browser()
        self.brain.decisions.append(Decision('respond', speech='A generated reply.'))
        session.speak = AsyncMock(side_effect=RuntimeError('offline playback failure'))
        await session.process((Intent.DISCUSSION, '', '[Speaker S1] A personal contribution.', 'voice'))
        self.assertEqual(session.controller.history, [])
        self.assertEqual(session.controller.flow.phase, 'introductions')
        self.assertTrue(session.closed.is_set())

    async def test_pause_cancels_pending_model_request_without_advancing(self):
        session = self.browser()
        self.brain.hold = asyncio.Event()
        session.active = asyncio.create_task(session.process((Intent.DISCUSSION, '', '[Speaker S1] Let us continue.', 'voice')))
        await asyncio.wait_for(self.brain.started.wait(), 1)
        await asyncio.wait_for(session.handle_control('pause'), 1)
        self.assertTrue(session.active.cancelled())
        await session.process(session.queue.get_nowait())
        self.assertTrue(session.controller.flow.paused)
        self.assertEqual(session.controller.flow.index, 0)
        self.assertEqual(len(self.brain.calls), 1)

    async def test_final_closing_waits_for_audio_acknowledgement(self):
        session = self.browser()
        session.speak = DemoSession.speak.__get__(session)
        session.controller.flow.phase = 'lesson'
        session.controller.flow.index = len(session.controller.flow.steps) - 2
        self.brain.decisions.append(Decision('next'))
        frames = [rtc.AudioFrame(data=bytes(640), sample_rate=16000, num_channels=1, samples_per_channel=320)]
        with patch('dbs_web.synthesize', new=AsyncMock(return_value=frames)):
            task = asyncio.create_task(session.process((Intent.DISCUSSION, '', '[Speaker S1] Let us finish.', 'voice')))
            try:
                for _ in range(200):
                    if session.played:
                        break
                    await asyncio.sleep(0)
                self.assertIsNotNone(session.played)
                self.assertEqual(session.controller.flow.phase, 'closing')
                session.played.set_result(True)
                await asyncio.wait_for(task, 1)
                self.assertEqual(session.controller.flow.phase, 'done')
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_stt_shutdown_failure_still_clears_context_and_closes_brain(self):
        session = self.browser()
        session.controller.history.append({'role': 'user', 'text': 'private-test-contribution'})
        self.ws.close = AsyncMock()
        recognizer = Mock(aclose=AsyncMock(side_effect=RuntimeError('offline cleanup failure')))
        recognizer.stream.side_effect = RuntimeError('offline stream startup failure')
        with patch('dbs_web.make_stt', return_value=recognizer):
            await session.run()
        self.assertTrue(self.brain.closed)
        self.assertEqual(session.controller.history, [])
        self.ws.close.assert_awaited_once()

    async def test_console_calls_generated_start_and_closes_brain(self):
        _, controller = await build_controller(text_mode=True)
        self.assertEqual(getattr(controller, 'provider', 'rules'), 'openrouter')
        self.brain.decisions.append(Decision('respond', speech='Welcome. Tell us your name and something you are thankful for.'))
        agent = DiscoveryAgent(controller, AsyncMock(), {'tts': 'elevenlabs', 'language': 'en'})
        with patch.object(DiscoveryAgent, 'session', new_callable=PropertyMock, return_value=Mock()), patch.object(agent, '_say', new=AsyncMock()) as say:
            await agent.on_enter()
            self.assertEqual(controller.render(say.call_args.args[0][0]), 'Welcome. Tell us your name and something you are thankful for.')
            self.assertEqual(self.brain.calls[0][3], 'opening')
            await agent.on_exit()
        self.assertTrue(self.brain.closed)

    async def one_timer_cycle(self, coroutine, module):
        calls = 0
        async def sleep(_seconds):
            nonlocal calls
            calls += 1
            if calls > 1:
                raise asyncio.CancelledError()
        with patch(module + '.asyncio.sleep', new=sleep), self.assertRaises(asyncio.CancelledError):
            await coroutine

    async def test_browser_intro_nudge_after_seven_seconds_once(self):
        session = self.browser()
        session.busy = False
        session.started = session.last_input = 100
        with patch('dbs_web.time.monotonic', return_value=100):
            session.last_activity = 93.1
            await self.one_timer_cycle(session.tick(), 'dbs_web')
            self.assertTrue(session.queue.empty())
            session.last_activity = 93
            await self.one_timer_cycle(session.tick(), 'dbs_web')
            self.assertEqual(session.queue.qsize(), 1, 'Nudge must be queued at seven seconds')
            self.assertEqual(session.queue.get_nowait(), 'idle')
            session.queue.task_done()
            self.brain.decisions.append(Decision('respond', speech='Anyone else? Let me know when everyone has shared.'))
            await session.process('idle')
            self.assertEqual(self.brain.calls[-1][3], 'idle')
            self.assertEqual(session.controller.flow.question_key, 'f.001')
            session.last_activity = 90
            await self.one_timer_cycle(session.tick(), 'dbs_web')
            self.assertTrue(session.queue.empty())

    async def test_browser_nudge_waits_for_silence_and_discards_stale_work(self):
        for blocked in ('busy', 'human_speaking', 'paused', 'queued'):
            with self.subTest(blocked=blocked):
                session = self.browser()
                session.busy = blocked == 'busy'
                session.human_speaking = blocked == 'human_speaking'
                session.controller.flow.paused = blocked == 'paused'
                session.started = session.last_input = 100
                session.last_activity = 90
                if blocked == 'queued':
                    session.queue.put_nowait('opening')
                with patch('dbs_web.time.monotonic', return_value=100):
                    await self.one_timer_cycle(session.tick(), 'dbs_web')
                self.assertEqual(session.queue.qsize(), 1 if blocked == 'queued' else 0)
        session = self.browser()
        session.last_activity = 100
        with patch('dbs_web.time.monotonic', return_value=100):
            await session.process('idle')
        self.assertEqual(self.brain.calls, [])

    async def test_console_intro_nudge_uses_same_seven_second_boundary(self):
        session = self.browser()
        agent = DiscoveryAgent(session.controller, AsyncMock(), {'tts': 'elevenlabs', 'language': 'en'})
        with patch('dbs_agent.time.monotonic', return_value=100), patch.object(agent, '_say', new=AsyncMock()):
            agent.last_activity = 93.1
            await self.one_timer_cycle(agent._idle(), 'dbs_agent')
            self.assertTrue(agent.queue.empty())
            agent.last_activity = 93
            await self.one_timer_cycle(agent._idle(), 'dbs_agent')
            self.assertEqual(agent.queue.qsize(), 1, 'Console nudge must be queued at seven seconds')
            self.assertIsNone(agent.queue.get_nowait())
            agent.queue.task_done()
            self.brain.decisions.append(Decision('respond', speech='Anyone else?'))
            await agent._process(None)
        self.assertEqual(self.brain.calls[-1][3], 'idle')
        self.assertEqual(session.controller.flow.question_key, 'f.001')

    async def test_browser_last_person_registers_and_starts_next_question(self):
        session = self.browser()
        session.durations['S1'] = 8
        session.recognizer.get_speaker_ids.return_value = [{'label': 'S1', 'speaker_identifiers': ['private-fixture']}]
        self.brain.decisions.append(Decision('finish_enrollment', name='Josh'))
        await session.process((Intent.DISCUSSION, '', '[Speaker S1] My name is Josh and I am thankful for today. That is everyone; next question.', 'voice'))
        self.assertEqual([p.name for p in session.controller.flow.roster], ['Josh'])
        self.assertEqual(session.controller.flow.question_key, 'f.002')
        self.assertIsNone(session.controller.flow.pending)

    async def test_final_introduction_playback_failure_restores_roster_and_question(self):
        session = self.browser()
        snapshot = session.controller.snapshot()
        session.durations['S1'] = 8
        session.recognizer.get_speaker_ids.return_value = [{'label': 'S1', 'speaker_identifiers': ['private-fixture']}]
        self.brain.decisions.append(Decision('finish_enrollment', name='Josh'))
        session.speak.side_effect = RuntimeError('offline playback failure')
        await session.process((Intent.DISCUSSION, '', '[Speaker S1] My name is Josh, thankful for today; everyone has shared, next question.', 'voice'))
        self.assertEqual(session.controller.snapshot(), snapshot)
        self.assertTrue(session.closed.is_set())

    async def test_rules_browser_retains_twenty_five_second_nudge(self):
        with patch.dict(os.environ, {'DBS_FACILITATION_MODE': 'rules'}):
            session = DemoSession(self.ws, 'en')
        session.busy = False
        session.controller.flow.phase = 'lesson'
        session.started = session.last_input = 100
        with patch('dbs_web.time.monotonic', return_value=100):
            session.last_activity = 93
            await self.one_timer_cycle(session.tick(), 'dbs_web')
            self.assertTrue(session.queue.empty())
            session.last_activity = 75
            await self.one_timer_cycle(session.tick(), 'dbs_web')
            self.assertEqual(session.queue.get_nowait(), 'idle')

    async def test_console_nudge_timer_is_suppressed_while_paused(self):
        session = self.browser()
        session.controller.control('pause')
        agent = DiscoveryAgent(session.controller, AsyncMock(), {'tts': 'elevenlabs', 'language': 'en'})
        agent.last_activity = 90
        with patch('dbs_agent.time.monotonic', return_value=100):
            await self.one_timer_cycle(agent._idle(), 'dbs_agent')
        self.assertTrue(agent.queue.empty(), 'Pause must suppress the console silence timer')
        self.assertEqual(self.brain.calls, [])


if __name__ == '__main__':
    unittest.main()
