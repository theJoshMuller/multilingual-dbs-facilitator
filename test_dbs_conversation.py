import unittest

from dbs_conversation import ConversationFlow
from dbs_facilitator import Decision
from test_dbs_facilitator import lesson


class ConversationTests(unittest.TestCase):
    def test_voice_evidence_required_before_pending(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('introduce', name='Ana'), speaker='S1')
        self.assertIsNone(flow.pending)
        flow.apply(Decision('introduce', name='Ana'), speaker='UU', identifiers=('opaque',))
        self.assertIsNone(flow.pending)

    def test_text_rehearsal_bypasses_voice_only(self):
        flow = ConversationFlow(lesson(), text_mode=True)
        flow.apply(Decision('introduce', name='Ana'), speaker='text-1')
        flow.apply(Decision('confirm_name'), speaker='text-1')
        self.assertEqual(flow.roster[0].name, 'Ana')
        self.assertEqual(flow.roster[0].identifiers, ())

    def test_manual_passage_waits_for_explicit_next(self):
        flow = ConversationFlow(lesson('es', False))
        prompts = flow.apply(Decision('next'))
        self.assertEqual([p.key for p in prompts], ['scripture'])
        self.assertEqual(flow.question_key, 'scripture')
        self.assertEqual(flow.apply(Decision('listen')), [])
        self.assertEqual([p.key for p in flow.apply(Decision('next'))], ['a.001'])

    def test_bounds_and_closing_completion(self):
        flow = ConversationFlow(lesson())
        self.assertEqual(flow.apply(Decision('previous'))[0].key, 'f.001')
        flow.apply(Decision('next'))
        self.assertEqual(flow.apply(Decision('next'))[-1].key, 'a.002')
        self.assertEqual(flow.phase, 'closing')
        self.assertEqual(flow.apply(Decision('next'))[-1].key, 'a.002')
        self.assertEqual(flow.index, len(flow.steps) - 1)

    def test_unusable_identifiers_and_cross_talk_cannot_bind(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('introduce', name='Ana'), speaker='S1', identifiers=('',))
        self.assertIsNone(flow.pending)
        flow.apply(Decision('introduce', name='Ana'), speaker='S1', identifiers=('opaque',))
        flow.attach_identifiers('S1', ('other-person',), solo=False)
        self.assertEqual(flow.pending.identifiers, ('opaque',))

    def test_empty_roster_cannot_finish_enrollment(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('finish_enrollment'))
        self.assertEqual(flow.phase, 'introductions')
        self.assertEqual(flow.index, 0)

    def test_pending_name_correction_reuses_only_same_solo_voice_evidence(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('introduce', name='Ann'), speaker='S1', identifiers=('opaque',))
        flow.apply(Decision('introduce', name='Ana'), speaker='S1')
        self.assertEqual(flow.pending.name, 'Ana')
        self.assertEqual(flow.pending.identifiers, ('opaque',))
        flow.apply(Decision('introduce', name='Ben'), speaker='S2')
        self.assertEqual(flow.pending.name, 'Ana')
        flow.apply(Decision('introduce', name='Cara'), speaker='S1', solo=False)
        self.assertEqual(flow.pending.name, 'Ana')
