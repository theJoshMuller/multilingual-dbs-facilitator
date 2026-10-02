"""Session-memory ASR evidence. Diarization labels are not human identities."""

from __future__ import annotations

import copy
import math
import re


def confident(value, floor):
    return type(value) in (int, float) and math.isfinite(value) and floor <= value <= 1


class TurnLedger:
    def __init__(self, limit=200):
        self.limit = limit
        self.turns = {}
        self.final_orders = set()
        self.names = {}
        self.pending = {}

    def clear(self):
        self.turns.clear()
        self.final_orders.clear()
        self.names.clear()
        self.pending.clear()

    @staticmethod
    def eligible(turn):
        words = turn.get("words", [])
        label = turn.get("speaker_label")
        confidence = turn.get("speaker_confidence")
        if (
            not turn.get("end_of_turn")
            or not isinstance(label, str)
            or not re.fullmatch("[A-Z]", label)
            or not confident(confidence, 0.9)
            or not words
            or turn.get("overlapping")
        ):
            return False
        if any(
            w.get("speaker") != label
            or w.get("word_is_final") is not True
            or not confident(w.get("speaker_confidence"), 0.9)
            for w in words
        ):
            return False
        return (
            max(w.get("end", 0) for w in words) - min(w.get("start", 0) for w in words)
            >= 2000
        )

    def event(self, turn, *, new_final=False, revision=False):
        eligible = self.eligible(turn)
        binding = self.names.get(turn.get("speaker_label"))
        return {
            "type": "transcript",
            "turn_order": turn["turn_order"],
            "text": turn.get("transcript", ""),
            "final": turn.get("end_of_turn") is True,
            "new_final": new_final,
            "revision": revision,
            "source": "participant",
            "speaker_label": turn.get("speaker_label"),
            "speaker_confidence": turn.get("speaker_confidence"),
            "language_code": turn.get("language_code"),
            "language_confidence": turn.get("language_confidence"),
            "identity_eligible": eligible,
            "verified_name": binding["name"] if eligible and binding else None,
            "identity_status": "confirmed_same_label"
            if eligible and binding
            else "UNVERIFIED",
            "human_test_verified": False,
            "speakers": [
                {
                    "speaker": w.get("speaker"),
                    "confidence": w.get("speaker_confidence"),
                    "text": w.get("text", ""),
                    "start": w.get("start", 0) / 1000,
                    "end": w.get("end", 0) / 1000,
                }
                for w in turn.get("words", [])
            ],
        }

    def _revoke(self, labels):
        for label in labels:
            self.names.pop(label, None)
            self.pending.pop(label, None)

    def accept(self, message):
        if message.get("type") == "Turn":
            order = message.get("turn_order")
            if type(order) is not int or order < 0:
                raise ValueError("Invalid turn order")
            previous = self.turns.get(order)
            if (
                previous
                and previous.get("end_of_turn")
                and not message.get("end_of_turn")
            ):
                return []  # Late partials cannot demote a completed thought.
            if previous and previous.get("speaker_label") != message.get(
                "speaker_label"
            ):
                self._revoke(
                    {previous.get("speaker_label"), message.get("speaker_label")}
                )
            turn = copy.deepcopy(message)
            self.turns[order] = turn
            if previous and (
                not self.eligible(turn)
                or previous.get("transcript") != turn.get("transcript")
            ):
                label = previous.get("speaker_label")
                evidence = self.names.get(label) or self.pending.get(label)
                if evidence and order in evidence["orders"]:
                    self._revoke({label})
            corrections = []
            for other_order, other in self.turns.items():
                if (
                    other_order == order
                    or not other.get("end_of_turn")
                    or not turn.get("end_of_turn")
                    or other.get("speaker_label") == turn.get("speaker_label")
                ):
                    continue
                if any(
                    a.get("start", 0) < b.get("end", 0)
                    and b.get("start", 0) < a.get("end", 0)
                    for a in turn.get("words", [])
                    for b in other.get("words", [])
                ):
                    turn["overlapping"] = other["overlapping"] = True
                    self._revoke(
                        {turn.get("speaker_label"), other.get("speaker_label")}
                    )
                    corrections.append(self.event(other, revision=True))
            final = turn.get("end_of_turn") is True
            fresh = final and order not in self.final_orders
            if final:
                self.final_orders.add(order)
            while len(self.turns) > self.limit:
                oldest = next(iter(self.turns))
                expired = self.turns.pop(oldest)
                self.final_orders.discard(oldest)
                self._revoke({expired.get("speaker_label")})
            return [*corrections, self.event(turn, new_final=fresh)]
        if message.get("type") == "SpeakerRevision":
            events = []
            for revision in message.get("revisions", []):
                turn = self.turns.get(revision.get("turn_order"))
                if not turn:
                    continue
                label = revision.get("speaker_label")
                self._revoke({turn.get("speaker_label"), label})
                turn["speaker_label"] = label
                turn.pop("speaker_confidence", None)
                # Only attribution changes. Text/timestamps remain original.
                revised = revision.get("words", [])
                for index, word in enumerate(turn.get("words", [])):
                    word["speaker"] = (
                        revised[index].get("speaker") if index < len(revised) else None
                    )
                    word.pop("speaker_confidence", None)
                events.append(self.event(turn, revision=True))
            return events
        return []

    def introduce(self, order, name):
        turn = self.turns.get(order, {})
        if (
            not self.eligible(turn)
            or not isinstance(name, str)
            or not re.fullmatch(r"[^\W\d_][\w'’ -]{0,59}", name)
            or any(
                n["name"].casefold() == name.casefold()
                for n in (*self.names.values(), *self.pending.values())
            )
        ):
            return False
        self.pending[turn["speaker_label"]] = {"name": name, "orders": (order,)}
        return True

    def confirm(self, order, name):
        turn = self.turns.get(order, {})
        pending = self.pending.get(turn.get("speaker_label"))
        if (
            not self.eligible(turn)
            or not pending
            or pending["name"] != name
            or order in pending["orders"]
        ):
            return False
        evidence = self.turns.get(pending["orders"][0], {})
        if not self.eligible(evidence) or evidence.get("speaker_label") != turn.get(
            "speaker_label"
        ):
            return False
        total = sum(
            max(w["end"] for w in t["words"]) - min(w["start"] for w in t["words"])
            for t in (evidence, turn)
        )
        if total < 6000:
            return False
        self.names[turn["speaker_label"]] = {
            "name": name,
            "orders": (*pending["orders"], order),
        }
        del self.pending[turn["speaker_label"]]
        return True


class TranslationGate:
    """Eligibility only. No translator/model is called by this ASR spike."""

    def __init__(self):
        self.sent = set()

    def targets(self, event, *, mixed, languages):
        source_language = event.get("language_code")
        if (
            not mixed
            or event.get("source") != "participant"
            or event.get("playback")
            or not event.get("final")
            or not event.get("identity_eligible")
            or not event.get("verified_name")
            or source_language not in languages
            or not confident(event.get("language_confidence"), 0.85)
            or event.get("revision")
        ):
            return []
        targets = []
        for target in dict.fromkeys(languages):
            key = (event["turn_order"], target)
            if target != source_language and key not in self.sent:
                targets.append(target)
                self.sent.add(key)
        return targets
