"""Synthetic offline source and controller checks; no microphone accuracy claim."""
import unittest
from unittest.mock import patch

from dbs_curriculum import Lesson
from dbs_english import (
    ENGLISH_OPENING,
    EnglishFlow,
    build_english_lesson,
    question_lesson,
)
from dbs_facilitator import Decision
from dbs_mixed_turns import TurnLedger
from dbs_youversion import QUESTIONS, ContentUnavailable
from test_dbs_mixed import turn


def lesson():
    return Lesson('eng', 'English', ['f.001', 'f.002', 'f.003', 'f.008', 'scripture', *[f'a.{i:03}' for i in range(1, 8)]], {q: 'Full original synthetic question ' + q for q in QUESTIONS}, [{'verseId': 'GEN.1.1', 'text': 'Synthetic source fixture'}], 'NIV', 'Synthetic copyright')


class EnglishTests(unittest.TestCase):
    def test_josh_requested_opening_is_exact_and_repeatable(self):
        with patch("dbs_english.canonical_questions", return_value=lesson().questions.copy()):
            selected = question_lesson()
        self.assertEqual(selected.questions["f.001"], ENGLISH_OPENING)
        self.assertIn("When the last person has shared", ENGLISH_OPENING)
        self.assertEqual(selected.questions["f.002"], lesson().questions["f.002"])

    def test_names_need_two_high_confidence_same_voice_turns(self):
        ledger = TurnLedger()
        flow = EnglishFlow(lesson(), ledger)
        ledger.accept(turn())
        flow.order = 0
        flow.apply(Decision('introduce', name='Test Person'), speaker='A')
        self.assertEqual(flow.roster, [])
        ledger.accept(turn(order=1))
        flow.order = 1
        flow.apply(Decision('confirm_name', name='Test Person'), speaker='A')
        self.assertEqual(flow.roster[0].name, 'Test Person')
        self.assertEqual(flow.roster[0].identifiers, ())
        ledger.accept({'type': 'SpeakerRevision', 'revisions': [{'turn_order': 0, 'speaker_label': 'B'}]})
        flow.sync_names()
        self.assertEqual(flow.roster, [])

    def test_uncertain_intro_never_blocks_study_or_becomes_identity(self):
        ledger = TurnLedger()
        flow = EnglishFlow(lesson(), ledger)
        ledger.accept(turn(label='PENDING'))
        flow.order = 0
        flow.apply(Decision('introduce', name='Test Person'), speaker='PENDING')
        self.assertFalse(flow.roster)
        flow.apply(Decision('finish_enrollment'))
        self.assertEqual(flow.question_key, 'f.002')

    def test_finish_introductions_cannot_skip_a_later_question(self):
        flow = EnglishFlow(lesson(), TurnLedger())
        flow.apply(Decision("finish_enrollment"))
        self.assertEqual(flow.question_key, "f.002")
        self.assertEqual(flow.apply(Decision("finish_enrollment")), [])
        self.assertEqual(flow.question_key, "f.002")

    def test_navigation_preserves_original_questions_and_passage(self):
        flow = EnglishFlow(lesson(), TurnLedger())
        flow.apply(Decision('next'))
        flow.apply(Decision('next'))
        prompts = flow.apply(Decision('next'))
        self.assertEqual([p.key for p in prompts], ['f.008', 'scripture', 'a.001'])
        self.assertEqual([p.key for p in flow.apply(Decision('previous'))], ['f.003'])
        self.assertEqual([p.key for p in flow.apply(Decision('repeat'))], ['f.003'])

    def test_source_loader_uses_only_exact_official_verses_and_attribution(self):
        questions = lesson().questions
        verses = [{'verseId': f'GEN.1.{i}', 'text': f'Synthetic exact verse {i}'} for i in range(1, 26)]
        source = {'verses': verses, 'version_id': 111, 'abbreviation': 'NIV', 'publisher': 'Test publisher', 'copyright': 'Test copyright'}
        with patch('dbs_english.canonical_questions', return_value=questions), patch('dbs_english.fetch_selected', return_value=source) as fetch:
            result, provenance = build_english_lesson()
        fetch.assert_called_once_with('en', include_verses=True)
        self.assertEqual(result.verses, verses)
        self.assertEqual(result.bible, 'NIV')
        self.assertIn('Test publisher', result.copyright)
        self.assertEqual(provenance['version_id'], 111)
        with patch('dbs_english.canonical_questions', side_effect=ContentUnavailable('Missing')), self.assertRaises(ContentUnavailable):
            build_english_lesson()


class EnglishSessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from unittest.mock import AsyncMock, Mock

        from dbs_english import EnglishSession
        from dbs_harness import HarnessBroker
        self.provider = Mock(ready=True, send_audio=AsyncMock())
        self.ws = Mock(closed=False, send_json=AsyncMock(), send_bytes=AsyncMock(), close=AsyncMock())
        with patch('dbs_english.question_lesson', return_value=lesson()):
            self.session = EnglishSession(self.ws, HarnessBroker('fixture'), provider=self.provider)

    async def test_start_asks_one_opening_with_no_extra_welcome(self):
        from unittest.mock import AsyncMock
        self.session.controller.lesson.questions["f.001"] = ENGLISH_OPENING
        self.session.speak = AsyncMock()
        await self.session.process("opening")
        prompts = self.session.speak.call_args.args[0]
        self.assertEqual([p.key for p in prompts], ["f.001"])
        self.assertEqual(self.session.controller.render(prompts[0]), ENGLISH_OPENING)

    async def test_connecting_controls_cannot_speak_but_stop_is_available(self):
        self.provider.ready = False
        await self.session.handle_control('pause')
        self.assertTrue(self.session.queue.empty())
        self.assertFalse(self.session.controller.flow.paused)
        self.assertFalse(self.session.closed.is_set())
        self.provider.ready = True
        await self.session.on_provider({'type': 'Begin'})
        self.assertEqual(self.session.queue.get_nowait(), 'opening')
        self.assertTrue(self.session.queue.empty())
        await self.session.handle_control('stop')
        self.assertTrue(self.session.closed.is_set())

    async def test_pause_before_queued_opening_defers_it_until_resume(self):
        from unittest.mock import AsyncMock
        self.session.provenance = {'version_id': 111}
        self.session.controller.lesson.questions['f.001'] = ENGLISH_OPENING
        self.session.speak = AsyncMock()
        await self.session.process({'control': 'pause'})
        self.session.speak.reset_mock()
        await self.session.process('opening')
        self.session.speak.assert_not_awaited()
        self.assertTrue(self.session.controller.flow.paused)
        await self.session.process({'control': 'resume'})
        prompts = self.session.speak.call_args.args[0]
        self.assertEqual([p.key for p in prompts], ['assistant', 'f.001'])
        self.assertEqual(self.session.controller.render(prompts[-1]), ENGLISH_OPENING)
        self.assertFalse(self.session.controller.flow.paused)

    async def test_pause_preemption_retains_an_opening_already_in_the_queue(self):
        from unittest.mock import AsyncMock
        self.session.provenance = {'version_id': 111}
        self.session.speak = AsyncMock()
        await self.session.on_provider({'type': 'Begin'})
        await self.session.handle_control('pause')
        await self.session.process(self.session.queue.get_nowait())
        self.assertTrue(self.session.queue.empty())
        await self.session.process({'control': 'resume'})
        self.assertEqual([p.key for p in self.session.speak.call_args.args[0]], ['assistant', 'f.001'])
        self.session.speak.reset_mock()
        await self.session.process('opening')
        self.session.speak.assert_not_awaited()

    async def test_pause_preemption_retains_opening_cancelled_before_selection(self):
        import asyncio
        from unittest.mock import AsyncMock
        self.session.provenance = {'version_id': 111}
        self.session.speak = AsyncMock()
        entered = asyncio.Event()
        async def blocked_start():
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.session.controller, 'start', side_effect=blocked_start):
            self.session.active = asyncio.create_task(self.session.process('opening'))
            await entered.wait()
            await self.session.handle_control('pause')
        await self.session.process(self.session.queue.get_nowait())
        await self.session.process({'control': 'resume'})
        self.assertEqual([p.key for p in self.session.speak.call_args.args[0]], ['assistant', 'f.001'])

    async def test_playback_audio_is_gated_and_revision_never_becomes_another_turn(self):
        from unittest.mock import AsyncMock
        self.session.speak = AsyncMock()
        await self.session.process('opening')
        self.session.busy = True
        data = b'\x01\x01' * 160
        await self.session.feed_audio(data)
        self.provider.send_audio.assert_called_with(data, playback=True)
        self.session.busy = False
        await self.session.feed_audio(data)
        self.provider.send_audio.assert_called_with(data, playback=False)
        await self.session.on_provider(turn())
        await self.session.on_provider(turn())
        self.assertEqual(self.session.queue.qsize(), 1)
        await self.session.on_provider({'type': 'SpeakerRevision', 'revisions': [{'turn_order': 0, 'speaker_label': 'B'}]})
        self.assertEqual(self.session.queue.qsize(), 1)
        self.session.controller.flow.paused = True
        self.assertFalse(self.session.state()['listen'])
        await self.session.feed_audio(data)
        self.provider.send_audio.assert_called_with(data, playback=True)
        await self.session.on_provider(turn(order=1))
        self.assertEqual(self.session.queue.qsize(), 1)

    async def test_pause_resume_preserves_selected_scripture_destination(self):
        import asyncio
        from unittest.mock import AsyncMock
        self.session.provenance = {'version_id': 111}
        self.session.speak = AsyncMock()
        await self.session.process('opening')
        self.session.controller.flow.index = 2
        self.session.controller.flow.phase = 'lesson'
        playback_started = asyncio.Event()
        async def blocked(prompts):
            self.session.remaining_prompts = prompts[:]
            playback_started.set()
            await asyncio.Event().wait()
        self.session.speak = blocked
        self.session.active = asyncio.create_task(self.session.process({'control': 'next'}))
        await playback_started.wait()
        await self.session.handle_control('pause')
        self.assertEqual(self.session.controller.flow.question_key, 'a.001')
        pause = self.session.queue.get_nowait()
        self.session.speak = AsyncMock()
        await self.session.process(pause)
        await self.session.process({'control': 'resume'})
        self.assertEqual(self.session.controller.flow.question_key, 'a.001')
        self.assertEqual([p.key for p in self.session.speak.call_args.args[0]], ['assistant', 'f.008', 'scripture', 'a.001'])

    async def test_question_waits_for_source_and_microphone_without_queue_reply(self):
        import asyncio
        import threading
        from unittest.mock import AsyncMock
        release_source = threading.Event()
        heard_opening = asyncio.Event()
        def delayed_source():
            release_source.wait(2)
            return lesson(), {'version_id': 111}
        async def play(prompts):
            if any(p.key == 'f.001' for p in prompts):
                heard_opening.set()
        self.session.speak = play
        async def provider_run(callback, closed, **kwargs):
            await callback({'type': 'Begin'})
            await heard_opening.wait()
            closed.set()
        self.provider.run = AsyncMock(side_effect=provider_run)
        with patch('dbs_english.build_english_lesson', side_effect=delayed_source):
            task = asyncio.create_task(self.session.run())
            try:
                await asyncio.sleep(.03)
                self.assertFalse(heard_opening.is_set())
                self.assertEqual(self.session.provenance, {})
                release_source.set()
                await asyncio.wait_for(heard_opening.wait(), 1)
                self.assertTrue(self.session.provider.ready)
                self.assertEqual(self.session.controller.facilitator.broker.jobs, {})
            finally:
                release_source.set()
                await asyncio.wait_for(task, 3)

    async def test_cleanup_stops_provider_and_clears_native_jobs(self):
        from unittest.mock import AsyncMock
        async def provider_run(callback, closed, **kwargs):
            await callback({'type': 'Begin'})
            closed.set()
        self.provider.run = AsyncMock(side_effect=provider_run)
        self.session.speak = AsyncMock()
        with patch('dbs_english.build_english_lesson', return_value=(lesson(), {'version_id': 111})):
            await self.session.run()
        self.assertTrue(self.session.closed.is_set())
        self.assertEqual(self.session.ledger.turns, {})
        self.assertEqual(self.session.controller.facilitator.broker.jobs, {})
