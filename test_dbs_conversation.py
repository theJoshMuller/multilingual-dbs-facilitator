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
        flow.apply(Decision('clarify_name', name='Ana'), speaker='S1', identifiers=('opaque',))
        flow.attach_identifiers('S1', ('other-person',), solo=False)
        self.assertEqual(flow.pending.identifiers, ('opaque',))

    def test_empty_roster_cannot_finish_enrollment(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('finish_enrollment'))
        self.assertEqual(flow.phase, 'introductions')
        self.assertEqual(flow.index, 0)

    def test_pending_name_correction_reuses_only_same_solo_voice_evidence(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('clarify_name', name='Ann'), speaker='S1', identifiers=('opaque',))
        flow.apply(Decision('clarify_name', name='Ana'), speaker='S1')
        self.assertEqual(flow.pending.name, 'Ana')
        self.assertEqual(flow.pending.identifiers, ('opaque',))
        flow.apply(Decision('introduce', name='Ben'), speaker='S2')
        self.assertEqual(flow.pending.name, 'Ana')
        flow.apply(Decision('introduce', name='Cara'), speaker='S1', solo=False)
        self.assertEqual(flow.pending.name, 'Ana')

    def test_clear_self_introduction_records_name_without_confirmation_gate(self):
        flow = ConversationFlow(lesson())
        prompts = flow.apply(Decision('introduce', name='Josh', speech="Josh, glad to have you."),
                             speaker='S1', identifiers=('real-fixture-evidence',))
        self.assertEqual([person.name for person in flow.roster], ['Josh'])
        self.assertEqual(flow.phase, 'introductions')
        self.assertIsNone(flow.pending)
        self.assertEqual(prompts[0].values['text'], "Josh, glad to have you.")

    def test_three_people_and_explicit_group_ready_advance_once(self):
        flow = ConversationFlow(lesson())
        for number, name in enumerate(('Anna', 'Ben', 'Carla'), 1):
            self.assertEqual(flow.apply(Decision('introduce', name=name),
                speaker=f'S{number}', identifiers=(f'evidence-{number}',)), [])
        self.assertEqual([person.name for person in flow.roster], ['Anna', 'Ben', 'Carla'])
        self.assertEqual(flow.question_key, 'f.001')
        prompts = flow.apply(Decision('finish_enrollment'))
        self.assertEqual([prompt.key for prompt in prompts], ['scripture', 'a.001'])
        self.assertEqual(flow.question_key, 'a.001')
        self.assertEqual(flow.apply(Decision('finish_enrollment')), [])
        self.assertEqual(flow.question_key, 'a.001')

    def test_last_person_can_introduce_and_finish_in_one_contribution(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('introduce', name='Anna'), speaker='S1', identifiers=('one',))
        prompts = flow.apply(Decision('finish_enrollment', name='Carla', speech='Let us continue.'),
                             speaker='S2', identifiers=('two',))
        self.assertEqual([person.name for person in flow.roster], ['Anna', 'Carla'])
        self.assertIsNone(flow.pending)
        self.assertEqual(prompts[-1].key, 'a.001')

    def test_last_introduction_with_missing_evidence_cannot_skip_or_advance(self):
        for speaker, identifiers, solo in (('S2', (), True), ('UU', ('two',), True),
                                            ('S2', ('two',), False)):
            with self.subTest(speaker=speaker, identifiers=identifiers, solo=solo):
                flow = ConversationFlow(lesson())
                flow.apply(Decision('introduce', name='Anna'), speaker='S1', identifiers=('one',))
                flow.apply(Decision('finish_enrollment', name='Carla'),
                           speaker=speaker, identifiers=identifiers, solo=solo)
                self.assertEqual([person.name for person in flow.roster], ['Anna'])
                self.assertEqual(flow.phase, 'introductions')
                self.assertEqual(flow.question_key, 'f.001')

    def test_only_uncertain_name_enters_same_speaker_clarification(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('clarify_name', name='Ann', speech='Ann, did I hear that correctly?'),
                   speaker='S1', identifiers=('one',))
        self.assertEqual(flow.phase, 'confirm_name')
        self.assertEqual(flow.roster, [])
        flow.apply(Decision('confirm_name'), speaker='S2')
        self.assertEqual(flow.roster, [])
        flow.apply(Decision('finish_enrollment'), speaker='S2')
        self.assertEqual(flow.question_key, 'f.001')
        flow.apply(Decision('introduce', name='Anna'), speaker='S1')
        self.assertEqual([person.name for person in flow.roster], ['Anna'])
        self.assertIsNone(flow.pending)

    def test_explicit_self_correction_requires_matching_voice_evidence(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('introduce', name='Ann'), speaker='S1', identifiers=('one',))
        flow.apply(Decision('introduce', name='Ben'), speaker='S1', identifiers=('other-voice',))
        self.assertEqual([person.name for person in flow.roster], ['Ann'])
        flow.apply(Decision('introduce', name='Anna'), speaker='S1', identifiers=('one',))
        self.assertEqual([person.name for person in flow.roster], ['Anna'])

    def test_other_person_can_share_without_erasing_uncertain_name(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('clarify_name', name='Ann'), speaker='S1', identifiers=('one',))
        flow.apply(Decision('introduce', name='Ben'), speaker='S2', identifiers=('two',))
        self.assertIsNotNone(flow.pending)
        self.assertEqual(flow.pending.name, 'Ann')
        self.assertEqual([person.name for person in flow.roster], ['Ben'])
        self.assertEqual(flow.phase, 'confirm_name')
        flow.apply(Decision('finish_enrollment'))
        self.assertEqual(flow.question_key, 'f.001')

    def test_second_uncertain_name_cannot_replace_first_person(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('clarify_name', name='Ann'), speaker='S1', identifiers=('one',))
        flow.apply(Decision('clarify_name', name='Ben'), speaker='S2', identifiers=('two',))
        self.assertEqual(flow.pending.name, 'Ann')
        self.assertEqual(flow.pending.identifiers, ('one',))

    def test_pending_name_cannot_be_taken_by_a_different_person(self):
        flow = ConversationFlow(lesson())
        flow.apply(Decision('clarify_name', name='Ana'), speaker='S1', identifiers=('one',))
        flow.apply(Decision('introduce', name='Ana'), speaker='S2', identifiers=('two',))
        self.assertEqual(flow.roster, [])
        self.assertEqual(flow.pending.speaker, 'S1')
        flow.apply(Decision('confirm_name'), speaker='S1')
        self.assertEqual([(p.name, p.speaker) for p in flow.roster], [('Ana', 'S1')])

    def test_pending_person_reserves_a_place_at_participant_limit(self):
        flow = ConversationFlow(lesson(), max_people=1)
        flow.apply(Decision('clarify_name', name='Ana'), speaker='S1', identifiers=('one',))
        flow.apply(Decision('introduce', name='Ben'), speaker='S2', identifiers=('two',))
        self.assertEqual(flow.roster, [])
        self.assertEqual(flow.pending.name, 'Ana')
        flow.apply(Decision('confirm_name'), speaker='S1')
        self.assertEqual([p.name for p in flow.roster], ['Ana'])

    def test_correction_back_to_registered_name_resolves_only_matching_voice(self):
        for identifiers, resolved in ((('one',), True), ((), True), (('other-voice',), False)):
            with self.subTest(identifiers=identifiers):
                flow = ConversationFlow(lesson())
                flow.apply(Decision('introduce', name='Ana'), speaker='S1', identifiers=('one',))
                flow.apply(Decision('clarify_name', name='Ann'), speaker='S1', identifiers=('one',))
                flow.apply(Decision('introduce', name='Ana'), speaker='S1', identifiers=identifiers)
                self.assertEqual([p.name for p in flow.roster], ['Ana'])
                self.assertEqual(flow.pending is None, resolved)
                flow.apply(Decision('finish_enrollment'))
                self.assertEqual(flow.question_key, 'a.001' if resolved else 'f.001')
