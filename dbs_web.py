"""Private mobile DBS demo: browser PCM over WSS -> existing LiveKit STT plugin.

No LiveKit room server is required by this transport. The canonical controller,
Speechmatics speaker enrollment and ElevenLabs synthesis are reused unchanged.
Only explicitly allowlisted static files are served. No transcripts/audio/IDs
are written to disk; debug events go exclusively to the owning WebSocket.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from pathlib import Path

from aiohttp import WSMsgType, web
from livekit import rtc
from livekit.agents import stt

from dbs_agent import (
    DBSController,
    configured_lesson,
    conversation_controller,
    facilitation_mode,
    identifiers_for,
    make_stt,
    settings,
    speaker_info,
)
from dbs_flow import Intent
from dbs_intents import rule_intent
from dbs_prompts import EN, ES
from dbs_tts import synthesize

ROOT = Path(__file__).resolve().parent
logger = logging.getLogger('discovering-god-web')
SAMPLE_RATE = 16000
MAX_SESSIONS = 3
MAX_SESSION_SECONDS = 3600
CONTROL_INTENTS = {
    'everyone': Intent.FINISH_ENROLLMENT, 'yes': Intent.YES, 'no': Intent.NO,
    'next': Intent.NEXT, 'repeat': Intent.REPEAT, 'scripture': Intent.SCRIPTURE,
    'pause': Intent.PAUSE, 'resume': Intent.RESUME, 'stop': Intent.STOP,
}
CONTROL_ACTIONS = {
    'everyone': 'finish_enrollment', 'next': 'next', 'previous': 'previous',
    'repeat': 'repeat', 'scripture': 'read_scripture', 'pause': 'pause',
    'resume': 'resume', 'stop': 'stop', 'yes': 'yes', 'no': 'no',
}
REASONS = {
    Intent.INTRODUCE: 'Validate a stated name against real speaker enrollment; request own-voice confirmation.',
    Intent.YES: 'Apply yes only to the pending confirmation; name confirmation requires the same speaker.',
    Intent.NO: 'Reject the pending confirmation without advancing.',
    Intent.FINISH_ENROLLMENT: 'Ask the group to verify the roster before continuing.',
    Intent.NEXT: 'Ask for confirmation before advancing the canonical lesson.',
    Intent.REPEAT: 'Replay the current question without advancing.',
    Intent.SCRIPTURE: 'Read the canonical passage, or request an NVI participant reading.',
    Intent.QUESTION: 'Redirect interpretation to Scripture and the group; never generate an answer.',
    Intent.DISCUSSION: 'Treat this as participant discussion, not an instruction to advance.',
    Intent.PAUSE: 'Pause facilitation and cancel playback.',
    Intent.RESUME: 'Resume without skipping any interrupted prompt.',
    Intent.STOP: 'Stop and clear session-only speaker bindings.',
    Intent.SAFETY: 'Pause the study for immediate human safety assistance.',
}


class RulesParser:
    """This demo deliberately exposes deterministic intents, not an LLM trace."""
    provider = 'rules'

    async def classify(self, text, phase):
        if len(text) > 10000:
            raise ValueError('Turn too long')
        return rule_intent(text, phase)

    async def close(self):
        pass


class DemoSession:
    def __init__(self, ws, language):
        self.ws = ws
        self.language = language
        self.config = {**settings(), 'language': language, 'stt_language': language}
        lesson = configured_lesson(self.config)
        self.controller = (conversation_controller(self.config, lesson) if facilitation_mode() == 'generative'
                           else DBSController(self.config, lesson, RulesParser(), EN if language == 'en' else ES))
        self.recognizer = None
        self.stream = None
        self.queue = asyncio.Queue(maxsize=12)
        self.closed = asyncio.Event()
        self.send_lock = asyncio.Lock()
        self.started = time.monotonic()
        self.last_input = self.started
        self.last_activity = self.started
        self.seq = 0
        self.busy = True
        self.nudged = False
        self.human_speaking = False
        self.turn_parts = []
        self.durations = {}
        self.received_bytes = 0
        self.accepted_bytes = 0
        self.active = None
        self.tasks = []
        self.played = None
        self.play_id = ''
        self.remaining_prompts = []
        self.resume_prompts = []
        self.current_prompt = ''
        self.current_key = 'welcome'

        self.audio_budget = 32000 * 2.0
        self.audio_budget_at = self.started

    def packet(self, kind, **values):
        self.seq += 1
        return {'type': kind, 'seq': self.seq, 'at': round(time.monotonic() - self.started, 3), **values}

    async def emit(self, kind, **values):
        if not self.ws.closed:
            async with self.send_lock:
                await self.ws.send_json(self.packet(kind, **values))

    def state(self):
        flow = self.controller.flow
        return {
            'phase': flow.phase, 'question_key': flow.question_key if self.controller.mode == 'generative' else self.current_key,
            'current_question': self.controller.render_canonical_question() if self.controller.mode == 'generative' else self.current_prompt,
            'spoken_prompt': self.current_prompt, 'index': flow.index,
            'steps': flow.steps, 'language': self.language,
            'bible': self.controller.lesson.bible,
            'scripture_mode': 'canonical_text' if self.controller.lesson.verses else 'participant_reads_NVI',
            'parser': self.controller.provider, 'model': self.controller.model, 'facilitation_mode': self.controller.mode,
            'paused': flow.paused, 'busy': self.busy,
            'listen': self.stream is not None and not self.busy and flow.phase != 'done',
            'queue_depth': self.queue.qsize(), 'elapsed_seconds': round(time.monotonic() - self.started, 1),
            'roster': [{'name': p.name, 'speaker': p.speaker, 'voice_enrolled': bool(p.identifiers),
                        'speech_seconds': round(self.durations.get(p.speaker, 0), 1)} for p in flow.roster],
            'pending': {'name': flow.pending.name, 'speaker': flow.pending.speaker} if flow.pending else None,
        }

    async def report_state(self):
        await self.emit('state', **self.state())

    async def fail(self, code, message, *, fatal=False):
        await self.emit('error', code=code, message=message, fatal=fatal)
        if fatal:
            self.closed.set()

    async def feed_audio(self, data):
        if not self.stream or self.closed.is_set():
            return
        if not data or len(data) % 2 or len(data) > 6400:
            await self.fail('invalid_audio', 'Expected mono 16 kHz int16 PCM, at most 200 ms per packet.', fatal=True)
            return
        now = time.monotonic()
        self.audio_budget = min(64000, self.audio_budget + (now - self.audio_budget_at) * 40000)
        self.audio_budget_at = now
        self.audio_budget -= len(data)
        if self.audio_budget < 0:
            await self.fail('audio_rate', 'Audio arrived faster than realtime. Restart the demo.', fatal=True)
            return
        self.last_input = now
        self.received_bytes += len(data)
        # Half-duplex demo: do not enroll playback/echo as a human. Still send
        # silence to keep STT timing/endpointing alive while William speaks.
        listening = self.state()['listen']
        pcm = data if listening else bytes(len(data))
        if listening:
            self.accepted_bytes += len(data)
        self.stream.push_frame(rtc.AudioFrame(data=pcm, sample_rate=SAMPLE_RATE, num_channels=1, samples_per_channel=len(pcm) // 2))

    async def handle_control(self, action):
        if action not in CONTROL_ACTIONS or (self.controller.mode == 'rules' and action == 'previous'):
            await self.fail('invalid_control', 'Unknown control.')
            return
        intent = CONTROL_INTENTS.get(action)
        if intent == Intent.STOP:
            await self.emit('cancel_audio')
            self.closed.set()
            return
        if self.controller.flow.phase == 'confirm_name' and intent in (Intent.YES, Intent.NO):
            await self.fail('voice_confirmation_required', 'Please confirm your name with your own voice, not a button.')
            return
        if self.controller.mode == 'generative':
            if action in ('yes', 'no'):
                await self.fail('no_pending_confirmation', 'Use your own voice to confirm a name; navigation is direct.')
                return
            item = ('control', CONTROL_ACTIONS[action])
            if action in ('pause', 'next', 'previous', 'repeat'):
                await self.preempt(item)
                return
            if self.busy:
                await self.fail('agent_busy', 'Wait until William finishes, or use Pause / End session.')
                return
            await self.enqueue(item)
            return
        if intent == Intent.PAUSE:
            await self.preempt((intent, '', '', 'button'))
            return
        if self.busy:
            await self.fail('agent_busy', 'Wait until William finishes, or use Pause / End session.')
            return
        await self.enqueue((intent, '', '', 'button'))

    async def enqueue(self, item):
        if self.queue.full():
            await self.fail('queue_full', 'Too many turns; wait, then repeat. The lesson has not advanced.')
            return
        self.queue.put_nowait(item)

    async def preempt(self, item):
        if self.remaining_prompts:
            self.resume_prompts = list(self.remaining_prompts)
        if self.active and not self.active.done():
            self.active.cancel()
            await asyncio.gather(self.active, return_exceptions=True)
        while not self.queue.empty():
            self.queue.get_nowait()
            self.queue.task_done()
        self.turn_parts.clear()
        await self.emit('cancel_audio')
        await self.enqueue(item)

    async def read_stt(self):
        try:
            async for event in self.stream:
                if event.type == stt.SpeechEventType.START_OF_SPEECH:
                    self.human_speaking = True
                    self.nudged = False
                if event.type in (stt.SpeechEventType.INTERIM_TRANSCRIPT, stt.SpeechEventType.FINAL_TRANSCRIPT):
                    final = event.type == stt.SpeechEventType.FINAL_TRANSCRIPT
                    alternatives = event.alternatives
                    text = ' '.join(a.text for a in alternatives)
                    if not text.strip():
                        continue
                    self.last_activity = time.monotonic()
                    self.nudged = False
                    await self.emit('transcript', final=final, text=text, ignored=self.busy,
                                    speakers=[{'speaker': a.speaker_id or 'UU', 'text': a.text,
                                               'confidence': a.confidence, 'start': a.start_time, 'end': a.end_time} for a in alternatives])
                    if final and not self.busy:
                        for alternative in alternatives:
                            sid = alternative.speaker_id or speaker_info(alternative.text)[0] or 'UU'
                            self.durations[sid] = self.durations.get(sid, 0) + max(0, alternative.end_time - alternative.start_time)
                        self.turn_parts.append(text)
                        if sum(map(len, self.turn_parts)) > 10000:
                            self.turn_parts.clear()
                            await self.fail('turn_too_long', 'Please speak in shorter turns.')
                if event.type == stt.SpeechEventType.END_OF_SPEECH:
                    self.human_speaking = False
                    if self.turn_parts:
                        text = ' '.join(self.turn_parts)
                        self.turn_parts.clear()
                        if not self.busy:
                            intent, name = rule_intent(text, self.controller.flow.phase)
                            item = (intent, name, text, 'voice')
                            if intent in (Intent.PAUSE, Intent.STOP, Intent.SAFETY):
                                await self.preempt(item)
                            else:
                                await self.enqueue(item)
            if not self.closed.is_set():
                await self.fail('stt_disconnected', 'Speech recognition disconnected. End and start a new session to re-enroll voices.', fatal=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- provider boundary, never expose authenticated errors
            if self.closed.is_set():
                return
            logger.warning('DBS web STT failed: %s', type(exc).__name__)
            await self.fail('stt_error', 'Speechmatics could not keep the session open. Check connectivity and restart.', fatal=True)

    async def speak(self, prompts):
        self.remaining_prompts = list(prompts)
        for prompt in prompts:
            self.busy = True
            text = self.controller.render(prompt)
            self.current_prompt, self.current_key = text, prompt.key
            await self.report_state()
            await self.emit('prompt', key=prompt.key, text=text, origin=self.controller.prompt_origin(prompt))
            await self.emit('status', stage='synthesizing', message='ElevenLabs Flash is preparing the next prompt.')
            metrics = {}
            frames = await synthesize(text, self.language, metrics=metrics)
            await self.emit('tts', **metrics)
            pcm = b''.join(bytes(frame.data) for frame in frames)
            self.play_id = uuid.uuid4().hex
            self.played = asyncio.get_running_loop().create_future()
            await self.emit('status', stage='speaking', message='William is speaking; microphone audio is gated to prevent echo.')
            async with self.send_lock:
                await self.ws.send_json(self.packet('audio', id=self.play_id, sample_rate=SAMPLE_RATE,
                                                    samples=len(pcm) // 2, format='pcm_s16le', channels=1))
                await self.ws.send_bytes(pcm)
            try:
                # Browser acknowledgement occurs only after actual AudioBuffer playout.
                await asyncio.wait_for(self.played, len(pcm) / 32000 + 20)
            finally:
                self.played = None
                self.play_id = ''
            self.remaining_prompts.pop(0)
        self.remaining_prompts.clear()

    async def process(self, item):
        previous = self.controller.snapshot()
        self.busy = True
        await self.report_state()
        started = time.perf_counter()
        try:
            if item == 'opening':
                prompts = await self.controller.start()
            elif item == 'idle':
                if self.human_speaking or not self.queue.empty():
                    return
                prompts = await self.controller.idle()
            elif isinstance(item, tuple) and len(item) == 2 and item[0] == 'control':
                action = item[1]
                prompts = self.controller.control(action)
                if action in ('next', 'previous', 'repeat', 'read_scripture', 'finish_enrollment'):
                    self.resume_prompts = []
                if action == 'resume':
                    prompts += self.resume_prompts
                    self.resume_prompts = []
                await self.emit('decision', input=f'Button: {action}', source='button', action=action, intent=action,
                                phase_before=previous[0]['phase'], phase_after=self.controller.flow.phase,
                                prompts=[p.key for p in prompts], prompt_origins=[self.controller.prompt_origin(p) for p in prompts],
                                parser='deterministic', model='', elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                                reason='Apply the selected session control directly.')
            else:
                intent, _name, text, source = item
                speaker, solo = speaker_info(text)
                ids = ()
                enrolling = self.controller.flow.phase in ('introductions', 'confirm_name')
                urgent = intent in (Intent.STOP, Intent.PAUSE, Intent.SAFETY)
                if source == 'voice' and not urgent and enrolling and speaker and speaker != 'UU' and solo and self.durations.get(speaker, 0) >= 5:
                    ids = identifiers_for(await self.recognizer.get_speaker_ids(), speaker)
                if source == 'button' or urgent:
                    prompts = self.controller.control(intent.value)
                else:
                    prompts = await self.controller.accept(text, speaker=speaker, identifiers=ids, solo=solo, source=source)
                decision = self.controller.last_decision
                action = decision.action if decision else intent.value
                await self.emit('decision', input=text or f'Button: {intent.value}', source=source,
                                action=action, intent=action, name='', phase_before=previous[0]['phase'],
                                phase_after=self.controller.flow.phase, prompts=[p.key for p in prompts],
                                prompt_origins=[self.controller.prompt_origin(p) for p in prompts],
                                parser='deterministic' if urgent or source == 'button' else self.controller.provider,
                                model=self.controller.model if not urgent and source != 'button' else '',
                                elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                                reason=decision.note if self.controller.mode == 'generative' and decision else REASONS[intent])
                if action in ('next', 'previous', 'repeat', 'read_scripture', 'finish_enrollment'):
                    self.resume_prompts = []
                if action == 'resume':
                    prompts += self.resume_prompts
                    self.resume_prompts = []
            if item in ('opening', 'idle'):
                decision = self.controller.last_decision
                action = decision.action if decision else item
                await self.emit('decision', input='', source=item, action=action, intent=action,
                                phase_before=previous[0]['phase'], phase_after=self.controller.flow.phase,
                                prompts=[p.key for p in prompts], prompt_origins=[self.controller.prompt_origin(p) for p in prompts],
                                parser=self.controller.provider, model=self.controller.model,
                                elapsed_ms=round((time.perf_counter() - started) * 1000, 1),
                                reason=decision.note if self.controller.mode == 'generative' and decision else '')
            await self.speak(prompts)
            self.controller.complete_playback(prompts)
        except asyncio.CancelledError:
            # Interruption preserves the selected stage; resume replays remaining
            # canonical prompts instead of skipping them or advancing twice.
            raise
        except Exception as exc:  # noqa: BLE001 -- transactional turn rollback on provider/playback failure
            self.controller.restore(previous)
            self.remaining_prompts.clear()
            await self.emit('cancel_audio')
            logger.warning('DBS web turn failed: %s', type(exc).__name__)
            await self.fail('turn_failed', 'Facilitation or speech playback failed. State was restored; please end and restart.', fatal=True)
        finally:
            self.busy = False
            self.last_activity = time.monotonic()
            if not self.closed.is_set():
                await self.report_state()
                await self.emit('status', stage='paused' if self.controller.flow.paused else 'listening',
                                message='Paused; say William, resume or use the Resume button.' if self.controller.flow.paused else 'Listening. Speak one at a time; say your name and share for at least five seconds.')

    async def consume(self):
        while not self.closed.is_set():
            item = await self.queue.get()
            self.active = asyncio.create_task(self.process(item))
            try:
                await self.active
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling():
                    raise
            finally:
                self.active = None
                self.queue.task_done()
            if self.controller.flow.phase == 'done':
                self.closed.set()

    async def tick(self):
        while not self.closed.is_set():
            await asyncio.sleep(1)
            now = time.monotonic()
            await self.emit('metrics', input_seconds=round(self.accepted_bytes / 32000, 1),
                            received_bytes=self.received_bytes, queue_depth=self.queue.qsize())
            if now - self.started > MAX_SESSION_SECONDS:
                await self.fail('session_limit', 'One-hour demo limit reached. Start a fresh session.', fatal=True)
            if now - self.last_input > 40:
                await self.fail('microphone_stalled', 'No audio has arrived for 40 seconds. Keep this page foreground and restart.', fatal=True)
            if not self.busy and not self.human_speaking and not self.nudged and self.queue.empty() and now - self.last_activity > 25:
                self.nudged = True
                if self.controller.flow.phase == 'lesson' and not self.controller.flow.paused:
                    await self.enqueue('idle')

    async def run(self):
        try:
            await self.emit('status', stage='connecting_stt', message='Starting private session with Speechmatics and ElevenLabs.')
            self.recognizer = make_stt(self.config)
            async with self.recognizer.stream() as stream:
                self.stream = stream
                await self.queue.put('opening')
                self.tasks = [asyncio.create_task(self.read_stt()), asyncio.create_task(self.consume()), asyncio.create_task(self.tick())]
                try:
                    await self.closed.wait()
                finally:
                    # Stop readers/playback before closing their underlying STT
                    # stream so a normal Stop is not reported as provider failure.
                    if self.active:
                        self.active.cancel()
                    for task in self.tasks:
                        task.cancel()
                    await asyncio.gather(*self.tasks, return_exceptions=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- fail closed at session boundary
            if not self.closed.is_set():
                logger.warning('DBS web session failed: %s', type(exc).__name__)
                await self.fail('session_failed', 'The voice session could not start. Please retry.', fatal=True)
        finally:
            self.closed.set()
            if self.active:
                self.active.cancel()
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            if self.active:
                await asyncio.gather(self.active, return_exceptions=True)
            self.stream = None
            try:
                if self.recognizer:
                    await self.recognizer.aclose()
            except Exception as exc:  # noqa: BLE001 -- close the other clients even if STT cleanup fails
                logger.warning('DBS STT cleanup failed: %s', type(exc).__name__)
            finally:
                self.controller.flow.roster.clear()
                self.controller.flow.pending = None
                self.turn_parts.clear()
                self.durations.clear()
                self.remaining_prompts.clear()
                self.resume_prompts.clear()
                try:
                    await self.controller.close()
                except Exception as exc:  # noqa: BLE001 -- local state is cleared; still release the browser
                    logger.warning('DBS facilitator cleanup failed: %s', type(exc).__name__)
            if not self.ws.closed:
                await self.emit('cancel_audio')
                await self.emit('ended', message='Session ended. Session-only speaker bindings cleared; close/clear the page to remove its transcript.')
                await self.ws.close()


def create_app(*, origins=None):
    configured_origins = origins or {o.strip() for o in os.getenv('DBS_WEB_ORIGINS', 'http://127.0.0.1:8094,http://localhost:8094').split(',')}
    sessions = set()
    sockets = set()

    @web.middleware
    async def security(request, handler):
        # Exact Origin/Host matching also prevents cross-site WebSocket hijacking
        # and DNS rebinding of the loopback service. Tailscale handles peer auth.
        from urllib.parse import urlsplit
        allowed_hosts = {urlsplit(o).netloc for o in configured_origins}
        if request.host not in allowed_hosts:
            raise web.HTTPForbidden(text='Unrecognized host')
        response = await handler(request)
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
            'Permissions-Policy': 'microphone=(self), camera=(), geolocation=()',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; media-src 'self' blob:; worker-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'",
        })
        return response

    app = web.Application(middlewares=[security], client_max_size=65536)

    async def health(request):
        return web.json_response({'ok': True, 'service': 'discovering-god-debug', 'protocol': 1,
                                  'active_sessions': len(sessions), 'max_sessions': MAX_SESSIONS,
                                  'transport': 'wss-pcm-livekit-stt', 'languages': ['en', 'es']})

    async def asset(request):
        name = request.match_info.get('name', 'index.html')
        if name not in ('index.html', 'app.js', 'styles.css', 'pcm-worklet.js'):
            raise web.HTTPNotFound()
        return web.FileResponse(ROOT / 'web' / name)

    async def websocket(request):
        if request.headers.get('Origin') not in configured_origins:
            raise web.HTTPForbidden(text='Same-origin browser connection required')
        if len(sockets) >= MAX_SESSIONS + 3:
            raise web.HTTPServiceUnavailable(text='Demo is full; retry later')
        ws = web.WebSocketResponse(heartbeat=15, max_msg_size=65536, compress=False)
        await ws.prepare(request)
        sockets.add(ws)
        session = None
        running = None
        await ws.send_json({'type': 'hello', 'protocol': 1})
        try:
            async with asyncio.timeout(MAX_SESSION_SECONDS + 30):
                while not ws.closed:
                    if session and session.closed.is_set():
                        break
                    message = await ws.receive(timeout=30 if session is None else 50)
                    if message.type == WSMsgType.BINARY:
                        if session:
                            await session.feed_audio(message.data)
                        continue
                    if message.type != WSMsgType.TEXT:
                        break
                    try:
                        data = json.loads(message.data)
                    except (json.JSONDecodeError, TypeError):
                        await ws.close(code=1008, message=b'Invalid JSON')
                        break
                    if not isinstance(data, dict):
                        await ws.close(code=1008, message=b'Expected object')
                        break
                    kind = data.get('type')
                    if kind == 'start':
                        if session or data.get('language') not in ('en', 'es'):
                            await ws.send_json({'type': 'error', 'code': 'invalid_start', 'message': 'Choose English or Spanish before starting.', 'fatal': True})
                            break
                        if len(sessions) >= MAX_SESSIONS:
                            await ws.send_json({'type': 'error', 'code': 'capacity', 'message': 'Three demo sessions are already active. End one and retry.', 'fatal': True})
                            break
                        session = DemoSession(ws, data['language'])
                        sessions.add(session)
                        running = asyncio.create_task(session.run())
                    elif session and kind == 'control':
                        await session.handle_control(data.get('action'))
                    elif session and kind in ('played', 'playback_error'):
                        if session.played and not session.played.done() and data.get('id') == session.play_id:
                            if kind == 'played':
                                session.played.set_result(True)
                            else:
                                session.played.set_exception(RuntimeError('Browser playback failed'))
        except (TimeoutError, ConnectionError):
            pass
        except Exception as exc:  # noqa: BLE001 -- invalid clients/config must not leak secrets or traceback
            if not (session and session.closed.is_set()):
                logger.warning('DBS web socket failed: %s', type(exc).__name__)
                if not ws.closed:
                    await ws.send_json({'type': 'error', 'code': 'connection_failed', 'message': 'The demo connection failed; end and restart.', 'fatal': True})
        finally:
            if session:
                session.closed.set()
            if running:
                try:
                    await asyncio.wait_for(asyncio.shield(running), 8)
                except TimeoutError:
                    running.cancel()
                    await asyncio.gather(running, return_exceptions=True)
            sessions.discard(session)
            sockets.discard(ws)
            await ws.close()
        return ws

    async def redirect(request):
        raise web.HTTPPermanentRedirect('/dbs/')

    app.router.add_get('/dbs', redirect)
    for prefix in ('', '/dbs'):
        app.router.add_get(prefix + '/', asset)
        app.router.add_get(prefix + '/health', health)
        app.router.add_get(prefix + '/ws', websocket)
        app.router.add_get(prefix + '/{name}', asset)

    async def shutdown(app):
        for session in list(sessions):
            session.closed.set()
        await asyncio.gather(*(ws.close(code=1001, message=b'Server shutdown') for ws in list(sockets)), return_exceptions=True)

    app.on_shutdown.append(shutdown)
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)
    # No access logs: URLs and participant activity do not belong in the journal.
    web.run_app(create_app(), host='127.0.0.1', port=int(os.getenv('DBS_WEB_PORT', '8094')), access_log=None)
