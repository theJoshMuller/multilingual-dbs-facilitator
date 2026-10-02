"""LiveKit voice DBS prototype. Run: .venv/bin/python dbs_agent.py console

Use `rehearse` for text-only testing. Unlike LiveKit's --text mode, this exercises
exactly the same deterministic controller without bypassing enrollment hooks.
"""
from __future__ import annotations

import asyncio
import copy
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from livekit import agents, rtc
from livekit.agents import (
    Agent,
    AgentSession,
    StopResponse,
    llm,
    stt,
)
from livekit.plugins import silero, speechmatics

from dbs_curriculum import DEFAULT_WAHA_ROOT, Lesson, load_lesson
from dbs_flow import DBSFlow, Event, Intent, Prompt
from dbs_intents import IntentParser, rule_intent
from dbs_tts import synthesize, tts_language, voice_id

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env.local")
load_dotenv(ROOT / ".env")
logger = logging.getLogger("discovering-god")


def language_catalog() -> dict:
    return json.loads((ROOT / "data/speechmatics-languages.json").read_text())["languages"]


def settings() -> dict:
    language = os.getenv("DBS_LANGUAGE", "en")
    stt_language = os.getenv("STT_LANGUAGE", language)
    if stt_language not in language_catalog():
        raise ValueError("Unsupported realtime language/pack. auto and multi cannot be used for realtime enrollment; run list-languages.")
    ceiling = int(os.getenv("DBS_MAX_SPEAKERS", "10"))
    people = int(os.getenv("DBS_MAX_PEOPLE", "7"))
    if not 2 <= ceiling <= 100 or not 1 <= people <= ceiling:
        raise ValueError("Require 1 <= DBS_MAX_PEOPLE <= DBS_MAX_SPEAKERS, and max speakers 2–100")
    provider = os.getenv("DBS_TTS_PROVIDER", "elevenlabs")
    if provider != "elevenlabs":
        raise ValueError("DBS_TTS_PROVIDER must be elevenlabs; other speech engines have been removed")
    tts_language(language)
    return {"language": language, "stt_language": stt_language, "ceiling": ceiling, "people": people, "tts": provider}


def configured_lesson(config: dict) -> Lesson:
    source = os.getenv("SCRIPTURE_FILE")
    return load_lesson(
        config["language"], root=Path(os.getenv("WAHA_ROOT", str(DEFAULT_WAHA_ROOT))),
        waha_language=os.getenv("WAHA_LANGUAGE", ""),
        scripture_file=Path(source) if source else None,
    )


def make_stt(config: dict) -> speechmatics.STT:
    options = {
        "language": config["stt_language"], "enable_diarization": True,
        "operating_point": speechmatics.OperatingPoint.ENHANCED,
        "turn_detection_mode": speechmatics.TurnDetectionMode.FIXED,
        "end_of_utterance_silence_trigger": 1.0,
        "max_speakers": config["ceiling"],
        "speaker_active_format": "[Speaker {speaker_id}] {text}",
        "speaker_passive_format": "[Speaker {speaker_id}] {text}",
    }
    if os.getenv("STT_DOMAIN"):
        options["domain"] = os.environ["STT_DOMAIN"]
    return speechmatics.STT(**options)


async def speech_frames(text: str, config: dict, voice: str = "") -> list[rtc.AudioFrame]:
    # Provider failures reach the controller; no silent robotic fallback.
    if config["tts"] != "elevenlabs":
        raise ValueError("Only ElevenLabs speech output is enabled")
    return await synthesize(text, config["language"], voice=voice)


async def frame_stream(frames: list[rtc.AudioFrame]):
    for frame in frames:
        yield frame


def speaker_info(text: str) -> tuple[str, bool]:
    speakers = set(re.findall(r"\[Speaker ([^\]\r\n]+)\]", text))
    return (next(iter(speakers)) if len(speakers) == 1 else "", len(speakers) <= 1)


def identifiers_for(result: list, label: str) -> tuple[str, ...]:
    # Plugin 1.8 returns raw dicts despite its SpeakerIdentifier type annotation.
    # More than one stream has independent S1 namespaces: fail closed.
    if any(isinstance(item, list) for item in result):
        return ()
    for speaker in result:
        current = speaker.get("label") if isinstance(speaker, dict) else speaker.label
        values = speaker.get("speaker_identifiers", []) if isinstance(speaker, dict) else speaker.speaker_identifiers
        if current == label and isinstance(values, list) and all(isinstance(v, str) and v for v in values):
            return tuple(values)
    return ()


class DBSController:
    def __init__(self, config: dict, lesson: Lesson, parser: IntentParser, prompts: dict, *, text_mode: bool = False):
        self.flow = DBSFlow(lesson.steps, text_mode=text_mode, max_people=config["people"], manual_scripture=not lesson.verses)
        self.lesson = lesson
        self.parser = parser
        self.prompts = prompts

    def render(self, prompt: Prompt) -> str:
        if prompt.key == "scripture":
            return self.lesson.scripture or self.prompts["manual_scripture"]
        if prompt.key in self.lesson.questions:
            return self.lesson.questions[prompt.key]
        return self.prompts[prompt.key].format(**prompt.values)

    async def accept(self, text: str, *, identifiers: tuple[str, ...] = ()) -> list[Prompt]:
        speaker, solo = speaker_info(text)
        intent, name = await self.parser.classify(text, self.flow.phase)
        if self.flow.text_mode:
            speaker = speaker or (self.flow.pending.speaker if self.flow.pending else f"text-{len(self.flow.roster) + 1}")
        return self.flow.handle(Event(intent, speaker, name, identifiers, solo))


class DiscoveryAgent(Agent):
    def __init__(self, controller: DBSController, recognizer: speechmatics.STT, config: dict):
        # There is deliberately no generative LLM connected to the speech pipeline.
        super().__init__(instructions="Facilitation is controlled by DBSFlow. Never generate answers.")
        self.controller = controller
        self.recognizer = recognizer
        self.config = config
        self.voice = voice_id(config["language"])
        self.tasks: list[asyncio.Task] = []
        self.queue: asyncio.Queue[str | None] = asyncio.Queue(maxsize=20)
        self.active_turn: asyncio.Task | None = None
        self.speech_handle = None
        self.resume_prompts: list[Prompt] = []
        self.busy = False
        self.human_speaking = False
        self.last_activity = time.monotonic()
        self.nudged = False
        self.durations: dict[str, float] = {}
        self.last_prompts: list[Prompt] = []

    async def on_enter(self):
        # Stay in LiveKit's registered on_enter task for inline playback.
        # A child task awaiting speech inherits context but not registration.
        try:
            await self._say(self.controller.flow.start())
        except asyncio.CancelledError:
            if (task := asyncio.current_task()) and task.cancelling():
                raise
        except Exception as exc:  # noqa: BLE001 -- provider boundary; shut down on failure
            logger.error("DBS startup speech failed (%s)", type(exc).__name__)
            self.session.shutdown(drain=False)
            return
        self.tasks = [asyncio.create_task(self._consume()), asyncio.create_task(self._idle())]
        logger.info("DBS ready: language=%s lesson=01.001.001 bible=%s", self.config["language"], self.controller.lesson.bible)

    async def on_exit(self):
        if self.active_turn:
            self.active_turn.cancel()
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.active_turn:
            await asyncio.gather(self.active_turn, return_exceptions=True)
        self.controller.flow.roster.clear()
        self.controller.flow.pending = None
        self.durations.clear()
        self.resume_prompts.clear()
        await self.controller.parser.close()

    async def stt_node(self, audio, model_settings):
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if event.type == stt.SpeechEventType.START_OF_SPEECH:
                self.human_speaking = True
                self.nudged = False
            if event.type == stt.SpeechEventType.END_OF_SPEECH:
                self.human_speaking = False
            if event.type in (stt.SpeechEventType.INTERIM_TRANSCRIPT, stt.SpeechEventType.FINAL_TRANSCRIPT):
                self.last_activity = time.monotonic()
                self.nudged = False
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and not self.busy:
                for alternative in event.alternatives:
                    sid = alternative.speaker_id or "UU"
                    self.durations[sid] = self.durations.get(sid, 0) + max(0, alternative.end_time - alternative.start_time)
            if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                text = event.alternatives[0].text
                intent, _ = rule_intent(text, self.controller.flow.phase)
                if intent in (Intent.STOP, Intent.PAUSE, Intent.SAFETY):
                    # LiveKit skips turn hooks during noninterruptible speech.
                    # Handle explicit controls here, before that gate, without an LLM.
                    self._preempt(text)
                    continue
            yield event

    def _preempt(self, text: str):
        if self.last_prompts:
            self.resume_prompts = list(self.last_prompts)
        if self.active_turn and not self.active_turn.done():
            self.active_turn.cancel()
        if self.speech_handle:
            self.speech_handle.interrupt(force=True)
        # A yes queued before pause/stop must not advance the resumed lesson.
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.queue.put_nowait(text)

    async def on_user_turn_completed(self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage):
        text = (new_message.text_content or "").strip()
        self.last_activity = time.monotonic()
        self.nudged = False
        if text:
            if rule_intent(text, self.controller.flow.phase)[0] in (Intent.STOP, Intent.PAUSE, Intent.SAFETY):
                # Already handled from the final STT event, including any interim
                # copy retained in LiveKit's end-of-turn buffer.
                raise StopResponse()
            try:
                self.queue.put_nowait(text)
            except asyncio.QueueFull:
                logger.warning("Input queue full; dropping turn without changing lesson state")
        raise StopResponse()

    async def _say(self, prompts: list[Prompt]):
        if not prompts:
            return
        self.busy = True
        self.last_prompts = prompts
        try:
            for prompt in prompts:
                text = self.controller.render(prompt)
                logger.info("DBS prompt: %s", prompt.key)
                # No generic answers, generated summaries, or SKIP can reach TTS.
                frames = await speech_frames(text, self.config, self.voice)
                handle = self.session.say(text, audio=frame_stream(frames), allow_interruptions=False, add_to_chat_ctx=False)
                self.speech_handle = handle
                try:
                    await handle.wait_for_playout()
                    if handle.interrupted:
                        raise asyncio.CancelledError()
                except asyncio.CancelledError:
                    handle.interrupt(force=True)
                    raise
                finally:
                    self.speech_handle = None
            self.last_prompts = []
        except asyncio.CancelledError:
            if self.speech_handle:
                self.speech_handle.interrupt(force=True)
            raise
        finally:
            self.last_activity = time.monotonic()
            self.busy = False

    async def _process(self, text: str | None):
        previous = copy.deepcopy(self.controller.flow)
        self.busy = True
        self.last_prompts = []
        try:
            if text is None:
                # Idle work shares the sole consumer and is rechecked for staleness.
                if self.human_speaking or not self.queue.empty() or time.monotonic() - self.last_activity < 25:
                    return
                prompts = self.controller.flow.idle()
            else:
                ids = ()
                speaker, solo = speaker_info(text)
                urgent = rule_intent(text, self.controller.flow.phase)[0] in (Intent.STOP, Intent.PAUSE, Intent.SAFETY)
                if not urgent and self.controller.flow.phase == "introductions" and speaker and solo and self.durations.get(speaker, 0) >= 5:
                    ids = identifiers_for(await self.recognizer.get_speaker_ids(), speaker)
                prompts = await self.controller.accept(text, identifiers=ids)
                if any(prompt.key == "resumed" for prompt in prompts):
                    prompts += self.resume_prompts
                    self.resume_prompts = []
            await self._say(prompts)
        except Exception as exc:  # noqa: BLE001 -- fail-closed boundary; log no participant content
            self.controller.flow = previous
            logger.error("DBS processing failed (%s); no model prose will be spoken", type(exc).__name__)
            try:
                await self._say([Prompt("error")])
            except Exception:
                self.session.shutdown(drain=False)
                raise

    async def _consume(self):
        while True:
            text = await self.queue.get()
            self.active_turn = asyncio.create_task(self._process(text))
            try:
                await self.active_turn
            except asyncio.CancelledError:
                if (task := asyncio.current_task()) and task.cancelling():
                    raise
            finally:
                self.active_turn = None
                self.busy = False
                self.last_activity = time.monotonic()
                self.queue.task_done()
            if self.controller.flow.phase == "done":
                self.controller.flow.roster.clear()
                self.controller.flow.pending = None
                self.session.shutdown()
                return

    async def _idle(self):
        # One invitation after a lull, never automatic lesson advancement.
        while True:
            await asyncio.sleep(0.5)
            if self.busy or self.human_speaking or self.nudged or not self.queue.empty():
                continue
            if time.monotonic() - self.last_activity >= 25:
                self.nudged = True
                self.queue.put_nowait(None)


async def build_controller(*, text_mode: bool = False):
    config = settings()
    lesson = configured_lesson(config)
    parser = IntentParser()
    try:
        if parser.provider == "rules" and config["language"] not in ("en", "es"):
            raise ValueError("Fast rules mode supports English/Spanish only; choose DBS_LLM_PROVIDER=ollama or openrouter for this language")
        prompts = await parser.prompts(config["language"], lesson.language_name)
    except BaseException:
        await parser.close()
        raise
    return config, DBSController(config, lesson, parser, prompts, text_mode=text_mode)


async def entrypoint(ctx):
    config, controller = await build_controller()
    await ctx.connect()
    recognizer = make_stt(config)
    options = {"stt": recognizer, "vad": silero.VAD.load(), "min_endpointing_delay": 1.0, "max_endpointing_delay": 6.0,
               "discard_audio_if_uninterruptible": False}
    session = AgentSession(**options)
    await session.start(agent=DiscoveryAgent(controller, recognizer, config), room=ctx.room)


async def rehearse():
    _, controller = await build_controller(text_mode=True)
    print("TEXT REHEARSAL — no microphone, no voice enrollment. Ctrl-D exits.")
    print(f"Lesson: 01.001.001 | {controller.lesson.language_name} | {controller.lesson.bible}")
    if controller.lesson.scripture_url:
        print("Scripture source:", controller.lesson.scripture_url)
    try:
        for prompt in controller.flow.start():
            print("William:", controller.render(prompt))
        while controller.flow.phase != "done":
            try:
                text = await asyncio.to_thread(input, "You: ")
            except EOFError:
                break
            if not text.strip():
                continue
            try:
                prompts = await controller.accept(text)
            except Exception as exc:  # noqa: BLE001 -- interactive provider boundary
                print(f"Parser error: {type(exc).__name__}", file=sys.stderr)
                prompts = [Prompt("error")]
            for prompt in prompts:
                print("William:", controller.render(prompt))
    finally:
        controller.flow.roster.clear()
        controller.flow.pending = None
        await controller.parser.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) > 1 and sys.argv[1] == "list-languages":
        for code, info in language_catalog().items():
            print(f"{code:16} {info['language_description']}")
        raise SystemExit(0)
    if len(sys.argv) > 1 and sys.argv[1] == "rehearse":
        try:
            asyncio.run(rehearse())
        except KeyboardInterrupt:
            pass
        raise SystemExit(0)
    if "--text" in sys.argv:
        raise SystemExit("Use `python dbs_agent.py rehearse`; LiveKit --text bypasses voice turn hooks.")
    agents.cli.run_app(agents.WorkerOptions(entrypoint_fnc=entrypoint))
