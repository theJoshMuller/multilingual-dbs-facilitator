import unittest
from unittest.mock import AsyncMock, Mock

from dbs_proof import ProofSession


class ProofTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.ws = Mock(closed=False, send_json=AsyncMock(), close=AsyncMock())
        self.provider = Mock(ready=True, send_audio=AsyncMock(), run=AsyncMock())
        self.session = ProofSession(self.ws, provider=self.provider)

    async def test_invalid_audio_fails_closed_and_stop_discards_late_packets(self):
        await self.session.feed_audio(b"x")
        self.assertTrue(self.session.closed.is_set())
        await self.session.feed_audio(bytes(3200))
        self.provider.send_audio.assert_not_called()

    async def test_provider_failure_is_redacted_and_session_memory_cleared(self):
        self.provider.run.side_effect = RuntimeError(
            "private fixture credential/transcript"
        )
        await self.session.run()
        self.assertTrue(self.session.closed.is_set())
        self.assertEqual(self.session.ledger.turns, {})
        self.assertNotIn("private fixture", str(self.ws.send_json.call_args_list))

    async def test_audio_gate_and_stalled_mic(self):
        await self.session.feed_audio(bytes(3200))
        self.provider.send_audio.assert_awaited_once_with(bytes(3200))
        self.session.last_input -= 11
        await self.session.check_limits()
        self.assertTrue(self.session.closed.is_set())

    async def test_provider_language_and_speaker_are_separate_debug_fields(self):
        await self.session.on_provider(
            {
                "type": "Turn",
                "turn_order": 0,
                "transcript": "fixture",
                "speaker_label": "PENDING",
                "language_code": "tr",
                "end_of_turn": True,
                "words": [],
            }
        )
        data = self.ws.send_json.call_args.args[0]
        self.assertEqual(data["speaker_label"], "PENDING")
        self.assertEqual(data["language_code"], "tr")
        self.assertIsNone(data["verified_name"])

    async def test_stop_signals_provider_cleanup(self):
        await self.session.handle_control("stop")
        self.assertTrue(self.session.closed.is_set())

    async def test_browser_disconnect_during_emit_still_clears_and_closes(self):
        self.ws.send_json.side_effect = ConnectionResetError("private fixture")
        escaped = False
        try:
            await self.session.run()
        except ConnectionResetError:
            escaped = True
        self.assertFalse(escaped)
        self.assertTrue(self.session.closed.is_set())
        self.assertEqual(self.session.ledger.turns, {})
        self.ws.close.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
