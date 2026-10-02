"""Offline legacy PCM playback/ack regression; synthetic mic and local WS fixture."""
import asyncio
import json
import os

from playwright.async_api import async_playwright, expect

from smoke_assemblyai_browser import browser_tmp


async def smoke():
    acknowledgments = []
    errors = []
    fixture = {}
    async with async_playwright() as p:
        browser = await p.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
            args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
        context = await browser.new_context(permissions=['microphone'])
        page = await context.new_page()
        page.on('pageerror', lambda e: errors.append(type(e).__name__))
        def bridge(route):
            def send(value):
                route.send(json.dumps(value))
            def receive(payload):
                if isinstance(payload, bytes): return
                value = json.loads(payload)
                if value['type'] == 'start':
                    fixture['send'] = send
                    fixture['route'] = route
                    send({'type':'state','mode':'assemblyai-english','phase':'introductions','bible':'NIV','steps':[],'busy':True,'listen':False,'roster':[],'source_ready':True,'provider_ready':False})
                    send({'type':'status','stage':'connecting_stt','message':'Synthetic connection fixture'})
                elif value['type'] == 'played':
                    acknowledgments.append(value['id'])
                    send({'type':'state','mode':'assemblyai-english','phase':'introductions','bible':'NIV','steps':[],'busy':False,'listen':True,'roster':[],'source_ready':True,'provider_ready':True})
                    send({'type':'status','stage':'listening','message':'Playback acknowledged'})
                elif value['type'] == 'control' and value['action'] == 'stop':
                    send({'type':'provider_termination','audio_duration_seconds':1,'session_duration_seconds':1})
                    send({'type':'ended','message':'Fixture ended'})
            route.on_message(receive)
            send({'type':'hello','protocol':1})
        await page.route_web_socket('**/ws', bridge)
        try:
            await page.goto(os.getenv('DBS_SMOKE_BASE_URL', 'http://127.0.0.1:8095') + '/dbs/', wait_until='networkidle')
            await page.locator('#start').click()
            await expect(page.locator('#connection')).to_have_text('connecting stt', timeout=5000)
            await expect(page.locator('[data-action="pause"]')).to_be_disabled()
            await expect(page.locator('#stop')).to_be_enabled()
            fixture['send']({'type':'state','mode':'assemblyai-english','phase':'introductions','bible':'NIV','steps':[],'busy':True,'listen':False,'roster':[],'source_ready':True,'provider_ready':True})
            fixture['send']({'type':'status','stage':'speaking','message':'Synthetic playback fixture'})
            await expect(page.locator('[data-action="pause"]')).to_be_enabled()
            fixture['send']({'type':'audio','id':'fixture-audio','sample_rate':16000,'samples':1600,'channels':1,'format':'pcm_s16le'})
            fixture['route'].send(b'\x01\x00'*1600)
            await expect(page.locator('#connection')).to_have_text('listening', timeout=5000)
            assert acknowledgments == ['fixture-audio']
            assert not errors
            await page.locator('#stop').click()
            await expect(page.locator('#start')).to_be_enabled(timeout=8000)
            print(json.dumps({'passed':True,'evidence':'offline synthetic PCM and WS fixture','playback_ack_count':len(acknowledgments),'provider_calls':0,'js_errors':errors}))
        finally:
            await browser.close()


if __name__ == '__main__':
    with browser_tmp(): asyncio.run(smoke())
