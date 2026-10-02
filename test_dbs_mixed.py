"""Synthetic offline fixtures; these do not establish human diarization accuracy."""

import copy
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from dbs_curriculum import load_lesson
from dbs_mixed_turns import TranslationGate, TurnLedger


def turn(
    order=0,
    *,
    label="A",
    language="en",
    final=True,
    text="A complete synthetic thought.",
    duration=3500,
    confidence=0.97,
):
    return {
        "type": "Turn",
        "turn_order": order,
        "transcript": text,
        "end_of_turn": final,
        "speaker_label": label,
        "speaker_confidence": confidence,
        "language_code": language,
        "language_confidence": 0.96,
        "words": [
            {
                "text": text,
                "start": order * 5000,
                "end": order * 5000 + duration,
                "word_is_final": final,
                "speaker": label,
                "speaker_confidence": confidence,
            }
        ],
    }


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.ledger = TurnLedger()

    def test_partial_final_and_formatted_duplicates_share_one_turn(self):
        partial = self.ledger.accept(turn(final=False))[0]
        self.assertFalse(partial["final"])
        self.assertFalse(partial["new_final"])
        final = self.ledger.accept(turn())[0]
        self.assertTrue(final["new_final"])
        self.assertFalse(self.ledger.accept(turn())[0]["new_final"])
        self.assertEqual(len(self.ledger.turns), 1)

    def test_language_switching_never_binds_a_name(self):
        for order, language in enumerate(("en", "tr", "en")):
            event = self.ledger.accept(turn(order, language=language))[0]
            self.assertEqual(event["language_code"], language)
            self.assertEqual(event["speaker_label"], "A")
            self.assertIsNone(event["verified_name"])

    def test_pending_short_ambiguous_and_low_confidence_are_unattributed(self):
        for index, data in enumerate(
            (turn(label="PENDING"), turn(duration=600), turn(confidence=0.6), turn())
        ):
            data["turn_order"] = index
            if index == 3:
                data["words"].append({**data["words"][0], "speaker": "B"})
            event = self.ledger.accept(data)[0]
            self.assertFalse(event["identity_eligible"])
            self.assertIsNone(event["verified_name"])
            self.assertFalse(self.ledger.introduce(index, "Mehmet"))

    def test_missing_or_pending_word_speakers_do_not_verify_name(self):
        for order, speaker in enumerate((None, "PENDING")):
            data = turn(order)
            if speaker is None:
                del data["words"][0]["speaker"]
            else:
                data["words"][0]["speaker"] = speaker
            self.ledger.accept(data)
            self.assertFalse(self.ledger.introduce(order, "Jill"))

    def test_uncertain_word_confidence_blocks_name_even_with_confident_turn(self):
        for order, confidence in enumerate((None, 0.4, float("nan"), True, 1.1)):
            data = turn(order)
            data["words"][0]["speaker_confidence"] = confidence
            event = self.ledger.accept(data)[0]
            self.assertFalse(event["identity_eligible"])
            self.assertFalse(self.ledger.introduce(order, "Jill"))

    def test_downgraded_word_evidence_revokes_previously_confirmed_name(self):
        self.ledger.accept(turn(0))
        self.ledger.introduce(0, "Jill")
        self.ledger.accept(turn(1))
        self.ledger.confirm(1, "Jill")
        data = turn(0)
        data["words"][0]["speaker_confidence"] = 0.2
        self.ledger.accept(data)
        self.assertIsNone(self.ledger.accept(turn(2))[0]["verified_name"])

    def test_name_requires_two_distinct_final_same_voice_turns(self):
        self.ledger.accept(turn(0))
        self.assertTrue(self.ledger.introduce(0, "Jill"))
        self.assertFalse(self.ledger.confirm(0, "Jill"))
        self.ledger.accept(turn(1, label="B"))
        self.assertFalse(self.ledger.confirm(1, "Jill"))
        self.ledger.accept(turn(2, language="tr"))
        self.assertTrue(self.ledger.confirm(2, "Jill"))
        self.assertEqual(self.ledger.accept(turn(3))[0]["verified_name"], "Jill")

    def test_revision_revokes_name_and_discards_stale_confidence(self):
        self.ledger.accept(turn(0))
        self.ledger.introduce(0, "Jill")
        self.ledger.accept(turn(1))
        self.ledger.confirm(1, "Jill")
        revised = {
            "type": "SpeakerRevision",
            "revisions": [
                {
                    "turn_order": 0,
                    "speaker_label": "B",
                    "words": [
                        {"text": "unchanged", "start": 0, "end": 3500, "speaker": "B"}
                    ],
                }
            ],
        }
        events = self.ledger.accept(revised)
        self.assertEqual(events[0]["speaker_label"], "B")
        self.assertIsNone(events[0]["speaker_confidence"])
        self.assertIsNone(events[0]["verified_name"])
        self.assertEqual(self.ledger.turns[0]["transcript"], turn()["transcript"])
        self.assertIsNone(self.ledger.accept(turn(2))[0]["verified_name"])

    def test_revision_of_later_bound_speaker_also_revokes_name(self):
        self.ledger.accept(turn(0))
        self.ledger.introduce(0, "Jill")
        self.ledger.accept(turn(1))
        self.ledger.confirm(1, "Jill")
        self.ledger.accept(turn(2))
        self.ledger.accept(
            {
                "type": "SpeakerRevision",
                "revisions": [{"turn_order": 2, "speaker_label": "B", "words": []}],
            }
        )
        self.assertIsNone(self.ledger.accept(turn(3))[0]["verified_name"])

    def test_name_collision_and_evidence_eviction_fail_closed(self):
        self.ledger.accept(turn(0))
        self.ledger.introduce(0, "Jill")
        self.ledger.accept(turn(1))
        self.ledger.confirm(1, "Jill")
        self.ledger.accept(turn(2, label="B"))
        self.assertFalse(self.ledger.introduce(2, "Jill"))
        self.ledger.clear()
        self.assertEqual(self.ledger.turns, {})
        self.assertEqual(self.ledger.names, {})

    def test_parallel_pending_claims_cannot_bind_the_same_name_to_two_voices(self):
        self.ledger.accept(turn(0))
        self.ledger.introduce(0, "Jill")
        self.ledger.accept(turn(1, label="B"))
        self.assertFalse(self.ledger.introduce(1, "Jill"))

    def test_late_partial_cannot_demote_completed_turn(self):
        self.ledger.accept(turn())
        self.assertEqual(self.ledger.accept(turn(final=False)), [])
        self.assertTrue(self.ledger.turns[0]["end_of_turn"])

    def test_same_label_downgraded_evidence_revokes_bound_name(self):
        self.ledger.accept(turn(0))
        self.ledger.introduce(0, "Jill")
        self.ledger.accept(turn(1))
        self.ledger.confirm(1, "Jill")
        self.ledger.accept(turn(0, confidence=0.1))
        self.assertIsNone(self.ledger.accept(turn(2))[0]["verified_name"])

    def test_overlapping_word_intervals_across_speakers_are_unattributed(self):
        self.ledger.accept(turn(0))
        data = turn(1, label="B")
        data["words"][0]["start"] = 1000
        data["words"][0]["end"] = 4000
        event = self.ledger.accept(data)[-1]
        self.assertFalse(event["identity_eligible"])
        self.assertFalse(self.ledger.introduce(0, "Jill"))
        self.assertFalse(self.ledger.introduce(1, "Aysha"))

    def test_nonfinite_out_of_range_or_boolean_speaker_confidence_fails_closed(self):
        for order, value in enumerate((float("nan"), float("inf"), 1.1, True)):
            self.assertFalse(
                self.ledger.accept(turn(order, confidence=value))[0][
                    "identity_eligible"
                ]
            )

    def test_within_sentence_switch_is_preserved_verbatim(self):
        text = "I am thankful because bugün ailemle birlikteyim, I think."
        event = self.ledger.accept(turn(text=text, language="tr"))[0]
        self.assertEqual(event["text"], text)


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.gate = TranslationGate()
        self.event = TurnLedger().accept(turn())[0]
        self.event["verified_name"] = "Test Participant"

    def test_monolingual_discussion_does_not_translate(self):
        self.assertEqual(
            self.gate.targets(self.event, mixed=False, languages=["en", "tr"]), []
        )

    def test_only_completed_named_participant_thoughts_translate_once_per_language(
        self,
    ):
        self.assertEqual(
            self.gate.targets(self.event, mixed=True, languages=["en", "tr", "tr"]),
            ["tr"],
        )
        self.assertEqual(
            self.gate.targets(self.event, mixed=True, languages=["en", "tr"]), []
        )

    def test_agent_playback_partials_unknown_language_and_identity_never_translate(
        self,
    ):
        for field, value in (
            ("source", "agent"),
            ("playback", True),
            ("final", False),
            ("language_code", None),
            ("language_confidence", 0.2),
            ("verified_name", None),
        ):
            event = copy.deepcopy(self.event)
            event[field] = value
            self.assertEqual(
                self.gate.targets(event, mixed=True, languages=["en", "tr"]), [], field
            )

    def test_nonfinite_language_confidence_fails_closed(self):
        for value in (float("nan"), float("inf"), True, 1.1):
            event = {**self.event, "language_confidence": value}
            self.assertEqual(
                self.gate.targets(event, mixed=True, languages=["en", "tr"]), []
            )


class WorktreeAssetTests(unittest.TestCase):
    def test_existing_curriculum_honors_explicit_runtime_waha_root(self):
        with (
            patch.dict(os.environ, {"WAHA_ROOT": "/fixture/waha"}),
            patch("dbs_curriculum.resolve_language", side_effect=RuntimeError("root checked")) as resolve,
            self.assertRaisesRegex(RuntimeError, "root checked"),
        ):
            load_lesson("es")
        resolve.assert_called_once_with(Path("/fixture/waha"), "es", "")


if __name__ == "__main__":
    unittest.main()
