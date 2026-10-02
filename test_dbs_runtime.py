"""Offline regression tests for playback, cancellation and provider failures."""
import asyncio
import os
import time
import unittest
from unittest.mock import AsyncMock, Mock, PropertyMock, patch

from livekit.agents import Agent, stt

from dbs_agent import DBSController, DiscoveryAgent
from dbs_curriculum import load_lesson
from dbs_flow import Intent, Prompt
from dbs_intents import IntentParser, rule_intent
from dbs_prompts import EN


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        with patch.dict(os.environ, {'DBS_LLM_PROVIDER': 'rules'}):
            self.parser = IntentParser()
        self.controller = DBSController({'people': 7}, load_lesson(), self.parser, EN, text_mode=True)
        self.agent = DiscoveryAgent(self.controller, AsyncMock(), {'tts': 'elevenlabs', 'language': 'en'})
        self.session = Mock()
        self.session_patch = patch.object(DiscoveryAgent, 'session', new_callable=PropertyMock, return_value=self.session)
        self.session_patch.start()
        self.workers = []

    async def asyncTearDown(self):
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)
        self.session_patch.stop()
        await self.parser.close()

    def consume(self):
        worker = asyncio.create_task(self.agent._consume())
        self.workers.append(worker)
        return worker

    async def test_tts_failure_rolls_back_and_shuts_down_if_error_speech_fails(self):
        worker = self.consume()
        with patch.object(self.agent, '_say', new=AsyncMock(side_effect=RuntimeError('offline'))):
            self.agent.queue.put_nowait('My name is Alex.')
            with self.assertRaises(RuntimeError):
                await asyncio.wait_for(asyncio.shield(worker), 1)
        self.assertEqual(self.controller.flow.phase, 'introductions')
        self.assertIsNone(self.controller.flow.pending)
        self.session.shutdown.assert_called_once_with(drain=False)

    async def test_stop_cancels_slow_parser_without_waiting_for_model(self):
        started = asyncio.Event()
        original = self.parser.classify

        async def slow(text, phase):
            if text == 'My name is Alex.':
                started.set()
                await asyncio.Event().wait()
            return await original(text, phase)

        worker = self.consume()
        with patch.object(self.parser, 'classify', side_effect=slow), patch.object(self.agent, '_say', new=AsyncMock()):
            self.agent.queue.put_nowait('My name is Alex.')
            await asyncio.wait_for(started.wait(), 1)
            self.agent.queue.put_nowait('Yes.')
            self.agent._preempt('[Speaker S1] William, stop.')
            await asyncio.wait_for(asyncio.shield(worker), 1)
        self.assertEqual(self.controller.flow.phase, 'done')
        self.assertIsNone(self.controller.flow.pending)
        self.assertTrue(self.agent.queue.empty())
        self.session.shutdown.assert_called_once()

    async def test_final_stt_pause_interrupts_playback_and_resume_replays_batch(self):
        started = asyncio.Event()
        handle = Mock()

        async def held_playout():
            started.set()
            await asyncio.Event().wait()

        handle.wait_for_playout = AsyncMock(side_effect=held_playout)
        self.session.say.return_value = handle
        self.controller.flow.phase = 'lesson'
        self.controller.flow.index = 5  # retelling, following the current scripture batch
        batch = [Prompt('scripture'), Prompt('a.001')]
        with patch('dbs_agent.speech_frames', new=AsyncMock(return_value=[object()])):
            self.agent.active_turn = asyncio.create_task(self.agent._say(batch))
            task = self.agent.active_turn
            await asyncio.wait_for(started.wait(), 1)

            async def events(*args):
                yield stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[
                    stt.SpeechData(language='en', text='[Speaker S1] William, pause.', speaker_id='S1')
                ])

            with patch.object(Agent.default, 'stt_node', new=events):
                emitted = [event async for event in self.agent.stt_node(None, None)]
            self.assertEqual(emitted, [])
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertEqual(self.session.say.call_count, 1)  # retelling did not play after cancellation
            handle.interrupt.assert_called_with(force=True)

        pause = self.agent.queue.get_nowait()
        self.agent.queue.task_done()
        with patch.object(self.agent, '_say', new=AsyncMock()) as say:
            await self.agent._process(pause)
            self.assertTrue(self.controller.flow.paused)
            await self.agent._process('William, resume.')
            assert say.await_args is not None
            self.assertEqual([p.key for p in say.await_args.args[0]], ['resumed', 'scripture', 'a.001'])
        self.assertFalse(self.controller.flow.paused)

    async def test_idle_and_normal_output_never_overlap(self):
        started = asyncio.Event()
        release = asyncio.Event()
        self.controller.flow.phase = 'lesson'
        self.controller.flow.index = 0
        self.agent.last_activity = time.monotonic() - 26
        calls = []
        active = 0
        maximum = 0

        async def say(prompts):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            calls.append([p.key for p in prompts])
            if prompts and prompts[0].key == 'invite':
                started.set()
                await release.wait()
            active -= 1

        self.consume()
        with patch.object(self.agent, '_say', side_effect=say):
            self.agent.queue.put_nowait(None)
            await asyncio.wait_for(started.wait(), 1)
            self.agent.queue.put_nowait('William, next question.')
            await asyncio.sleep(0)
            self.assertEqual(len(calls), 1)
            release.set()
            await asyncio.wait_for(self.agent.queue.join(), 1)
        self.assertEqual(maximum, 1)
        self.assertEqual(calls, [['invite'], ['confirm_next']])

    async def test_stale_idle_is_discarded(self):
        with patch.object(self.agent, '_say', new=AsyncMock()) as say:
            await self.agent._process(None)
        say.assert_not_awaited()

    async def test_safety_controls_do_not_require_remote_model(self):
        self.parser.provider = 'ollama'
        with patch.object(self.parser, 'request', new=AsyncMock(side_effect=AssertionError('model called'))):
            for text in ('I am going to hurt myself.', 'Estoy en peligro inmediato.'):
                self.assertEqual(rule_intent(text, 'lesson')[0], Intent.SAFETY)
                self.assertEqual((await self.parser.classify(text, 'lesson'))[0], Intent.SAFETY)
            self.assertEqual((await self.parser.classify('William, stop.', 'lesson'))[0], Intent.STOP)


if __name__ == '__main__':
    unittest.main()
