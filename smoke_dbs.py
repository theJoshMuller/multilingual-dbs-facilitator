"""Live smoke test: synthesized audio -> Speechmatics plugin -> real identifier.

No microphone or human voice recording. Does use the Speechmatics API quota.
Run with --language en or es. No identifiers are logged or saved.
"""
import argparse
import asyncio
import json
from collections import Counter

from livekit import rtc
from livekit.agents import stt

from dbs_agent import DBSController, identifiers_for, make_stt, speech_frames
from dbs_curriculum import load_lesson
from dbs_intents import IntentParser
from dbs_prompts import EN, ES


async def smoke(language):
    config = {"language": language, "stt_language": language, "ceiling": 10, "people": 7,
              "tts": "elevenlabs"}
    sample = (
        "My name is Alex. I am a synthetic test voice for this demonstration. "
        "Since we last met, I am thankful for time with friends and walks in the park. "
        "I am here today to check that the voice system can hear this introduction clearly."
        if language == "en" else
        "Me llamo Alex. Soy una voz de prueba para esta demostración. "
        "Desde la última vez que nos reunimos, agradezco el tiempo con amigos y los paseos por el parque. "
        "Hoy estoy aquí para comprobar que el sistema de voz escucha esta presentación."
    )
    frames = await speech_frames(sample, config)
    recognizer = make_stt(config)
    transcripts = []
    events = Counter()
    ended = asyncio.Event()
    parser = IntentParser()
    controller = DBSController(config, load_lesson(language), parser, EN if language == "en" else ES)
    try:
        async with recognizer.stream() as stream:
            async def consume():
                async for event in stream:
                    events[event.type.value] += 1
                    if event.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                        transcripts.extend(a.text for a in event.alternatives)
                    if event.type == stt.SpeechEventType.END_OF_SPEECH and transcripts:
                        ended.set()
            reader = asyncio.create_task(consume())
            try:
                for frame in frames:
                    stream.push_frame(frame)
                    await asyncio.sleep(frame.duration)
                rate = frames[0].sample_rate
                samples = rate // 50
                for _ in range(150):
                    stream.push_frame(rtc.AudioFrame(data=bytes(samples * 2), sample_rate=rate, num_channels=1, samples_per_channel=samples))
                    await asyncio.sleep(0.02)
                try:
                    await asyncio.wait_for(ended.wait(), 30)
                except TimeoutError:
                    # Only synthesized test speech; never log participant audio/IDs.
                    print(json.dumps({"status": "timeout", "events": dict(events), "synthetic_transcripts": transcripts}), flush=True)
                    raise
                speakers = await recognizer.get_speaker_ids()
                # IDs must come from the live GetSpeakers response, not test fixtures.
                text = " ".join(transcripts)
                from dbs_agent import speaker_info
                label, solo = speaker_info(text)
                assert solo and label, "Synthetic single speaker was not diarized reliably"
                ids = identifiers_for(speakers, label)
                assert ids, "No real voice identifiers were returned"
                prompts = await controller.accept(text, identifiers=ids)
                assert prompts[0].key == "confirm_name", f"Enrollment did not accept real transcript: {prompts[0].key}"
                assert controller.flow.pending is not None
                assert controller.flow.pending.name == "Alex", "ASR did not preserve test speaker name"
                answer = "Yes." if language == "en" else "Sí."
                await controller.accept(f"[Speaker {label}] {answer}")
                assert len(controller.flow.roster) == 1
                commands = "William, everyone is here." if language == "en" else "William, ya estamos todos."
                await controller.accept(f"[Speaker {label}] {commands}")
                prompts = await controller.accept(f"[Speaker {label}] {answer}")
                assert prompts[-1].key == "f.002", "Opening thankfulness must not repeat after introductions"
                result = {
                    "status": "passed", "language": language,
                    "tts_frames": len(frames),
                    "tts_seconds": round(sum(f.duration for f in frames), 2),
                    "final_transcript_segments": len(transcripts),
                    "real_voice_identifier_count": len(ids),
                    "confirmed_roster_count": len(controller.flow.roster),
                    "first_question_after_onboarding": prompts[-1].key,
                    "bible": controller.lesson.bible,
                    "scripture_mode": "canonical_text" if controller.lesson.verses else "participant_reads_NVI",
                }
                print(json.dumps(result, indent=2), flush=True)
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)
    finally:
        controller.flow.roster.clear()
        controller.flow.pending = None
        await parser.close()
        await recognizer.aclose()


if __name__ == "__main__":
    args = argparse.ArgumentParser()
    args.add_argument("--language", choices=("en", "es"), default="en")
    asyncio.run(smoke(args.parse_args().language))
