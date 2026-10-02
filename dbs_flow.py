"""Deterministic DBS facilitation. Model output is an intent, never spoken prose."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum


class Intent(StrEnum):
    INTRODUCE = "introduce"
    YES = "yes"
    NO = "no"
    FINISH_ENROLLMENT = "finish_enrollment"
    NEXT = "next"
    REPEAT = "repeat"
    SCRIPTURE = "scripture"
    QUESTION = "question"
    DISCUSSION = "discussion"
    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"
    SAFETY = "safety"


@dataclass(frozen=True)
class Event:
    intent: Intent
    speaker: str = ""
    name: str = ""
    # Only true after a real GetSpeakers result, not an LLM assertion.
    identifiers: tuple[str, ...] = ()
    solo: bool = True


@dataclass(frozen=True)
class Prompt:
    key: str
    values: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Participant:
    name: str
    speaker: str
    identifiers: tuple[str, ...]


def valid_name(name: str) -> bool:
    return (
        isinstance(name, str) and 0 < len(name) <= 60 and name == name.strip()
        and all(c.isalpha() or c in " '-’" for c in name)
        and any(c.isalpha() for c in name)
        and name.upper() != "UU" and not re.fullmatch(r"S\d+", name, re.IGNORECASE)
    )


class DBSFlow:
    """No silent stage skipping, inferred attendance, theology, or disk persistence."""

    def __init__(self, steps: list[str], *, text_mode: bool = False, max_people: int = 7, manual_scripture: bool = False):
        if not steps or "scripture" not in steps:
            raise ValueError("Lesson must contain canonical questions and scripture")
        if not 1 <= max_people <= 100:
            raise ValueError("max_people must be between 1 and 100")
        self.steps = steps
        self.text_mode = text_mode
        self.manual_scripture = manual_scripture
        self.max_people = max_people
        self.phase = "introductions"
        self.roster: list[Participant] = []
        self.pending: Participant | None = None
        self.index = -1
        self.paused = False
        self.contributed: set[str] = set()

    def start(self) -> list[Prompt]:
        # Permissions belong to app onboarding, not spoken facilitation.
        prompts = [Prompt("welcome")]
        if self.text_mode:
            prompts.append(Prompt("text_notice"))
        return prompts

    def _roster_prompt(self) -> list[Prompt]:
        return [Prompt("roster", {
            "names": ", ".join(p.name for p in self.roster),
            "count": str(len(self.roster)),
        })]

    def _advance(self) -> list[Prompt]:
        self.index += 1
        self.contributed.clear()
        self.phase = "lesson"
        if self.index == len(self.steps):
            self.phase = "done"
            return [Prompt("done")]
        prompts = [Prompt(self.steps[self.index])]
        # The story introduction, exact passage, and retelling question play together.
        while self.steps[self.index] in ("f.008", "scripture") and self.index + 1 < len(self.steps):
            if self.steps[self.index] == "scripture" and self.manual_scripture:
                break
            self.index += 1
            prompts.append(Prompt(self.steps[self.index]))
        if self.index == len(self.steps) - 1:
            self.phase = "done"
        return prompts

    def idle(self) -> list[Prompt]:
        if self.paused or self.phase != "lesson":
            return []
        return [Prompt("invite")]

    def handle(self, event: Event) -> list[Prompt]:
        if self.phase == "done":
            return []
        if event.intent == Intent.STOP:
            self.phase = "done"
            self.roster.clear()
            self.pending = None
            return [Prompt("stopped")]
        if event.intent == Intent.SAFETY:
            self.paused = True
            return [Prompt("safety")]
        if event.intent == Intent.PAUSE:
            self.paused = True
            return [Prompt("paused")]
        if self.paused:
            if event.intent == Intent.RESUME:
                self.paused = False
                return [Prompt("resumed")]
            return []

        if self.phase == "confirm_name":
            assert self.pending is not None
            # A second person's yes must never attach a name to somebody else's voice.
            if not event.solo or (not self.text_mode and event.speaker != self.pending.speaker):
                return [Prompt("same_speaker")]
            if event.intent == Intent.YES:
                self.roster.append(self.pending)
                self.pending = None
                self.phase = "introductions"
                return [Prompt("next_person")]
            if event.intent == Intent.NO:
                self.pending = None
                self.phase = "introductions"
                return [Prompt("retry_intro")]
            return [Prompt("confirm_name", {"name": self.pending.name})]

        if self.phase == "confirm_roster":
            if event.intent == Intent.YES:
                # The opening combines introductions and thankfulness (f.001).
                # Do not repeat the old welcome/question after everyone shares.
                if self.steps[0] == "f.001":
                    self.index = 0
                return [Prompt("facilitation_rules"), *self._advance()]
            if event.intent == Intent.NO:
                # Rejecting a roster starts fresh rather than retaining mistaken bindings.
                self.roster.clear()
                self.phase = "introductions"
                return [Prompt("retry_roster")]
            return self._roster_prompt()

        if self.phase == "introductions":
            if event.intent == Intent.DISCUSSION and any(p.speaker == event.speaker for p in self.roster):
                # A confirmed person may continue sharing without restating a name.
                return []
            if event.intent == Intent.FINISH_ENROLLMENT:
                if not self.roster:
                    return [Prompt("retry_intro")]
                self.phase = "confirm_roster"
                return self._roster_prompt()
            if event.intent != Intent.INTRODUCE or not valid_name(event.name):
                return [Prompt("retry_intro")]
            if not event.solo:
                return [Prompt("solo_intro")]
            if len(self.roster) >= self.max_people:
                return [Prompt("full", {"count": str(self.max_people)})]
            if any(p.name.casefold() == event.name.casefold() for p in self.roster):
                return [Prompt("duplicate_name")]
            if not self.text_mode:
                if not event.speaker or event.speaker == "UU" or not event.identifiers:
                    return [Prompt("more_speech")]
                if any(p.speaker == event.speaker for p in self.roster):
                    return [Prompt("speaker_collision")]
            self.pending = Participant(event.name, event.speaker, event.identifiers)
            self.phase = "confirm_name"
            return [Prompt("confirm_name", {"name": event.name})]

        if event.intent == Intent.QUESTION:
            # An explicit request for an answer cannot advance the lesson either.
            return [Prompt("redirect")]
        if event.intent == Intent.SCRIPTURE:
            return [Prompt("scripture")]
        if event.intent == Intent.REPEAT:
            return [Prompt(self.steps[self.index])]
        if self.phase == "confirm_next":
            if event.intent == Intent.YES:
                return self._advance()
            if event.intent == Intent.NO:
                self.phase = "lesson"
                return [Prompt("invite")]
            # Discussion resumes without an unsolicited response.
            if event.intent == Intent.DISCUSSION:
                self.phase = "lesson"
                return []
        if event.intent == Intent.NEXT:
            self.phase = "confirm_next"
            missing = [p.name for p in self.roster if p.speaker not in self.contributed]
            if missing and self.steps[self.index] not in ("f.008", "scripture", "a.007"):
                return [Prompt("confirm_next_missing", {"names": ", ".join(missing)})]
            return [Prompt("confirm_next")]
        if event.intent == Intent.DISCUSSION and event.speaker:
            self.contributed.add(event.speaker)
        return []
