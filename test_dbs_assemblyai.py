import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from aiohttp import WSMsgType

from dbs_assemblyai import (
    AssemblyStream,
    ProviderError,
    connection_params,
    read_worktree_key,
)


class FakeSocket:
    def __init__(self):
        self.closed = False
        self.incoming = asyncio.Queue()
        self.incoming.put_nowait(
            SimpleNamespace(
                type=WSMsgType.TEXT,
                data=json.dumps(
                    {
                        "type": "Begin",
                        "configuration": {
                            "model": "universal-3-6-pro",
                            "speaker_labels": True,
                        },
                    }
                ),
            )
        )
        self.commands = []
        self.audio = []

    async def receive(self):
        return await self.incoming.get()

    async def send_json(self, data):
        self.commands.append(data)
        if data.get("type") == "Terminate":
            for message in (
                {"type": "SpeakerRevision", "revisions": []},
                {
                    "type": "Termination",
                    "audio_duration_seconds": 0.1,
                    "session_duration_seconds": 0.2,
                },
            ):
                self.incoming.put_nowait(
                    SimpleNamespace(type=WSMsgType.TEXT, data=json.dumps(message))
                )

    async def send_bytes(self, data):
        self.audio.append(data)

    async def close(self):
        self.closed = True


class StreamTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.socket = FakeSocket()
        self.http = SimpleNamespace(
            ws_connect=AsyncMock(return_value=self.socket), close=AsyncMock()
        )
        self.provider = AssemblyStream(
            "test-secret", client_factory=lambda **kw: self.http
        )
        self.stop = asyncio.Event()
        self.events = []

    async def message(self, data):
        self.events.append(data)
        if data["type"] == "Begin":
            await self.provider.send_audio(bytes(3200))
            self.stop.set()

    async def test_authenticated_single_stream_and_termination_revisions_are_drained(
        self,
    ):
        await self.provider.run(self.message, self.stop)
        self.http.ws_connect.assert_awaited_once()
        url = self.http.ws_connect.call_args.args[0]
        options = self.http.ws_connect.call_args.kwargs
        self.assertNotIn("test-secret", url)
        self.assertEqual(options["headers"], {"Authorization": "test-secret"})
        self.assertEqual(options["params"]["speech_model"], "universal-3-6-pro")
        self.assertEqual(json.loads(options["params"]["language_codes"]), ["en", "tr"])
        self.assertEqual(self.socket.audio, [bytes(3200)])
        self.assertEqual(self.socket.commands, [{"type": "Terminate"}])
        self.assertEqual(
            [e["type"] for e in self.events],
            ["Begin", "SpeakerRevision", "Termination"],
        )
        self.assertTrue(self.socket.closed)
        self.http.close.assert_awaited_once()
        self.assertIsNone(self.provider.ws)

    async def test_callback_failure_still_terminates_and_closes(self):
        async def fail(_):
            raise RuntimeError("private exception fixture")

        with self.assertRaises(RuntimeError):
            await self.provider.run(fail, self.stop)
        self.assertEqual(self.socket.commands, [{"type": "Terminate"}])
        self.assertTrue(self.socket.closed)

    async def test_cancellation_and_deadline_terminate(self):
        ready = asyncio.Event()

        async def receive(_):
            ready.set()

        task = asyncio.create_task(self.provider.run(receive, self.stop))
        await asyncio.wait_for(ready.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.socket.commands, [{"type": "Terminate"}])
        self.assertTrue(self.socket.closed)

    async def test_stop_before_start_never_opens_billed_connection(self):
        self.stop.set()
        await self.provider.run(self.message, self.stop)
        self.http.ws_connect.assert_not_called()

    async def test_stop_during_begin_closes_without_waiting_for_begin_timeout(self):
        self.socket.incoming.get_nowait()
        task = asyncio.create_task(self.provider.run(self.message, self.stop))
        for _ in range(50):
            if self.provider.ws:
                break
            await asyncio.sleep(0)
        self.stop.set()
        await asyncio.wait_for(task, 0.5)
        self.assertEqual(self.socket.commands, [{"type": "Terminate"}])
        self.assertTrue(self.socket.closed)

    async def test_lifetime_deadline_terminates_bounded_stream(self):
        await self.provider.run(AsyncMock(), self.stop, max_seconds=0.01)
        self.assertTrue(self.socket.closed)
        self.assertEqual(self.socket.commands, [{"type": "Terminate"}])

    async def test_blocked_begin_callback_is_bounded_and_terminated(self):
        blocked = asyncio.Event()

        async def callback(_):
            await blocked.wait()

        started = asyncio.get_running_loop().time()
        with self.assertRaises(TimeoutError):
            await asyncio.wait_for(
                self.provider.run(
                    callback, self.stop, max_seconds=0.02, drain_seconds=0.01
                ),
                0.4,
            )
        self.assertLess(asyncio.get_running_loop().time() - started, 0.2)
        self.assertEqual(self.socket.commands, [{"type": "Terminate"}])
        self.assertTrue(self.socket.closed)

    async def test_wrong_provider_model_fails_closed(self):
        self.socket.incoming.get_nowait()
        self.socket.incoming.put_nowait(
            SimpleNamespace(
                type=WSMsgType.TEXT,
                data=json.dumps(
                    {
                        "type": "Begin",
                        "configuration": {
                            "model": "older-model",
                            "speaker_labels": True,
                        },
                    }
                ),
            )
        )
        with self.assertRaises(ProviderError):
            await self.provider.run(self.message, self.stop)
        self.assertTrue(self.socket.closed)

    async def test_invalid_pcm_and_playback_gate(self):
        self.provider.ws = self.socket
        self.provider.ready = True
        for data in (b"x", b"", bytes(6402)):
            with self.assertRaises(ValueError):
                await self.provider.send_audio(data)
        await self.provider.send_audio(b"\x01\x01" * 1600, playback=True)
        self.assertEqual(self.socket.audio, [bytes(3200)])

    def test_audio_and_language_parameters_are_explicit(self):
        params = connection_params(("en", "tr"))
        self.assertEqual(params["encoding"], "pcm_s16le")
        self.assertEqual(params["sample_rate"], "16000")
        self.assertEqual(params["language_detection"], "true")
        self.assertEqual(params["speaker_labels"], "true")
        self.assertEqual(params["inactivity_timeout"], "5")


class CredentialTests(unittest.TestCase):
    def test_only_worktree_owner_only_file_is_loaded(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory) / ".env"
            p.write_text("ASSEMBLYAI_API_KEY=test-only\n")
            p.chmod(0o600)
            self.assertEqual(read_worktree_key(p), "test-only")
            p.chmod(0o644)
            with self.assertRaises(ValueError):
                read_worktree_key(p)
            p.chmod(0o600)
            p.write_text("ASSEMBLYAI_API_KEY=\n")
            with self.assertRaisesRegex(ValueError, "ASSEMBLYAI_API_KEY"):
                read_worktree_key(p)


if __name__ == "__main__":
    unittest.main()
