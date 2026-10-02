"""ElevenLabs Flash speech output. No local/robotic synthesis fallback."""
from __future__ import annotations

import os
import re
import time
from collections.abc import AsyncGenerator
from contextlib import aclosing
from pathlib import Path

import httpx
from dotenv import dotenv_values
from livekit import rtc

MODEL_ID = 'eleven_flash_v2_5'
# Eric — Smooth, Trustworthy; verified against this account's default voices.
DEFAULT_VOICE_ID = 'cjVigY5qzO86Huf0OWal'
SAMPLE_RATE = 16000
# Verified via GET /v1/models; recognition coverage is a separate concern.
LANGUAGES = frozenset({'en', 'ja', 'zh', 'de', 'hi', 'fr', 'ko', 'pt', 'it', 'es', 'ru', 'id', 'nl', 'tr', 'fil', 'pl', 'sv', 'bg', 'ro', 'ar', 'cs', 'el', 'fi', 'hr', 'ms', 'sk', 'da', 'ta', 'uk', 'hu', 'no', 'vi'})
LANGUAGE_ALIASES = {'cmn': 'zh', 'nb': 'no'}


def tts_language(language: str) -> str:
    code = LANGUAGE_ALIASES.get(language, language)
    if code not in LANGUAGES:
        raise ValueError(f'ElevenLabs Flash v2.5 does not support {language}; no alternate-voice fallback')
    return code


def voice_id(language: str) -> str:
    value = os.getenv(f'ELEVENLABS_VOICE_ID_{language.upper()}', os.getenv('ELEVENLABS_VOICE_ID', DEFAULT_VOICE_ID))
    if not re.fullmatch(r'[A-Za-z0-9]{1,128}', value):
        raise ValueError('ELEVENLABS_VOICE_ID must be a valid voice identifier')
    return value


def api_key() -> str:
    for name in ('ELEVENLABS_API_KEY', 'ELEVEN_API_KEY'):
        if value := os.getenv(name):
            return value
    # Reuse the existing secret, without copying it into the demo repository.
    source = Path(os.getenv('ELEVENLABS_ENV_FILE', str(Path.home() / 'Developer/video-use/.env'))).expanduser()
    values = dotenv_values(source) if source.is_file() else {}
    value = values.get('ELEVENLABS_API_KEY') or values.get('ELEVEN_API_KEY')
    if not value:
        raise ValueError('Set ELEVENLABS_API_KEY or point ELEVENLABS_ENV_FILE to the existing key file')
    return value


async def stream_pcm(text: str, language: str, *, voice: str = '', metrics: dict | None = None) -> AsyncGenerator[bytes, None]:
    """Yield mono little-endian int16 16 kHz PCM as it arrives (<=6400 bytes).

    Status and audio type are checked before yielding. A later transport failure
    or odd final byte can still fail after partial audio; there is no fallback.
    Callers stopping early must use ``contextlib.aclosing`` or ``aclose()`` to
    release the response, including cancellation during their own playback work.
    First-byte timing is available while streaming; completion metrics appear
    only on successful exhaustion. Synthesis time includes consumer backpressure,
    and neither timing measures audible end-to-end latency.
    """
    code = tts_language(language)
    selected_voice = voice or voice_id(language)
    if not re.fullmatch(r'[A-Za-z0-9]{1,128}', selected_voice):
        raise ValueError('Invalid ElevenLabs voice identifier')
    if not text.strip():
        raise ValueError('Cannot synthesize an empty prompt')
    headers = {'xi-api-key': api_key(), 'Accept': 'audio/pcm'}
    payload = {
        'text': text, 'model_id': MODEL_ID, 'language_code': code,
        'voice_settings': {'stability': 0.5, 'similarity_boost': 0.75, 'style': 0.0,
                           'use_speaker_boost': False, 'speed': 1.0},
        'apply_text_normalization': 'auto', 'apply_language_text_normalization': False,
    }
    started = time.perf_counter()
    first_byte = None
    total_bytes = 0
    carry = b''
    if metrics is not None:
        # A reused dictionary must not claim a previous stream's completion.
        for key in ('first_audio_seconds', 'synthesis_seconds', 'audio_seconds'):
            metrics.pop(key, None)
        metrics.update({'provider': 'elevenlabs', 'model': MODEL_ID,
                        'voice_id': selected_voice, 'language': code})
    async with (
        httpx.AsyncClient(timeout=httpx.Timeout(60, connect=15)) as client,
        client.stream(
            'POST', f'https://api.elevenlabs.io/v1/text-to-speech/{selected_voice}/stream',
            params={'output_format': 'pcm_16000', 'enable_logging': 'true'},
            headers=headers, json=payload,
        ) as response,
    ):
        if response.status_code != 200:
            # Do not echo provider error bodies or authenticated requests.
            raise RuntimeError(f'ElevenLabs TTS failed (HTTP {response.status_code})')
        content_type = response.headers.get('content-type', '').split(';')[0].strip().lower()
        if content_type not in ('audio/pcm', 'audio/x-pcm', 'audio/raw', 'application/octet-stream'):
            raise RuntimeError(f'Unexpected ElevenLabs audio type: {content_type}')
        # Do not pass chunk_size: httpx would buffer up to that size before the
        # first yield. Normalize actual network chunks ourselves instead.
        async for chunk in response.aiter_bytes():
            if not chunk:
                continue
            if first_byte is None:
                first_byte = time.perf_counter() - started
                if metrics is not None:
                    metrics['first_audio_seconds'] = round(first_byte, 3)
            data = carry + chunk
            even_length = len(data) - len(data) % 2
            carry = data[even_length:]
            for offset in range(0, even_length, 6400):
                pcm = data[offset:min(offset + 6400, even_length)]
                total_bytes += len(pcm)
                yield pcm
    if not total_bytes or carry:
        raise RuntimeError('ElevenLabs returned empty or truncated int16 PCM')
    if metrics is not None:
        metrics.update({'synthesis_seconds': round(time.perf_counter() - started, 3),
                        'audio_seconds': round(total_bytes / (2 * SAMPLE_RATE), 3)})


async def synthesize(text: str, language: str, *, voice: str = '', metrics: dict | None = None) -> list[rtc.AudioFrame]:
    """Collect and validate an entire prompt before returning 20 ms PCM frames.

    This preserves the buffered API for existing callers; use ``stream_pcm``
    for incremental playback. Errors return no frames and trigger no fallback.
    """
    pcm = bytearray()
    async with aclosing(stream_pcm(text, language, voice=voice, metrics=metrics)) as audio:
        async for chunk in audio:
            pcm.extend(chunk)
    frames = []
    for offset in range(0, len(pcm), 640):
        data = bytes(pcm[offset:offset + 640])
        frames.append(rtc.AudioFrame(data=data, sample_rate=SAMPLE_RATE,
                                    num_channels=1, samples_per_channel=len(data) // 2))
    return frames
