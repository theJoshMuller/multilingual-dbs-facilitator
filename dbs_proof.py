"""Bounded one-mic ASR-only session; no Scripture, translation or identity claim."""

import asyncio
import time

from dbs_assemblyai import AssemblyStream, read_worktree_key
from dbs_mixed_turns import TurnLedger


class ProofSession:
    def __init__(self, ws, *, provider=None):
        self.ws = ws
        self.provider = provider or AssemblyStream(read_worktree_key())
        self.ledger = TurnLedger()
        self.closed = asyncio.Event()
        self.started = self.last_input = self.budget_at = time.monotonic()
        self.budget = 64000
        self.received_bytes = 0
        self.seq = 0
        self.send_lock = asyncio.Lock()
        self.played = None
        self.play_id = ""

    async def emit(self, kind, **values):
        if not self.ws.closed:
            async with self.send_lock:
                self.seq += 1
                try:
                    await self.ws.send_json(
                        {
                            "type": kind,
                            "seq": self.seq,
                            "at": round(time.monotonic() - self.started, 3),
                            **values,
                        }
                    )
                except ConnectionError:
                    # The peer may close between the check and the write.
                    self.closed.set()

    async def fail(self, code, message):
        self.closed.set()
        await self.emit("error", code=code, message=message, fatal=True)

    async def state(self):
        await self.emit(
            "state",
            mode="assemblyai-proof",
            phase="asr_proof",
            question_key="ASR ONLY",
            current_question="One microphone · separate EN/TR turns · include a within-sentence switch. Human identity is UNVERIFIED.",
            index=-1,
            steps=[],
            language="en,tr",
            bible="",
            scripture_mode="unavailable_in_asr_proof",
            parser="none",
            busy=False,
            paused=False,
            listen=self.provider.ready and not self.closed.is_set(),
            roster=[],
            pending=None,
            queue_depth=0,
            human_test_verified=False,
        )

    async def on_provider(self, message):
        kind = message["type"]
        if kind == "Begin":
            await self.state()
            await self.emit(
                "status",
                stage="listening",
                message="AssemblyAI Universal-3.6 Pro · one EN/TR stream · human identity UNVERIFIED · three-minute limit.",
            )
        elif kind == "Termination":
            await self.emit(
                "provider_termination",
                audio_duration_seconds=message.get("audio_duration_seconds"),
                session_duration_seconds=message.get("session_duration_seconds"),
            )
        else:
            for event in self.ledger.accept(message):
                kind = event.pop("type")
                await self.emit(kind, **event)

    async def feed_audio(self, data):
        if self.closed.is_set():
            return
        if not data or len(data) % 2 or len(data) > 6400:
            await self.fail(
                "invalid_audio",
                "Expected mono 16 kHz int16 PCM; maximum packet 200 ms.",
            )
            return
        now = time.monotonic()
        self.budget = min(64000, self.budget + (now - self.budget_at) * 40000) - len(
            data
        )
        self.budget_at = self.last_input = now
        if self.budget < 0:
            await self.fail("audio_rate", "Audio arrived faster than realtime.")
            return
        self.received_bytes += len(data)
        try:
            await self.provider.send_audio(data)
        except Exception:  # noqa: BLE001 -- provider boundary; do not expose authenticated exceptions
            await self.fail(
                "provider_audio",
                "AssemblyAI audio transport failed; stream is stopping.",
            )

    async def handle_control(self, action):
        if action == "stop":
            self.closed.set()
        else:
            await self.emit(
                "error",
                code="asr_only",
                message="This proof transcribes only. Use Mute or Stop; study controls belong to the original mode.",
                fatal=False,
            )

    async def check_limits(self):
        now = time.monotonic()
        if now - self.last_input > 10:
            await self.fail(
                "microphone_stalled",
                "No microphone packets for ten seconds; stream is stopping.",
            )
        elif now - self.started > 180:
            self.closed.set()
        await self.emit(
            "metrics",
            received_bytes=self.received_bytes,
            input_seconds=self.received_bytes / 32000,
            queue_depth=0,
        )

    async def monitor(self):
        while not self.closed.is_set():
            await asyncio.sleep(1)
            await self.check_limits()

    async def run(self):
        monitor = asyncio.create_task(self.monitor())
        try:
            await self.emit(
                "status",
                stage="connecting_stt",
                message="Opening one bounded server-only AssemblyAI stream.",
            )
            await self.provider.run(self.on_provider, self.closed, max_seconds=180)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- provider boundary; sanitized browser error only
            await self.fail(
                "assemblyai_failed",
                "AssemblyAI stream failed. Check worktree credentials/account/connectivity; no older-model fallback was used.",
            )
        finally:
            self.closed.set()
            monitor.cancel()
            await asyncio.gather(monitor, return_exceptions=True)
            self.ledger.clear()
            if not self.ws.closed:
                await self.emit(
                    "ended",
                    message="AssemblyAI stream closed. Human diarization remains UNVERIFIED until the real one-mic test is assessed. Clear the page to remove speech.",
                )
                await self.ws.close()
