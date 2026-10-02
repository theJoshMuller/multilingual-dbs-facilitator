"""Start the real CLI/audio pipeline, verify readiness, and always stop it.

This uses the microphone and plays the welcome through system audio. Run only
with participants' agreement; no transcript or raw console log is saved.
"""
import argparse
import json
import os
import pty
import select
import signal
import subprocess
import sys
import time
from pathlib import Path


def smoke(language: str, tts: str):
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [sys.executable, 'dbs_agent.py', 'console', '--log-level', 'info'],
        cwd=Path(__file__).resolve().parent,
        stdin=slave, stdout=slave, stderr=slave, start_new_session=True,
        env={**os.environ, 'DBS_LANGUAGE': language, 'STT_LANGUAGE': language,
             'DBS_LLM_PROVIDER': 'rules', 'DBS_TTS_PROVIDER': tts},
    )
    os.close(slave)
    text = ''
    ready = False
    deadline = time.monotonic() + 90
    try:
        while time.monotonic() < deadline and process.poll() is None:
            if not select.select([master], [], [], 1)[0]:
                continue
            try:
                text += os.read(master, 65536).decode(errors='replace')
            except OSError:
                break
            if 'DBS ready:' in text:
                ready = True
                break
        result = {'language': language, 'tts': tts, 'console_ready': ready,
                  'traceback': 'Traceback' in text,
                  'provider_error': 'ERROR' in text}
        print(json.dumps(result), flush=True)
        if not ready:
            # Prompt keys are non-sensitive; no participant text is printed.
            print('Prompt markers:', [key for key in ('welcome',) if f'DBS prompt: {key}' in text])
        assert ready and not result['traceback'] and not result['provider_error'], 'Console startup failed'
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGINT)
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=8)
        os.close(master)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--language', choices=('en', 'es'), default='en')
    parser.add_argument('--tts', choices=('elevenlabs',), default='elevenlabs')
    args = parser.parse_args()
    smoke(args.language, args.tts)
