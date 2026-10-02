"""Server-only AssemblyAI stream. Never log authenticated provider exceptions."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import time
from pathlib import Path

import aiohttp
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ENDPOINT = "wss://streaming.assemblyai.com/v3/ws"
MODEL = "universal-3-6-pro"


class ProviderError(Exception):
    pass


class _Stopped(Exception):
    pass


def read_worktree_key(path=ROOT / ".env"):
    if path.is_symlink() or not path.is_file():
        raise ValueError("Missing regular worktree .env with ASSEMBLYAI_API_KEY")
    info = path.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise ValueError("Worktree .env must be owner-only (0600)")
    value = dotenv_values(path, interpolate=False).get("ASSEMBLYAI_API_KEY")
    if not value or any(c.isspace() for c in value):
        raise ValueError("Missing or invalid ASSEMBLYAI_API_KEY in worktree .env")
    return value


def connection_params(languages=("en", "tr")):
    languages = tuple(dict.fromkeys(languages))
    if not languages or any(code not in ("en", "tr", "es") for code in languages):
        raise ValueError("Select supported proof languages")
    return {
        "speech_model": MODEL,
        "encoding": "pcm_s16le",
        "sample_rate": "16000",
        "speaker_labels": "true",
        "language_detection": "true",
        "language_codes": json.dumps(languages),
        "max_speakers": "6",
        "speaker_labels_revision_interval_ms": "120000",
        "inactivity_timeout": "5",
    }


class AssemblyStream:
    def __init__(
        self, key, languages=("en", "tr"), *, client_factory=aiohttp.ClientSession
    ):
        self._key = key
        self.params = connection_params(languages)
        self.client_factory = client_factory
        self.ws = None
        self.ready = False
        self.terminated = False
        self.termination = None

    async def send_audio(self, pcm, *, playback=False):
        if not pcm or len(pcm) % 2 or len(pcm) > 6400:
            raise ValueError("Expected 16 kHz mono int16 PCM; maximum packet 200 ms")
        if self.ws is not None and self.ready and not self.ws.closed:
            await self.ws.send_bytes(bytes(len(pcm)) if playback else pcm)

    async def _receive(self, callback):
        while self.ws is not None and not self.ws.closed:
            message = await self.ws.receive()
            if message.type != aiohttp.WSMsgType.TEXT:
                break
            data = json.loads(message.data)
            if data.get("type") == "Termination":
                self.terminated = True
                self.termination = {
                    field: data.get(field)
                    for field in ("audio_duration_seconds", "session_duration_seconds")
                }
            if data.get("type") in ("Turn", "SpeakerRevision", "Termination"):
                await callback(data)
            if self.terminated:
                return
            if data.get("type") == "Error" or data.get("error"):
                raise ProviderError("AssemblyAI returned a provider error")
        if not self.terminated:
            raise ProviderError("AssemblyAI disconnected before Termination")

    async def _startup(self, operation, stop, deadline):
        task = asyncio.ensure_future(operation)
        stopper = asyncio.create_task(stop.wait())
        try:
            done, _ = await asyncio.wait(
                (task, stopper),
                timeout=max(0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if task in done:
                return await task
            if stopper in done:
                raise _Stopped()
            raise TimeoutError("AssemblyAI startup deadline")
        finally:
            task.cancel()
            stopper.cancel()
            await asyncio.gather(task, stopper, return_exceptions=True)

    async def run(self, callback, stop, *, max_seconds=180, drain_seconds=3):
        if stop.is_set():
            return
        deadline = time.monotonic() + max_seconds
        # Redirects must never forward the permanent key to another endpoint.
        trace = aiohttp.TraceConfig()

        async def reject_redirect(*_):
            raise ProviderError("Provider redirect rejected")

        trace.on_request_redirect.append(reject_redirect)
        http = self.client_factory(
            trace_configs=[trace],
            timeout=aiohttp.ClientTimeout(total=None, sock_connect=10),
        )
        reader = stopper = None
        try:

            async def connect():
                self.ws = await http.ws_connect(
                    ENDPOINT,
                    params=self.params,
                    headers={"Authorization": self._key},
                    timeout=aiohttp.ClientWSTimeout(ws_close=2),
                    max_msg_size=65536,
                )

            await self._startup(connect(), stop, min(deadline, time.monotonic() + 10))
            if stop.is_set():
                return
            first = await self._startup(
                self.ws.receive(), stop, min(deadline, time.monotonic() + 15)
            )
            if first.type != aiohttp.WSMsgType.TEXT:
                raise ProviderError("AssemblyAI did not begin a stream")
            begin = json.loads(first.data)
            config = begin.get("configuration", {})
            if (
                begin.get("type") != "Begin"
                or config.get("model") != MODEL
                or config.get("speaker_labels") is not True
            ):
                raise ProviderError(
                    "AssemblyAI did not confirm requested model and diarization"
                )
            self.ready = True
            reader = asyncio.create_task(self._receive(callback))
            await self._startup(callback(begin), stop, deadline)
            stopper = asyncio.create_task(stop.wait())
            done, _ = await asyncio.wait(
                (reader, stopper),
                timeout=max(0, deadline - time.monotonic()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if reader in done:
                await reader
        except _Stopped:
            pass
        finally:
            self.ready = False
            if stopper:
                stopper.cancel()
            try:
                if self.ws is not None and not self.ws.closed and not self.terminated:
                    await asyncio.wait_for(self.ws.send_json({"type": "Terminate"}), 1)
                if reader is not None and not reader.done():
                    await asyncio.wait_for(asyncio.shield(reader), drain_seconds)
            except (Exception, asyncio.CancelledError):  # noqa: BLE001, S110 -- authenticated cleanup errors must not be logged
                # Cleanup is bounded even after malformed provider messages/network loss.
                pass
            finally:
                for task in (reader, stopper):
                    if task is not None:
                        task.cancel()
                await asyncio.gather(
                    *(t for t in (reader, stopper) if t is not None),
                    return_exceptions=True,
                )
                if self.ws is not None:
                    await self.ws.close()
                self.ws = None
                await http.close()
