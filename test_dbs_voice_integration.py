"""Migration contract checks with injected decisions and synthetic audio only."""
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from dbs_english import ENGLISH_OPENING, EnglishFlow, EnglishSession
from dbs_facilitator import Decision, _context
from dbs_mixed_turns import TurnLedger
from test_dbs_english import lesson
from test_dbs_mixed import turn


class IdentityIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.ledger = TurnLedger()
        self.flow = EnglishFlow(lesson(), self.ledger)
        self.ledger.accept(turn())
        self.flow.order = 0

    def test_registration_is_silent_even_if_model_supplies_acknowledgement(self):
        self.assertEqual(self.flow.apply(Decision('introduce', name='Josh', speech='Thanks, Josh.'), speaker='A'), [])
        self.assertEqual(self.flow.reported_names, ['Josh'])
        self.assertEqual(self.flow.roster, [])

    def test_last_self_introduction_is_recorded_before_readiness_advances(self):
        prompts = self.flow.apply(Decision('finish_enrollment', name='Josh'), speaker='A')
        self.assertEqual(self.flow.reported_names, ['Josh'])
        self.assertEqual([p.key for p in prompts], ['f.002'])
        self.assertEqual(self.flow.roster, [])

    def test_uncertain_name_can_be_clarified_without_speechmatics_identifiers(self):
        prompts = self.flow.apply(Decision('clarify_name', name='Josh', speech='Josh, did I catch that right?'), speaker='A', solo=False)
        self.assertEqual(self.flow.generated('Josh, did I catch that right?'), prompts)
        self.assertEqual(self.flow.pending.name, 'Josh')
        self.flow.sync_names()
        self.assertEqual(self.flow.pending.name, 'Josh')
        self.assertEqual(self.flow.apply(Decision('finish_enrollment')), [])
        self.assertEqual(self.flow.question_key, 'f.001')
        self.flow.apply(Decision('introduce', name='Josh'), speaker='A', solo=False)
        self.assertIsNone(self.flow.pending)
        self.assertEqual(self.flow.reported_names, ['Josh'])
        self.assertFalse(self.ledger.names)

    def test_other_person_can_introduce_without_erasing_uncertain_name(self):
        self.flow.apply(Decision('clarify_name', name='Josh', speech='Josh?'), speaker='A', solo=False)
        self.flow.apply(Decision('introduce', name='Kami'), speaker='B', solo=False)
        self.assertEqual(self.flow.reported_names, ['Kami'])
        self.assertEqual(self.flow.pending.name, 'Josh')

    def test_unassigned_clarification_cannot_lock_out_later_clear_introduction(self):
        self.flow.apply(Decision('clarify_name', name='Josh', speech='Josh?'), speaker='PENDING', solo=False)
        self.flow.apply(Decision('introduce', name='Josh'), speaker='A', solo=False)
        self.assertEqual(self.flow.reported_names, ['Josh'])
        self.assertFalse(self.flow.name_uncertain)
        self.assertEqual([p.key for p in self.flow.apply(Decision('finish_enrollment'))], ['f.002'])
        self.assertFalse(self.ledger.names)

    def test_revision_revokes_uncertain_name_owner(self):
        self.flow.apply(Decision('clarify_name', name='Josh', speech='Josh?'), speaker='A', solo=False)
        self.ledger.accept({'type': 'SpeakerRevision', 'revisions': [{'turn_order': 0, 'speaker_label': 'B'}]})
        self.flow.sync_names()
        self.assertIsNone(self.flow.pending)
        self.assertFalse(self.flow.name_uncertain)

    def test_pending_and_overlapping_turns_cannot_bind_a_name(self):
        self.flow.apply(Decision('introduce', name='Josh'), speaker='PENDING', solo=False)
        self.flow.apply(Decision('confirm_name'), speaker='A', solo=False)
        self.assertFalse(self.ledger.names)
        self.assertFalse(self.flow.roster)


class RuntimeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def make_session(self, facilitator=None):
        facilitator = facilitator or Mock(provider='fixture', model='injected', decide=AsyncMock(return_value=Decision('listen')), close=AsyncMock())
        provider = Mock(ready=True, send_audio=AsyncMock())
        ws = Mock(closed=False, send_json=AsyncMock(), send_bytes=AsyncMock(), close=AsyncMock())
        prepared = (lesson(), {'version_id': 111})
        session = EnglishSession(ws, provider=provider, prepared=prepared, facilitator=facilitator)
        session.speak = AsyncMock()
        return session

    async def test_configured_provider_is_default_without_native_broker(self):
        fake = Mock(provider='configured', model='fixture')
        with patch('dbs_english.Facilitator', return_value=fake) as factory:
            session = EnglishSession(Mock(), provider=Mock(), prepared=(lesson(), {}))
        factory.assert_called_once()
        self.assertIs(session.controller.facilitator, fake)

    async def test_exact_opening_never_requires_model_call_or_native_queue(self):
        session = self.make_session()
        session.controller.lesson.questions['f.001'] = ENGLISH_OPENING
        await session.process('opening')
        session.controller.facilitator.decide.assert_not_awaited()
        self.assertEqual([p.key for p in session.speak.call_args.args[0]], ['f.001'])
        self.assertEqual(session.controller.history[-1]['text'], ENGLISH_OPENING)

    async def test_idle_is_injected_once_per_lull_and_never_advances(self):
        session = self.make_session()
        await session.process('opening')
        session.last_activity = time.monotonic() - 8
        await session.process('idle')
        await session.process('idle')
        self.assertEqual(session.controller.facilitator.decide.await_count, 1)
        self.assertEqual(session.controller.facilitator.decide.call_args.kwargs['event'], 'idle')
        self.assertEqual(session.controller.flow.question_key, 'f.001')

    async def test_partial_speech_and_pause_prevent_idle_and_playback_echo_is_ignored(self):
        session = self.make_session()
        await session.process('opening')
        partial = turn()
        partial['end_of_turn'] = False
        session.last_activity = time.monotonic() - 8
        await session.on_provider(partial)
        self.assertTrue(session.human_speaking)
        await session.process('idle')
        session.controller.facilitator.decide.assert_not_awaited()
        session.controller.flow.paused = True
        session.human_speaking = False
        session.last_activity = time.monotonic() - 8
        await session.process('idle')
        session.controller.facilitator.decide.assert_not_awaited()
        session.busy = True
        session.opening_pending = False
        await session.on_provider(turn(order=1))
        self.assertTrue(session.queue.empty())

    async def test_current_final_while_paused_clears_speech_without_fresh_decision(self):
        session = self.make_session()
        await session.process('opening')
        partial = turn()
        partial['end_of_turn'] = False
        await session.on_provider(partial)
        session.controller.flow.paused = True
        await session.on_provider(turn())
        self.assertFalse(session.human_speaking)
        self.assertTrue(session.queue.empty())
        session.controller.flow.paused = False
        session.last_activity = time.monotonic() - 8
        await session.process('idle')
        session.controller.facilitator.decide.assert_awaited_once()

    async def test_late_partial_cannot_suppress_idle_after_a_final(self):
        session = self.make_session()
        await session.process('opening')
        await session.on_provider(turn())
        session.queue.get_nowait()
        late = turn()
        late['end_of_turn'] = False
        await session.on_provider(late)
        self.assertFalse(session.human_speaking)
        session.last_activity = time.monotonic() - 8
        await session.process('idle')
        session.controller.facilitator.decide.assert_awaited_once()

    async def test_older_duplicate_final_does_not_clear_current_speech(self):
        session = self.make_session()
        await session.process('opening')
        await session.on_provider(turn())
        session.queue.get_nowait()
        newer = turn(order=1)
        newer['end_of_turn'] = False
        await session.on_provider(newer)
        await session.on_provider(turn())
        self.assertTrue(session.human_speaking)
        session.last_activity = time.monotonic() - 8
        await session.process('idle')
        session.controller.facilitator.decide.assert_not_awaited()

    async def test_identity_metadata_survives_provider_allowlist_without_false_attribution(self):
        session = self.make_session()
        session.controller.flow.reported_names = ['Josh']
        context = _context(session.controller._context('A', False))
        self.assertEqual(context['identity_mode'], 'revisable_labels')
        self.assertEqual(context['reported_names_unverified'], ['Josh'])
        self.assertEqual(context['speaker'], 'PENDING')
        self.assertEqual(context['roster'], [])
        self.assertFalse(context['human_identity_verified'])
        self.assertFalse(context['voice_enrollment_ready'])

    async def test_stop_closes_injected_facilitator_and_shared_stream(self):
        session = self.make_session()
        async def run(callback, closed, **kwargs):
            await callback({'type':'Begin'})
            closed.set()
        session.provider.run = AsyncMock(side_effect=run)
        await session.run()
        session.provider.run.assert_awaited_once()
        session.controller.facilitator.close.assert_awaited_once()
        self.assertFalse(session.ledger.turns)
