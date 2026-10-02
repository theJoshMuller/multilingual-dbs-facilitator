"""Offline browser QA with a synthetic device and intercepted WebSocket.

Never uses the host microphone or calls a speech/model provider.
"""

import argparse
import asyncio
import json
import os
import secrets
from contextlib import contextmanager
from pathlib import Path

from playwright.async_api import async_playwright, expect


@contextmanager
def browser_tmp():
    """Keep artifacts in the worktree while fitting Unix socket pathname limits."""
    target = Path(__file__).resolve().parent / ".cache/browser"
    target.mkdir(parents=True, mode=0o700, exist_ok=True)
    alias = Path("/tmp") / ("dbs-qa-" + secrets.token_hex(6))
    alias.symlink_to(target, target_is_directory=True)
    previous = os.environ.get("TMPDIR")
    os.environ["TMPDIR"] = str(alias)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("TMPDIR", None)
        else:
            os.environ["TMPDIR"] = previous
        alias.unlink()


async def smoke(url):
    frames = []
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            executable_path="/usr/bin/chromium",
            headless=True,
            args=[
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ],
        )
        context = await browser.new_context(permissions=["microphone"])
        page = await context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        await page.add_init_script("""
            window.__proofTracks = [];
            const capture = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = async (...args) => {
              const stream = await capture(...args);
              window.__proofTracks.push(...stream.getTracks());
              return stream;
            };
        """)

        def bridge(route):
            def send(value):
                route.send(json.dumps(value))

            def receive(message):
                if isinstance(message, bytes):
                    frames.append(len(message))
                    return
                data = json.loads(message)
                if data["type"] == "start":
                    assert (
                        data["mode"] == "assemblyai-proof" and data["consent"] is True
                    )
                    send(
                        {
                            "type": "state",
                            "mode": "assemblyai-proof",
                            "listen": True,
                            "phase": "asr_proof",
                            "busy": False,
                            "steps": [],
                            "index": -1,
                            "roster": [],
                            "pending": None,
                            "current_question": "Synthetic fixture; no facilitation.",
                        }
                    )
                    send(
                        {
                            "type": "status",
                            "stage": "listening",
                            "message": "Synthetic fixture.",
                        }
                    )
                    first = {
                        "type": "transcript",
                        "turn_order": 0,
                        "text": "Synthetic EN/TR thought <script>window.__injected=1</script>",
                        "final": True,
                        "speaker_label": "A",
                        "language_code": "en",
                        "language_confidence": 0.95,
                        "verified_name": None,
                        "speakers": [{"speaker": "A", "text": "Synthetic fixture"}],
                    }
                    send({**first, "final": False})
                    send(first)
                    send(first)
                    send(
                        {
                            **first,
                            "turn_order": 1,
                            "speaker_label": "PENDING",
                            "language_code": "tr",
                            "speakers": [
                                {"speaker": "PENDING", "text": "Synthetic fixture"}
                            ],
                        }
                    )
                elif data["type"] == "control" and data["action"] == "stop":
                    send(
                        {
                            "type": "transcript",
                            "turn_order": 0,
                            "text": "Synthetic EN/TR thought",
                            "final": True,
                            "revision": True,
                            "speaker_label": "B",
                            "language_code": "en",
                            "verified_name": None,
                            "speakers": [{"speaker": "B", "text": "Synthetic fixture"}],
                        }
                    )
                    send(
                        {
                            "type": "provider_termination",
                            "audio_duration_seconds": 1,
                            "session_duration_seconds": 1,
                        }
                    )
                    send({"type": "ended", "message": "Synthetic fixture ended."})

            route.on_message(receive)
            send({"type": "hello", "protocol": 1})

        await page.route_web_socket("**/ws", bridge)
        try:
            await page.goto(url, wait_until="networkidle")
            assert await page.locator("#start").is_disabled()
            assert await page.evaluate("window.__proofTracks.length") == 0
            await page.locator("#consent").check()
            await page.locator("#start").click()
            await expect(page.locator("#connection")).to_have_text("listening")
            await expect(page.locator("#transcript article")).to_have_count(2)
            assert await page.evaluate("window.__injected") is None
            assert (
                "Language code: tr"
                in await page.locator('[data-turn-order="1"]').inner_text()
            )
            assert "PENDING" in await page.locator('[data-turn-order="1"]').inner_text()
            await page.wait_for_timeout(350)
            assert frames and set(frames) == {3200}, frames
            await page.locator("#stop").click()
            await expect(page.locator("#connection")).to_have_text("offline")
            assert await page.evaluate(
                'window.__proofTracks.every(t=>t.readyState==="ended")'
            )
            revised = await page.locator('[data-turn-order="0"]').inner_text()
            assert "Speaker label: B" in revised and "SPEAKER REVISION" in revised
            assert (
                "Termination acknowledged" in await page.locator("#events").inner_text()
            )
            assert not errors, errors
            print(
                json.dumps(
                    {
                        "status": "passed",
                        "evidence": "offline synthetic device and WebSocket fixtures",
                        "pcm_frame_bytes": 3200,
                        "frames_observed": len(frames),
                        "final_dedup": True,
                        "pending_preserved": True,
                        "end_revision_received": True,
                        "microphone_released": True,
                        "provider_called": False,
                        "js_errors": errors,
                    }
                )
            )
        finally:
            await context.close()
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8095/dbs/")
    with browser_tmp():
        asyncio.run(smoke(parser.parse_args().url))
