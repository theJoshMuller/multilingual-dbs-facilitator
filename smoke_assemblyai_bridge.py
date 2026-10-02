"""Bounded real provider transport test using synthetic silence, never a microphone.

Start the worktree server first. This calls AssemblyAI through the existing bridge
and consumes a few seconds of connected provider time. It proves no diarization.
"""

import asyncio
import json
import time

import aiohttp

ORIGIN = "http://127.0.0.1:8095"


async def smoke():
    started = time.monotonic()
    turns = 0
    termination = None
    async with aiohttp.ClientSession() as http:
        async with http.ws_connect(
            ORIGIN + "/dbs/ws", headers={"Origin": ORIGIN}
        ) as ws:
            try:
                async with asyncio.timeout(20):
                    assert (await ws.receive_json())["type"] == "hello"
                    await ws.send_json(
                        {"type": "start", "mode": "assemblyai-proof", "consent": True}
                    )
                    while True:
                        event = await ws.receive_json()
                        if event["type"] == "error":
                            raise RuntimeError("Provider start failed")
                        if (
                            event["type"] == "status"
                            and event.get("stage") == "listening"
                        ):
                            break
                    for _ in range(30):
                        await ws.send_bytes(bytes(3200))
                        await asyncio.sleep(0.1)
                    await ws.send_json({"type": "control", "action": "stop"})
                    while True:
                        event = await ws.receive_json()
                        if event["type"] == "error":
                            raise RuntimeError("Provider stream failed")
                        if event["type"] == "transcript":
                            turns += 1  # Never print or retain any transcript.
                        if event["type"] == "provider_termination":
                            termination = {
                                "audio_seconds": event.get("audio_duration_seconds"),
                                "connected_seconds": event.get(
                                    "session_duration_seconds"
                                ),
                            }
                        if event["type"] == "ended":
                            break
                    assert termination, "Provider Termination not acknowledged"
            finally:
                if not ws.closed:
                    try:
                        await asyncio.wait_for(
                            ws.send_json({"type": "control", "action": "stop"}), 1
                        )
                    except ConnectionError:
                        pass
        async with http.get(ORIGIN + "/health") as response:
            remaining = (await response.json())["active_sessions"]
    print(
        json.dumps(
            {
                "status": "passed",
                "evidence": "real AssemblyAI through bridge; synthetic silence",
                "pcm_sample_rate": 16000,
                "pcm_channels": 1,
                "pcm_frame_bytes": 3200,
                "sent_audio_seconds": 3,
                "wall_seconds": round(time.monotonic() - started, 3),
                "termination": termination,
                "transcript_events": turns,
                "remaining_group_sessions": remaining,
                "human_diarization_proven": False,
            }
        )
    )


if __name__ == "__main__":
    try:
        asyncio.run(smoke())
    except Exception as error:  # noqa: BLE001 -- sanitized failure only
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
