"""Offline incremental streaming tests; PCM and credentials are test fixtures."""
import asyncio
from contextlib import aclosing, contextmanager
import json
import unittest
from unittest.mock import patch

import httpx

import dbs_tts


class ChunkedPCM(httpx.AsyncByteStream):
    def __init__(self, chunks, *, gate_after=None, fail_after=False):
        self.chunks = chunks
        self.gate_after = gate_after
        self.fail_after = fail_after
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.finished = False
        self.reads = 0

    async def __aiter__(self):
        for index, chunk in enumerate(self.chunks):
            if index == self.gate_after:
                self.waiting.set()
                await self.release.wait()
            self.reads += 1
            yield chunk
        if self.fail_after:
            raise httpx.ReadError('fixture transport failure')
        self.finished = True

    async def aclose(self):
        self.closed = True


class PCMStreamingTests(unittest.IsolatedAsyncioTestCase):
    @contextmanager
    def provider(self, chunks, *, status=200, content_type='audio/pcm', **kwargs):
        stream = ChunkedPCM(chunks, **kwargs)
        response = httpx.Response(status, headers={'content-type': content_type}, stream=stream)
        requests = []

        def handler(request):
            requests.append(request)
            return response

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with (
            patch('dbs_tts.api_key', return_value='unit-test-only'),
            patch('dbs_tts.httpx.AsyncClient', return_value=client),
        ):
            yield stream, response, client, requests

    def assert_closed(self, stream, response, client):
        self.assertTrue(stream.closed)
        self.assertTrue(response.is_closed)
        self.assertTrue(client.is_closed)

    async def test_first_chunk_arrives_before_rest_and_metrics_are_incremental(self):
        metrics = {'audio_seconds': 999, 'synthesis_seconds': 999}
        with self.provider([b'\x01\x02', b'\x03\x04'], gate_after=1) as (source, response, client, requests):
            audio = dbs_tts.stream_pcm('Hello.', 'en', voice=dbs_tts.DEFAULT_VOICE_ID, metrics=metrics)
            try:
                first = await asyncio.wait_for(anext(audio), 1)
                self.assertEqual(first, b'\x01\x02')
                self.assertFalse(source.finished)
                self.assertEqual(source.reads, 1)
                self.assertFalse(response.is_closed)
                self.assertEqual(metrics['provider'], 'elevenlabs')
                self.assertEqual(metrics['model'], dbs_tts.MODEL_ID)
                self.assertEqual(metrics['voice_id'], dbs_tts.DEFAULT_VOICE_ID)
                self.assertEqual(metrics['language'], 'en')
                self.assertGreaterEqual(metrics['first_audio_seconds'], 0)
                self.assertNotIn('synthesis_seconds', metrics)
                self.assertNotIn('audio_seconds', metrics)
                source.release.set()
                self.assertEqual([chunk async for chunk in audio], [b'\x03\x04'])
            finally:
                await audio.aclose()
            self.assert_closed(source, response, client)
            self.assertEqual(metrics['audio_seconds'], round(4 / 32000, 3))
            self.assertGreaterEqual(metrics['synthesis_seconds'], metrics['first_audio_seconds'])
            request = requests[0]
            self.assertEqual(request.url.params['output_format'], 'pcm_16000')
            self.assertEqual(json.loads(request.content)['model_id'], 'eleven_flash_v2_5')

    async def test_odd_network_splits_empty_chunks_and_large_chunks_are_lossless(self):
        chunks = [b'', b'\x01', b'', b'\x02\x03', b'\x04', bytes(range(256)) * 60, b'\x05\x06']
        with self.provider(chunks) as (source, response, client, _):
            result = [chunk async for chunk in dbs_tts.stream_pcm('Hola.', 'es')]
            self.assertTrue(all(isinstance(chunk, bytes) and 0 < len(chunk) <= 6400 and len(chunk) % 2 == 0 for chunk in result))
            self.assertEqual(b''.join(result), b''.join(chunks))
            self.assert_closed(source, response, client)

    async def test_odd_tail_errors_after_partial_audio_and_no_completion_metrics(self):
        metrics = {}
        with self.provider([b'\x01\x02', b'\x03']) as (source, response, client, _):
            audio = dbs_tts.stream_pcm('Hello.', 'en', metrics=metrics)
            self.assertEqual(await anext(audio), b'\x01\x02')
            with self.assertRaisesRegex(RuntimeError, 'truncated int16 PCM'):
                await anext(audio)
            self.assert_closed(source, response, client)
            self.assertNotIn('synthesis_seconds', metrics)
            self.assertNotIn('audio_seconds', metrics)

    async def test_empty_status_content_type_failures_close_without_yield(self):
        cases = [
            ([], 200, 'audio/pcm', 'empty or truncated'),
            ([b''], 200, 'audio/pcm', 'empty or truncated'),
            ([b'\x01'], 200, 'audio/pcm', 'empty or truncated'),
            ([b'private error body'], 401, 'application/json', r'^ElevenLabs TTS failed \(HTTP 401\)$'),
            ([b'{}'], 200, 'application/json', 'Unexpected ElevenLabs audio type'),
            ([b'\x01\x02'], 200, '', 'Unexpected ElevenLabs audio type'),
        ]
        for chunks, status, content_type, message in cases:
            with self.subTest(status=status, content_type=content_type, chunks=chunks):
                with self.provider(chunks, status=status, content_type=content_type) as (source, response, client, _):
                    with self.assertRaisesRegex(RuntimeError, message):
                        await anext(dbs_tts.stream_pcm('Hello.', 'en'))
                    if status != 200 or not content_type.startswith('audio/'):
                        self.assertEqual(source.reads, 0)
                    self.assert_closed(source, response, client)

    async def test_cancellation_while_reading_closes_response_and_client(self):
        with self.provider([b'\x01\x02', b'\x03\x04'], gate_after=1) as (source, response, client, _):
            audio = dbs_tts.stream_pcm('Hello.', 'en')
            self.assertEqual(await anext(audio), b'\x01\x02')
            pending = asyncio.create_task(anext(audio))
            try:
                await asyncio.wait_for(source.waiting.wait(), 1)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 1)
                self.assert_closed(source, response, client)
            finally:
                pending.cancel()
                await audio.aclose()

    async def test_cancellation_during_consumer_playback_with_aclosing(self):
        playing = asyncio.Event()
        with self.provider([b'\x01\x02', b'\x03\x04']) as (source, response, client, _):
            async def consume():
                async with aclosing(dbs_tts.stream_pcm('Hello.', 'en')) as audio:
                    async for _ in audio:
                        playing.set()
                        await asyncio.Event().wait()

            pending = asyncio.create_task(consume())
            try:
                await asyncio.wait_for(playing.wait(), 1)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(pending, 1)
                self.assert_closed(source, response, client)
            finally:
                pending.cancel()

    async def test_explicit_close_after_first_chunk_closes_response_and_client(self):
        with self.provider([b'\x01\x02', b'\x03\x04']) as (source, response, client, _):
            audio = dbs_tts.stream_pcm('Hello.', 'en')
            await anext(audio)
            await audio.aclose()
            self.assertEqual(source.reads, 1)
            self.assert_closed(source, response, client)

    async def test_transport_failure_after_partial_audio_closes(self):
        with self.provider([b'\x01\x02'], fail_after=True) as (source, response, client, _):
            audio = dbs_tts.stream_pcm('Hello.', 'en')
            await anext(audio)
            with self.assertRaises(httpx.ReadError):
                await anext(audio)
            self.assert_closed(source, response, client)

    async def test_validation_happens_before_http_client_creation(self):
        cases = [('Hello.', 'sw', ''), ('Hello.', 'en', '../voice'), ('   ', 'en', '')]
        for text, language, voice in cases:
            with self.subTest(text=text, language=language, voice=voice):
                with patch('dbs_tts.httpx.AsyncClient') as client:
                    with self.assertRaises(ValueError):
                        await anext(dbs_tts.stream_pcm(text, language, voice=voice))
                    client.assert_not_called()
        with patch('dbs_tts.api_key', side_effect=ValueError('missing key')), patch('dbs_tts.httpx.AsyncClient') as client:
            with self.assertRaisesRegex(ValueError, 'missing key'):
                await anext(dbs_tts.stream_pcm('Hello.', 'en'))
            client.assert_not_called()

    async def test_synthesize_preserves_frame_boundaries_and_metrics(self):
        pcm = bytes(range(256)) * 5 + b'\x01\x02'
        metrics = {}
        with self.provider([pcm[:1], pcm[1:603], pcm[603:]]) as (source, response, client, _):
            frames = await dbs_tts.synthesize('Hola.', 'es', metrics=metrics)
            self.assertIsInstance(frames, list)
            self.assertEqual([len(frame.data) * frame.data.itemsize for frame in frames], [640, 640, 2])
            self.assertEqual(b''.join(bytes(frame.data) for frame in frames), pcm)
            self.assertTrue(all(frame.sample_rate == 16000 and frame.num_channels == 1 for frame in frames))
            self.assertEqual(set(metrics), {'provider', 'model', 'voice_id', 'language', 'first_audio_seconds', 'synthesis_seconds', 'audio_seconds'})
            self.assertEqual(metrics['audio_seconds'], round(len(pcm) / 32000, 3))
            self.assert_closed(source, response, client)


if __name__ == '__main__':
    unittest.main()
