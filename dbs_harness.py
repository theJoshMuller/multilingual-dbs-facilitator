"""In-memory handoff to the active Codex session; no external model client."""
import asyncio
import os
import re
import secrets
import stat
import uuid
from pathlib import Path

from aiohttp import web

from dbs_facilitator import ACTIONS, Decision


def validate_decision(value):
    if not isinstance(value, dict) or set(value) - {'action', 'speech', 'name', 'note'}:
        raise ValueError('Invalid decision fields')
    action = value.get('action')
    if action not in ACTIONS or action == 'grounding_challenge':
        raise ValueError('Invalid action')
    for field, limit in (('speech', 1200), ('name', 60), ('note', 160)):
        if not isinstance(value.get(field, ''), str) or len(value.get(field, '')) > limit:
            raise ValueError('Invalid decision text')
    if (action == 'listen' and value.get('speech')) or (action == 'respond' and not value.get('speech', '').strip()):
        raise ValueError('Invalid response')
    return Decision(**value)


class HarnessBroker:
    def __init__(self, token):
        self.token = token
        self.jobs = {}
        self.changed = asyncio.Event()

    @classmethod
    def local(cls, path=None):
        path = Path(path or Path(__file__).resolve().parent / '.cache/harness-token')
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.exists():
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_uid != os.getuid():
                raise ValueError('Harness token must be a private regular file')
            token = path.read_text().strip()
        else:
            token = secrets.token_urlsafe(32)
            with path.open('x') as file:
                path.chmod(0o600)
                file.write(token)
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
            raise ValueError("Invalid saved harness token")
        return cls(token)

    async def request(self, session, text, context, source, event, timeout):
        if len(self.jobs) >= 12:
            raise RuntimeError('Decision queue full')
        job_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        self.jobs[job_id] = ({'id': job_id, 'session': session, 'text': text[:4000], 'context': context, 'source': source, 'event': event}, future)
        self.changed.set()
        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self.jobs.pop(job_id, None)

    async def next(self, timeout=20):
        if not self.jobs:
            self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), timeout)
            except TimeoutError:
                return None
        return next(iter(self.jobs.values()))[0] if self.jobs else None

    def reply(self, job_id, value):
        decision = validate_decision(value)
        job = self.jobs.get(job_id)
        if not job or job[1].done():
            raise ValueError('Expired decision')
        job[1].set_result(decision)

    def close_session(self, session):
        for _, (payload, future) in list(self.jobs.items()):
            if payload['session'] == session:
                future.cancel()

    def mount(self, app):
        def authorized(request):
            if (request.remote not in ('127.0.0.1', '::1') or request.headers.get('Origin')
                    or request.headers.get('Sec-Fetch-Site')
                    or not request.headers.get('X-DBS-Harness')
                    or not secrets.compare_digest(request.headers.get('X-DBS-Harness', ''), self.token)):
                raise web.HTTPForbidden(text='Local harness only')

        async def poll(request):
            authorized(request)
            return web.json_response({'job': await self.next()})

        async def reply(request):
            authorized(request)
            try:
                value = await request.json()
                self.reply(value['id'], value['decision'])
            except (ValueError, KeyError, TypeError):
                raise web.HTTPBadRequest(text='Invalid or expired decision') from None
            return web.json_response({'accepted': True})

        app.router.add_get('/internal/harness/next', poll)
        app.router.add_post('/internal/harness/reply', reply)


class HarnessFacilitator:
    provider = 'codex-session'
    model = 'active harness session'

    def __init__(self, broker, *, timeout=90):
        self.broker = broker
        self.timeout = timeout
        self.session = uuid.uuid4().hex

    async def decide(self, text, context, *, source='voice', event='participant'):
        if event == 'opening':
            # The user-authored f.001 opening is played once by the controller.
            return Decision('listen')
        return await self.broker.request(self.session, text, context, source, event, self.timeout)

    async def close(self):
        self.broker.close_session(self.session)
