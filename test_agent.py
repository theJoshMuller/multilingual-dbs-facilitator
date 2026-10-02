import asyncio
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import agent


class SpeakerProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.profile_path = Path(self.temporary_directory.name) / "profiles.json"

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_save_append_load_and_remove_profile(self) -> None:
        count = agent._save_enrollment(
            "Josh",
            ["identifier-one", "identifier-one"],
            path=self.profile_path,
        )
        self.assertEqual(count, 1)

        count = agent._save_enrollment(
            "Josh",
            ["identifier-two"],
            path=self.profile_path,
        )
        self.assertEqual(count, 2)

        known_speakers = agent.load_known_speakers(self.profile_path)
        self.assertEqual(len(known_speakers), 1)
        self.assertEqual(known_speakers[0].label, "Josh")
        self.assertEqual(
            known_speakers[0].speaker_identifiers,
            ["identifier-one", "identifier-two"],
        )
        self.assertEqual(stat.S_IMODE(self.profile_path.stat().st_mode), 0o600)

        self.assertTrue(agent._remove_speaker("Josh", self.profile_path))
        self.assertFalse(agent._remove_speaker("Josh", self.profile_path))
        self.assertEqual(agent.load_known_speakers(self.profile_path), [])

    def test_replace_discards_old_identifiers(self) -> None:
        agent._save_enrollment("Josh", ["old"], path=self.profile_path)
        count = agent._save_enrollment(
            "Josh",
            ["new"],
            replace=True,
            path=self.profile_path,
        )
        self.assertEqual(count, 1)
        self.assertEqual(
            agent.load_known_speakers(self.profile_path)[0].speaker_identifiers,
            ["new"],
        )

    def test_rejects_reserved_or_ambiguous_labels(self) -> None:
        for label in ("", " Josh", "Josh ", "UU", "S1", "s42"):
            with self.subTest(label=label), self.assertRaises(ValueError):
                agent._validate_speaker_label(label)

    def test_rejects_more_than_session_identifier_limit(self) -> None:
        identifiers = [f"identifier-{index}" for index in range(51)]
        with self.assertRaisesRegex(ValueError, "at most 50"):
            agent._save_enrollment("Josh", identifiers, path=self.profile_path)
        self.assertFalse(self.profile_path.exists())

    def test_rejects_profiles_from_another_model(self) -> None:
        self.profile_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "model": "standard",
                    "speakers": [
                        {
                            "label": "Josh",
                            "speaker_identifiers": ["identifier"],
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "re-enroll"):
            agent.load_known_speakers(self.profile_path)

    def test_extracts_identifiers_from_one_speaker(self) -> None:
        identifiers = agent._extract_enrollment_identifiers(
            [
                {
                    "message": "SpeakersResult",
                    "speakers": [
                        {
                            "label": "S1",
                            "speaker_identifiers": ["one", "two", "one"],
                        }
                    ],
                }
            ]
        )
        self.assertEqual(identifiers, ["one", "two"])

    def test_rejects_empty_or_multi_speaker_enrollment(self) -> None:
        with self.assertRaisesRegex(ValueError, "No voice identifier"):
            agent._extract_enrollment_identifiers([])

        with self.assertRaisesRegex(ValueError, "detected 2 speakers"):
            agent._extract_enrollment_identifiers(
                [
                    {
                        "speakers": [
                            {"label": "S1", "speaker_identifiers": ["one"]},
                            {"label": "S2", "speaker_identifiers": ["two"]},
                        ]
                    }
                ]
            )

    def test_enrollment_streams_audio_and_requests_final_identifiers(self) -> None:
        class FakeClient:
            instance = None

            def __init__(self, *, api_key: str) -> None:
                self.api_key = api_key
                self.handlers = {}
                self.messages = []
                self.audio = []
                self.start_options = None
                self.closed = False
                FakeClient.instance = self

            def on(self, event, callback) -> None:
                self.handlers[event] = callback

            async def start_session(self, **options) -> None:
                self.start_options = options

            async def send_message(self, message) -> None:
                self.messages.append(message)

            async def send_audio(self, audio: bytes) -> None:
                self.audio.append(audio)

            async def stop_session(self) -> None:
                self.handlers[agent.ServerMessageType.ADD_TRANSCRIPT](
                    {"results": [{"alternatives": [{"content": "hello"}]}]}
                )
                self.handlers[agent.ServerMessageType.SPEAKERS_RESULT](
                    {
                        "speakers": [
                            {
                                "label": "S1",
                                "speaker_identifiers": ["identifier"],
                            }
                        ]
                    }
                )

            async def close(self) -> None:
                self.closed = True

        class FakeMicrophone:
            def __enter__(self):
                return self

            def __exit__(self, *args) -> None:
                return None

            def read(self, frames: int):
                return bytes(frames * 2), False

        with (
            mock.patch.object(agent, "SpeechmaticsClient", FakeClient),
            mock.patch.object(
                agent.sd,
                "RawInputStream",
                return_value=FakeMicrophone(),
            ),
            mock.patch.object(agent.asyncio, "sleep", new=mock.AsyncMock()),
            mock.patch.dict(
                agent.os.environ,
                {"SPEECHMATICS_API_KEY": "test-key"},
            ),
        ):
            identifiers = asyncio.run(agent.enroll_speaker("Josh", seconds=5))

        client = FakeClient.instance
        assert client is not None
        assert client.start_options is not None
        self.assertEqual(identifiers, ["identifier"])
        self.assertTrue(client.closed)
        self.assertEqual(len(client.audio), 50)
        self.assertEqual(len(client.audio[0]), 3200)
        self.assertEqual(
            client.messages,
            [{"message": agent.ClientMessageType.GET_SPEAKERS, "final": True}],
        )
        self.assertEqual(
            client.start_options["transcription_config"].model,
            agent.Model.ENHANCED,
        )
        self.assertEqual(
            client.start_options["transcription_config"].diarization,
            "speaker",
        )
        self.assertIsNone(
            client.start_options["transcription_config"].speaker_diarization_config
        )

    def test_profile_temporary_file_is_private_at_creation(self) -> None:
        real_mkstemp = tempfile.mkstemp
        modes_at_creation = []

        def observing_mkstemp(*args, **kwargs):
            descriptor, name = real_mkstemp(*args, **kwargs)
            modes_at_creation.append(stat.S_IMODE(os.stat(name).st_mode))
            return descriptor, name

        with mock.patch.object(
            agent.tempfile,
            "mkstemp",
            side_effect=observing_mkstemp,
        ):
            agent._save_enrollment(
                "Josh",
                ["identifier"],
                path=self.profile_path,
            )

        self.assertEqual(modes_at_creation, [0o600])


if __name__ == "__main__":
    unittest.main()
