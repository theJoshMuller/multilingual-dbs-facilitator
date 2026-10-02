"""Offline contract tests; synthetic PCM here is only a test fixture."""
import json
import os
import unittest
from unittest.mock import patch

import httpx

from dbs_agent import settings, speech_frames
from dbs_tts import (
    DEFAULT_VOICE_ID,
    MODEL_ID,
    api_key,
    synthesize,
    tts_language,
    voice_id,
)


class TTSConfigurationTests(unittest.TestCase):
    def test_elevenlabs_is_default_for_both_languages(self):
        for language in ('en', 'es'):
            with patch.dict(os.environ, {'DBS_LANGUAGE': language}, clear=True):
                self.assertEqual(settings()['tts'], 'elevenlabs')
                self.assertEqual(voice_id(language), DEFAULT_VOICE_ID)

    def test_robotic_provider_is_rejected(self):
        with patch.dict(os.environ, {'DBS_TTS_PROVIDER': 'espeak'}, clear=True), self.assertRaisesRegex(ValueError, 'elevenlabs'):
            settings()

    def test_unsupported_output_locale_and_alias(self):
        self.assertEqual(tts_language('cmn'), 'zh')
        with self.assertRaisesRegex(ValueError, 'does not support'):
            tts_language('sw')

    def test_per_language_voice_override(self):
        with patch.dict(os.environ, {'ELEVENLABS_VOICE_ID_ES': 'SpanishTestVoice'}, clear=True):
            self.assertEqual(voice_id('es'), 'SpanishTestVoice')
            self.assertEqual(voice_id('en'), DEFAULT_VOICE_ID)

    def test_missing_key_fails_without_other_provider(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch('dbs_tts.dotenv_values', return_value={}),
            patch('dbs_tts.Path.is_file', return_value=False),
            self.assertRaisesRegex(ValueError, 'ELEVENLABS_API_KEY'),
        ):
            api_key()


class TTSContractTests(unittest.IsolatedAsyncioTestCase):
    request_seen: httpx.Request

    async def request(self, response, text='Hola, ¿quiénes nos acompañan hoy?'):
        def handler(request):
            self.request_seen = request
            return response
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch('dbs_tts.api_key', return_value='unit-test-only'), patch('dbs_tts.httpx.AsyncClient', return_value=client):
            return await synthesize(text, 'es')

    async def test_flash_payload_and_lossless_pcm(self):
        pcm = b'\x01\x02' * 655
        frames = await self.request(httpx.Response(200, content=pcm, headers={'content-type': 'audio/pcm'}))
        payload = json.loads(self.request_seen.content)
        self.assertEqual(payload['model_id'], MODEL_ID)
        self.assertEqual(payload['language_code'], 'es')
        self.assertEqual(payload['text'], 'Hola, ¿quiénes nos acompañan hoy?')
        self.assertFalse(payload['voice_settings']['use_speaker_boost'])
        self.assertEqual(self.request_seen.url.params['output_format'], 'pcm_16000')
        self.assertEqual(b''.join(bytes(frame.data) for frame in frames), pcm)
        self.assertTrue(all(frame.sample_rate == 16000 and frame.num_channels == 1 for frame in frames))

    async def test_provider_error_does_not_expose_body_or_fallback(self):
        with self.assertRaisesRegex(RuntimeError, r'^ElevenLabs TTS failed \(HTTP 401\)$'):
            await self.request(httpx.Response(401, json={'private': 'must not be logged'}))

    async def test_empty_truncated_and_non_audio_are_rejected(self):
        for data, content_type in ((b'', 'audio/pcm'), (b'\x01', 'audio/pcm'), (b'{}', 'application/json')):
            with self.subTest(data=data, content_type=content_type), self.assertRaises(RuntimeError):
                await self.request(httpx.Response(200, content=data, headers={'content-type': content_type}))

    async def test_adapter_rejects_removed_provider(self):
        with self.assertRaisesRegex(ValueError, 'Only ElevenLabs'):
            await speech_frames('Hello', {'tts': 'espeak', 'language': 'en'})


if __name__ == '__main__':
    unittest.main()
