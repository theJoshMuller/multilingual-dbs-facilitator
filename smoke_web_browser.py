"""Disposable Chromium checks using a fake device, not the host microphone.

Run: uv run --with playwright python smoke_web_browser.py --url HTTPS_URL
Uses installed Chromium; does not download/install a system browser.
"""
import argparse
import asyncio
import json

from playwright.async_api import async_playwright, expect


async def smoke(url):
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path='/usr/bin/chromium', headless=True,
                                                  args=['--use-fake-device-for-media-stream', '--use-fake-ui-for-media-stream'])
        context = await browser.new_context(viewport={'width': 393, 'height': 851}, is_mobile=True, has_touch=True,
                                            permissions=['microphone'], device_scale_factor=2)
        page = await context.new_page()
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        await page.add_init_script('''
            window.__micTracks = [];
            const original = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia = async (...args) => {
                const stream = await original(...args);
                window.__micTracks.push(...stream.getTracks());
                return stream;
            };
        ''')
        try:
            await page.goto(url, wait_until='networkidle')
            assert await page.title() == 'William · DBS voice lab'
            assert await page.evaluate('window.isSecureContext')
            assert await page.locator('#start').is_disabled()
            assert await page.evaluate('window.__micTracks.length') == 0
            assert await page.evaluate('document.documentElement.scrollWidth <= window.innerWidth'), 'Horizontal overflow on phone'
            await page.locator('#consent').check()
            await page.locator('#start').click()
            await expect(page.locator('#connection')).to_have_text('listening', timeout=75000)
            assert await page.locator('#error-banner').is_hidden(), await page.locator('#error-banner').inner_text()
            assert await page.locator('#current-prompt').inner_text() == "Welcome to a new session of Discovering God! Let's begin by catching up on how we're doing. First, can you say your name so I can recognize who is who? Then, based on what's happened to you since last time we met, what is something that you're thankful for?"
            assert await page.evaluate("window.__micTracks.every(track => track.readyState === 'live')")
            assert await page.locator('#events').inner_text() and 'playback finished' in await page.locator('#events').inner_text()
            assert await page.locator('#audio-format').inner_text() != 'Mono PCM · 16 kHz · 100 ms frames'
            await page.locator('#mute').click()
            assert 'Muted' in await page.locator('#gate').inner_text()
            await page.locator('#stop').click()
            assert await page.evaluate("window.__micTracks.every(track => track.readyState === 'ended')")
            assert await page.locator('#stop').is_disabled()
            await page.locator('#clear-debug').click()
            assert 'Debug history cleared.' in await page.locator('#events').inner_text()
            assert not errors, errors
            print(json.dumps({'status': 'passed', 'url': url, 'viewport': '393x851', 'secure_context': True,
                              'permission_gated': True, 'real_elevenlabs_browser_playout': True,
                              'fake_microphone_worklet_capture': True, 'mute_and_stop': True,
                              'microphone_released': True, 'horizontal_overflow': False, 'js_errors': errors}))
        finally:
            await context.close()
            await browser.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://127.0.0.1:8094/dbs/')
    args = parser.parse_args()
    asyncio.run(smoke(args.url))
