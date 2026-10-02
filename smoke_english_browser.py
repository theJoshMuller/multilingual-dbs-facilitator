"""Bounded real provider transport check with Chromium's synthetic microphone.

The prepared opening needs no live decision-queue reply. Later facilitation needs the active Codex session. This does not prove
human microphone accuracy, diarization, name binding, or room echo behavior.
"""
import asyncio
import json

from playwright.async_api import async_playwright, expect

from smoke_assemblyai_browser import browser_tmp


async def smoke():
    events = []
    errors = []
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
            args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
        context = await browser.new_context(permissions=['microphone'])
        page = await context.new_page()
        page.on('pageerror', lambda error: errors.append(type(error).__name__))
        def socket(ws):
            def receive(payload):
                if isinstance(payload, str):
                    event = json.loads(payload)
                    events.append(event)
                    if event['type'] in ('status', 'error', 'provider_termination'):
                        print(json.dumps({k:v for k,v in event.items() if k in ('type','stage','code','message','audio_duration_seconds','session_duration_seconds')}), flush=True)
            ws.on('framereceived', receive)
        page.on('websocket', socket)
        await page.add_init_script('''
          window.__tracks = [];
          const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
          navigator.mediaDevices.getUserMedia = async (...args) => {
            const stream = await original(...args);
            window.__tracks.push(...stream.getTracks());
            return stream;
          };
        ''')
        try:
            await page.goto('http://127.0.0.1:8095/dbs/', wait_until='networkidle')
            await expect(page.locator('#mode')).to_have_value('assemblyai-english')
            assert await page.locator('#consent').count() == 0
            await page.locator('#start').click()
            await expect(page.locator('#connection')).to_have_text('listening', timeout=90000)
            assert any(e['type'] == 'audio' for e in events)
            assert any(e['type'] == 'prompt' and e['key'] == 'f.001' for e in events)
            await page.locator('#stop').click()
            await expect(page.locator('#start')).to_be_enabled(timeout=10000)
            assert await page.evaluate('window.__tracks.every(t=>t.readyState==="ended")')
            assert not [e for e in events if e['type'] == 'error']
            assert not errors
            termination = [e for e in events if e['type'] == 'provider_termination']
            assert termination
            print(json.dumps({'status':'passed', 'evidence':'real YouVersion / AssemblyAI / ElevenLabs, synthetic Chromium microphone',
                'source_version_ids':[e.get('version_id') for e in events if e['type']=='scripture_source'],
                'prompt_keys':[e['key'] for e in events if e['type']=='prompt'],
                'tts_audio_messages':sum(e['type']=='audio' for e in events),
                'first_audio_seconds':next(e.get('at') for e in events if e['type']=='audio'),
                'source_ready_seconds':next(e.get('at') for e in events if e['type']=='scripture_source'),
                'termination':{k:v for k,v in termination[-1].items() if k.endswith('_seconds')},
                'microphone_released':True,'js_errors':errors}))
        finally:
            if await page.locator('#stop').is_enabled():
                await page.locator('#stop').click()
                await asyncio.sleep(4)
            await browser.close()


if __name__ == '__main__':
    with browser_tmp():
        asyncio.run(smoke())
