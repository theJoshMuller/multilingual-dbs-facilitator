"""
William — a group-conversation voice agent.

One microphone, several people talking. Speechmatics diarization tags each
utterance with a speaker ID; William stays quiet and listens until either:

  1. someone says his name, or
  2. the room goes quiet for IDLE_REPLY_SECONDS.

Run locally:   python agent.py console
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import sounddevice as sd
from dotenv import load_dotenv
from livekit import agents
from livekit.agents import (
    Agent,
    AgentSession,
    JobContext,
    RoomInputOptions,
    StopResponse,
    llm,
)
from livekit.plugins import openai, silero, speechmatics
from speechmatics.rt import (
    AsyncClient as SpeechmaticsClient,
    AudioEncoding,
    AudioFormat,
    ClientMessageType,
    Model,
    ServerMessageType,
    TranscriptionConfig,
)

load_dotenv(".env.local")
load_dotenv()
logger = logging.getLogger("william")

SPEAKER_PROFILES_PATH = Path(
    os.getenv(
        "SPEAKER_PROFILES_FILE",
        str(Path(__file__).with_name(".speaker_profiles.json")),
    )
)
SPEAKER_PROFILES_VERSION = 1
ENROLLMENT_MODEL = Model.ENHANCED
ENROLLMENT_SAMPLE_RATE = 16_000
ENROLLMENT_CHUNK_FRAMES = 1_600
MAX_SPEAKER_IDENTIFIERS = 50
MANAGEMENT_COMMANDS = {"enroll", "list-speakers", "remove-speaker"}


# ═══════════════════════════════════════════════════════════════════════
#  PROMPT — this is the part to grow.
# ═══════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """\
You are William, a voice participant in a conversation between several people
who are sharing a single microphone in the same room.

The transcript you receive is speaker-tagged, like this:

    [Speaker S1] I think we should push the deadline.
    [Speaker S2] Disagree — the client already moved it twice.

Enrolled speakers have their name in the tag, such as [Speaker Josh]. Unknown
speakers use temporary labels such as S1 or S2. Track who holds which position
across the conversation. If an unknown speaker states their name, start using
it.

How to behave:
- You are a participant, not a narrator. Never summarize what was just said
  back to the group unless asked.
- Keep replies to two or three sentences. This is speech, not prose.
- Address people by their speaker label or name when responding to a specific
  point ("S2, on the client timeline —").
- When you were not directly addressed and are speaking into a lull, only
  contribute if you have something concrete: a fact, a correction, an option
  nobody raised. Otherwise reply with the single word SKIP.
"""

# Anything matching this counts as the group addressing William directly.
# Keep it tight — a loose pattern fires on ordinary speech.
WAKE_PATTERN = re.compile(r"\b(william|will iam|willem)\b", re.IGNORECASE)

# Seconds of room silence after which William may volunteer something.
IDLE_REPLY_SECONDS = 5.0

# Reply exactly equal to this (from the prompt above) is swallowed, not spoken.
SKIP_TOKEN = "SKIP"


# ═══════════════════════════════════════════════════════════════════════
#  Speaker enrollment
# ═══════════════════════════════════════════════════════════════════════


def _validate_speaker_label(label: str) -> str:
    if not label or label != label.strip():
        raise ValueError("Speaker names must be non-empty with no surrounding spaces")
    if "\n" in label or "\r" in label:
        raise ValueError("Speaker names must fit on one line")
    if label.upper() == "UU" or re.fullmatch(r"S\d+", label, re.IGNORECASE):
        raise ValueError(f"{label!r} is reserved for Speechmatics' internal speaker labels")
    return label


def _empty_profile_store() -> dict[str, Any]:
    return {
        "version": SPEAKER_PROFILES_VERSION,
        "model": ENROLLMENT_MODEL.value,
        "speakers": [],
    }


def _read_profile_store(path: Path = SPEAKER_PROFILES_PATH) -> dict[str, Any]:
    if not path.exists():
        return _empty_profile_store()

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read speaker profiles from {path}: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError(f"Invalid speaker profile document in {path}")
    if data.get("version") != SPEAKER_PROFILES_VERSION:
        raise ValueError(f"Unsupported speaker profile version in {path}")
    if data.get("model") != ENROLLMENT_MODEL.value:
        raise ValueError(
            f"Speaker profiles in {path} were generated for model "
            f"{data.get('model')!r}, not {ENROLLMENT_MODEL.value!r}; re-enroll them"
        )
    if not isinstance(data.get("speakers"), list):
        raise ValueError(f"Invalid speaker profile list in {path}")

    labels: set[str] = set()
    total_identifiers = 0
    for speaker in data["speakers"]:
        if not isinstance(speaker, dict):
            raise ValueError(f"Invalid speaker profile entry in {path}")
        label = _validate_speaker_label(speaker.get("label", ""))
        if label in labels:
            raise ValueError(f"Duplicate speaker label {label!r} in {path}")
        labels.add(label)
        identifiers = speaker.get("speaker_identifiers")
        if not identifiers or not isinstance(identifiers, list):
            raise ValueError(f"Invalid identifiers for {label!r} in {path}")
        if any(not isinstance(identifier, str) or not identifier for identifier in identifiers):
            raise ValueError(f"Invalid identifiers for {label!r} in {path}")
        total_identifiers += len(identifiers)

    if total_identifiers > MAX_SPEAKER_IDENTIFIERS:
        raise ValueError(
            f"Speaker profiles in {path} contain {total_identifiers} identifiers; "
            f"Speechmatics allows at most {MAX_SPEAKER_IDENTIFIERS} per session"
        )

    return data


def _write_profile_store(data: dict[str, Any], path: Path = SPEAKER_PROFILES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f"{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        descriptor = -1
        with stream:
            stream.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary_path.replace(path)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        temporary_path.unlink(missing_ok=True)


def load_known_speakers(
    path: Path = SPEAKER_PROFILES_PATH,
) -> list[speechmatics.SpeakerIdentifier]:
    data = _read_profile_store(path)
    return [
        speechmatics.SpeakerIdentifier(
            label=speaker["label"],
            speaker_identifiers=speaker["speaker_identifiers"],
        )
        for speaker in data["speakers"]
    ]


def _save_enrollment(
    label: str,
    identifiers: list[str],
    *,
    replace: bool = False,
    path: Path = SPEAKER_PROFILES_PATH,
) -> int:
    label = _validate_speaker_label(label)
    identifiers = list(dict.fromkeys(identifiers))
    if not identifiers:
        raise ValueError("Speechmatics returned no speaker identifiers")

    data = _read_profile_store(path)
    existing = next(
        (speaker for speaker in data["speakers"] if speaker["label"] == label),
        None,
    )
    if existing is None:
        existing = {"label": label, "speaker_identifiers": []}
        data["speakers"].append(existing)

    current = [] if replace else existing["speaker_identifiers"]
    existing["speaker_identifiers"] = list(dict.fromkeys([*current, *identifiers]))

    total = sum(
        len(speaker["speaker_identifiers"])
        for speaker in data["speakers"]
    )
    if total > MAX_SPEAKER_IDENTIFIERS:
        raise ValueError(
            f"This would configure {total} identifiers; Speechmatics allows "
            f"at most {MAX_SPEAKER_IDENTIFIERS} per session"
        )

    _write_profile_store(data, path)
    return len(existing["speaker_identifiers"])


def _remove_speaker(label: str, path: Path = SPEAKER_PROFILES_PATH) -> bool:
    data = _read_profile_store(path)
    remaining = [
        speaker for speaker in data["speakers"] if speaker["label"] != label
    ]
    if len(remaining) == len(data["speakers"]):
        return False
    data["speakers"] = remaining
    _write_profile_store(data, path)
    return True


def _extract_enrollment_identifiers(messages: list[dict[str, Any]]) -> list[str]:
    speakers = [
        speaker
        for message in messages
        for speaker in message.get("speakers", [])
        if speaker.get("speaker_identifiers")
    ]
    if not speakers:
        raise ValueError(
            "No voice identifier was produced. Re-run enrollment and speak clearly "
            "for the full recording."
        )
    if len(speakers) != 1:
        raise ValueError(
            f"Speechmatics detected {len(speakers)} speakers. Re-run enrollment "
            "with only the person being enrolled in the room."
        )
    return list(dict.fromkeys(speakers[0]["speaker_identifiers"]))


def _transcript_words(message: dict[str, Any]) -> list[str]:
    return [
        alternative["content"]
        for result in message.get("results", [])
        for alternative in result.get("alternatives", [])[:1]
        if alternative.get("content")
    ]


async def enroll_speaker(
    label: str,
    *,
    seconds: int = 20,
    language: str = "en",
) -> list[str]:
    _validate_speaker_label(label)
    if not 5 <= seconds <= 30:
        raise ValueError("Enrollment duration must be between 5 and 30 seconds")
    if not language.strip():
        raise ValueError("Enrollment language must be non-empty")
    api_key = os.getenv("SPEECHMATICS_API_KEY")
    if not api_key:
        raise ValueError("SPEECHMATICS_API_KEY is not set")

    speaker_messages: list[dict[str, Any]] = []
    transcript_words: list[str] = []
    client = SpeechmaticsClient(api_key=api_key)
    client.on(ServerMessageType.SPEAKERS_RESULT, speaker_messages.append)
    client.on(
        ServerMessageType.ADD_TRANSCRIPT,
        lambda message: transcript_words.extend(_transcript_words(message)),
    )

    transcription_config = TranscriptionConfig(
        language=language,
        model=ENROLLMENT_MODEL,
        diarization="speaker",
    )
    audio_format = AudioFormat(
        encoding=AudioEncoding.PCM_S16LE,
        sample_rate=ENROLLMENT_SAMPLE_RATE,
        chunk_size=ENROLLMENT_CHUNK_FRAMES * 2,
    )

    print(
        f"Enroll {label}: speak naturally and alone for {seconds} seconds. "
        "Use the same microphone and position you expect in real conversations."
    )
    for remaining in range(3, 0, -1):
        print(f"Starting in {remaining}…", flush=True)
        await asyncio.sleep(1)

    try:
        with sd.RawInputStream(
            samplerate=ENROLLMENT_SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=ENROLLMENT_CHUNK_FRAMES,
        ) as microphone:
            await client.start_session(
                transcription_config=transcription_config,
                audio_format=audio_format,
            )
            await client.send_message(
                {"message": ClientMessageType.GET_SPEAKERS, "final": True}
            )

            last_remaining = None
            total_chunks = (
                seconds * ENROLLMENT_SAMPLE_RATE // ENROLLMENT_CHUNK_FRAMES
            )
            for chunk_index in range(total_chunks):
                audio, overflowed = await asyncio.to_thread(
                    microphone.read,
                    ENROLLMENT_CHUNK_FRAMES,
                )
                if overflowed:
                    logger.warning("Microphone input overflowed during enrollment")
                await client.send_audio(bytes(audio))
                recorded_frames = (chunk_index + 1) * ENROLLMENT_CHUNK_FRAMES
                remaining = max(
                    0,
                    seconds - recorded_frames // ENROLLMENT_SAMPLE_RATE,
                )
                if remaining != last_remaining:
                    print(f"\rRecording… {remaining:2d}s remaining", end="", flush=True)
                    last_remaining = remaining

            print("\rRecording complete. Processing voice identifier…", flush=True)
            await client.stop_session()
    finally:
        await client.close()

    identifiers = _extract_enrollment_identifiers(speaker_messages)
    if not transcript_words:
        raise ValueError(
            "No speech was transcribed. The enrollment was not saved; check the "
            "microphone and try again."
        )
    print(f"Captured {len(transcript_words)} transcribed words.")
    return identifiers


def _management_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python agent.py",
        description="Manage William's enrolled Speechmatics speakers.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    enroll = commands.add_parser("enroll", help="Record and enroll one speaker")
    enroll.add_argument("name", help="Speaker label, such as Josh or Kami")
    enroll.add_argument(
        "--seconds",
        type=int,
        choices=range(5, 31),
        default=20,
        metavar="5-30",
        help="Solo speech duration (default: 20 seconds)",
    )
    enroll.add_argument("--language", default="en", help="Speech language (default: en)")
    enroll.add_argument(
        "--replace",
        action="store_true",
        help="Replace this speaker's existing identifiers instead of appending",
    )

    commands.add_parser("list-speakers", help="List enrolled names and identifier counts")
    remove = commands.add_parser("remove-speaker", help="Remove an enrolled speaker")
    remove.add_argument("name")
    return parser


def run_management_command(argv: list[str]) -> int:
    args = _management_parser().parse_args(argv)
    try:
        if args.command == "enroll":
            identifiers = asyncio.run(
                enroll_speaker(
                    args.name,
                    seconds=args.seconds,
                    language=args.language,
                )
            )
            count = _save_enrollment(
                args.name,
                identifiers,
                replace=args.replace,
            )
            print(
                f"Enrolled {args.name!r} with {count} saved identifier(s). "
                "Future agent sessions will load this profile automatically."
            )
            return 0

        if args.command == "list-speakers":
            speakers = _read_profile_store()["speakers"]
            if not speakers:
                print("No speakers enrolled.")
                return 0
            for speaker in speakers:
                print(f"{speaker['label']}: {len(speaker['speaker_identifiers'])} identifier(s)")
            return 0

        if args.command == "remove-speaker":
            if not _remove_speaker(args.name):
                print(f"Speaker {args.name!r} is not enrolled.", file=sys.stderr)
                return 1
            print(f"Removed speaker {args.name!r}.")
            return 0
    except KeyboardInterrupt:
        print("Enrollment cancelled; no profile was saved.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Enrollment error: {exc}", file=sys.stderr)
        return 1

    return 1


# ═══════════════════════════════════════════════════════════════════════
#  Agent
# ═══════════════════════════════════════════════════════════════════════


class William(Agent):
    def __init__(self) -> None:
        super().__init__(instructions=SYSTEM_PROMPT)
        self._buffer: list[str] = []          # group talk William hasn't answered
        self._last_speech = time.monotonic()
        self._idle_task: asyncio.Task | None = None
        self._busy = False

    async def on_enter(self) -> None:
        self._idle_task = asyncio.create_task(self._watch_for_silence())

    async def on_exit(self) -> None:
        if self._idle_task:
            self._idle_task.cancel()

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        """Runs at the end of every human turn, before any reply is generated.

        We raise StopResponse unconditionally, which suppresses the framework's
        automatic answer. Every word William speaks is triggered explicitly in
        _respond() instead — that's what turns him from a call-and-response
        assistant into something that sits in a group and waits.
        """
        text = (new_message.text_content or "").strip()
        self._last_speech = time.monotonic()

        if not text:
            raise StopResponse()

        self._buffer.append(text)
        logger.debug("heard: %s", text)

        if WAKE_PATTERN.search(text):
            asyncio.create_task(self._respond(reason="addressed"))

        raise StopResponse()

    async def _watch_for_silence(self) -> None:
        """Fires a reply once the room has been quiet long enough."""
        while True:
            await asyncio.sleep(0.25)
            if self._busy or not self._buffer:
                continue
            if time.monotonic() - self._last_speech >= IDLE_REPLY_SECONDS:
                await self._respond(reason="silence")

    async def _respond(self, reason: str) -> None:
        if self._busy or not self._buffer:
            return
        self._busy = True
        try:
            transcript = "\n".join(self._buffer)
            self._buffer.clear()
            logger.info("responding (%s):\n%s", reason, transcript)

            if reason == "addressed":
                instructions = (
                    "You were addressed by name. Respond to what was asked of you."
                )
            else:
                instructions = (
                    "The room has fallen silent. You were not addressed. "
                    f"Contribute only if you have something concrete to add; "
                    f"otherwise reply with exactly {SKIP_TOKEN}."
                )

            handle = self.session.generate_reply(
                user_input=transcript,
                instructions=instructions,
            )
            await handle.wait_for_playout()
        finally:
            # Reset the clock so a silence-triggered reply doesn't immediately
            # retrigger itself.
            self._last_speech = time.monotonic()
            self._busy = False


# ═══════════════════════════════════════════════════════════════════════
#  Session
# ═══════════════════════════════════════════════════════════════════════


async def entrypoint(ctx: JobContext) -> None:
    await ctx.connect()

    known_speakers = load_known_speakers()
    if known_speakers:
        logger.info(
            "loaded enrolled speakers: %s",
            ", ".join(speaker.label for speaker in known_speakers),
        )
    else:
        logger.warning(
            "no enrolled speakers; run `python agent.py enroll NAME` for stable names"
        )

    session = AgentSession(
        stt=speechmatics.STT(
            enable_diarization=True,
            operating_point=speechmatics.OperatingPoint.ENHANCED,
            speaker_active_format="[Speaker {speaker_id}] {text}",
            known_speakers=known_speakers,
            # max_speakers=4,
            #
            # Once the agent's voice is enrolled, it can be excluded with
            # ignore_speakers=["William"].
        ),
        # Reads OPENROUTER_API_KEY from the environment. fallback_models is
        # OpenRouter's own routing, not a LiveKit feature — worth having when a
        # provider hiccup would otherwise stall a live conversation.
        llm=openai.LLM.with_openrouter(
            model=os.getenv("LLM_MODEL", "anthropic/claude-sonnet-4.5"),
            fallback_models=["openai/gpt-4o"],
        ),
        tts=speechmatics.TTS(),
        vad=silero.VAD.load(),
        # Endpointing is deliberately slack — in a group, short pauses are
        # people thinking, not turn boundaries.
        min_endpointing_delay=0.8,
        max_endpointing_delay=6.0,
    )

    await session.start(
        agent=William(),
        room=ctx.room,
        room_input_options=RoomInputOptions(),
    )
    # No opening greeting on purpose: William waits to be invited in.


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] in MANAGEMENT_COMMANDS:
        raise SystemExit(run_management_command(sys.argv[1:]))
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
