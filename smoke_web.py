"""Live synthetic WSS/STT/TTS test; no human microphone, no saved voice IDs.

Output audio is validated and acknowledged immediately (transport test, not
acoustic playback). smoke_web_browser.py separately exercises browser playout.
"""
import argparse
import asyncio
import json
from collections import Counter, deque
from urllib.parse import urlsplit

import aiohttp

from dbs_tts import synthesize


async def smoke(base, language):
    introduction = (
        'My name is Alex. I am a synthetic test voice. Since we last met, I am thankful '
        'for time with friends and long walks in the park. This is a test of the voice demo.'
        if language == 'en' else
        'Me llamo Alex. Soy una voz de prueba. Desde la última vez que nos reunimos, '
        'estoy agradecido por el tiempo con amigos y los paseos por el parque. Esta es una prueba del sistema de voz.'
    )
    intro_frames = await synthesize(introduction, language)
    yes_frames = await synthesize('Yes, that is my name.' if language == 'en' else 'Sí, ese es mi nombre.', language)
    origin = f'{urlsplit(base).scheme}://{urlsplit(base).netloc}'
    outgoing = deque()
    latest = {}
    counts = Counter()
    errors = []
    synthetic_transcripts = []
    changed = asyncio.Condition()
    descriptor = None
    audio_bytes = 0

    async with aiohttp.ClientSession() as client:  # noqa: SIM117 -- separate client/socket ownership
        async with client.ws_connect(base.rstrip('/') + '/ws', origin=origin, max_msg_size=16 * 1024 * 1024) as ws:
            async def consume():
                nonlocal latest, descriptor, audio_bytes
                async for message in ws:
                    if message.type == aiohttp.WSMsgType.TEXT:
                        event = json.loads(message.data)
                        counts[event['type']] += 1
                        assert 'speaker_identifiers' not in message.data
                        if event['type'] == 'state':
                            async with changed:
                                latest = event
                                changed.notify_all()
                        if event['type'] == 'error':
                            errors.append(event)
                            async with changed:
                                changed.notify_all()
                        if event['type'] == 'audio':
                            descriptor = event
                        if event['type'] == 'transcript' and event['final']:
                            synthetic_transcripts.append({'text': event['text'], 'ignored': event['ignored']})
                    elif message.type == aiohttp.WSMsgType.BINARY:
                        assert descriptor and len(message.data) == descriptor['samples'] * 2
                        assert any(message.data), 'Output audio was all silence'
                        audio_bytes += len(message.data)
                        await ws.send_json({'type': 'played', 'id': descriptor['id']})
                        descriptor = None

            async def pump():
                while not ws.closed:
                    await ws.send_bytes(outgoing.popleft() if outgoing else bytes(3200))
                    await asyncio.sleep(0.1)

            async def wait_state(predicate, timeout=60):
                try:
                    async with asyncio.timeout(timeout):
                        async with changed:
                            await changed.wait_for(lambda: errors or predicate(latest))
                        assert not errors, errors
                except TimeoutError:
                    print(json.dumps({'synthetic_test_timeout': True, 'phase': latest.get('phase'),
                                      'busy': latest.get('busy'), 'events': dict(counts),
                                      'synthetic_transcripts': synthetic_transcripts,
                                      'reader_done': reader.done(),
                                      'reader_error': type(reader.exception()).__name__ if reader.done() and not reader.cancelled() else None}), flush=True)
                    raise

            def feed(frames):
                pcm = b''.join(bytes(frame.data) for frame in frames)
                outgoing.extend(pcm[offset:offset + 3200] for offset in range(0, len(pcm), 3200))

            reader = asyncio.create_task(consume())
            sender = asyncio.create_task(pump())
            try:
                await ws.send_json({'type': 'start', 'language': language, 'consent': True})
                await wait_state(lambda s: s.get('listen') and s.get('phase') == 'introductions')
                feed(intro_frames)
                await wait_state(lambda s: s.get('listen') and s.get('phase') == 'confirm_name')
                assert latest['pending']['name'] == 'Alex', {'pending': latest['pending'], 'synthetic_transcripts': synthetic_transcripts}
                feed(yes_frames)
                await wait_state(lambda s: s.get('listen') and len(s.get('roster', [])) == 1)
                assert latest['roster'][0]['voice_enrolled'] is True
                await ws.send_json({'type': 'control', 'action': 'everyone'})
                await wait_state(lambda s: s.get('listen') and s.get('phase') == 'confirm_roster')
                await ws.send_json({'type': 'control', 'action': 'yes'})
                await wait_state(lambda s: s.get('listen') and s.get('phase') == 'lesson')
                assert latest['question_key'] == 'f.002', latest
                await ws.send_json({'type': 'control', 'action': 'stop'})
                await asyncio.wait_for(reader, 15)
                assert not errors, errors
                print(json.dumps({'status': 'passed', 'language': language, 'url': base,
                                  'real_voice_enrolled': True, 'own_voice_confirmation': True,
                                  'question_after_onboarding': 'f.002', 'output_pcm_bytes': audio_bytes,
                                  'events': dict(counts), 'errors': len(errors), 'human_mic_used': False}, indent=2))
            finally:
                reader.cancel()
                sender.cancel()
                await asyncio.gather(reader, sender, return_exceptions=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8094/dbs/')
    parser.add_argument('--language', choices=('en', 'es'), default='en')
    args = parser.parse_args()
    asyncio.run(smoke(args.url, args.language))
