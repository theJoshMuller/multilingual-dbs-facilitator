"""Offline tests of the in-session decision bridge; no model API calls."""
import asyncio
import unittest

from dbs_facilitator import Decision
from dbs_harness import HarnessBroker, HarnessFacilitator


class HarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_empty_saved_token_fails_closed(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'token'
            path.write_text('   ')
            path.chmod(0o600)
            with self.assertRaises(ValueError):
                HarnessBroker.local(path)

    async def test_opening_is_ready_without_a_decision_queue_wait(self):
        broker = HarnessBroker("fixture-token")
        brain = HarnessFacilitator(broker)
        decision = await asyncio.wait_for(brain.decide("", {}, event="opening"), .05)
        self.assertEqual(decision.action, "listen")
        self.assertEqual(decision.speech, "")
        self.assertEqual(broker.jobs, {})

    async def test_decision_is_in_memory_and_validated(self):
        broker = HarnessBroker('fixture-token')
        brain = HarnessFacilitator(broker)
        request = asyncio.create_task(brain.decide('Synthetic turn', {'phase': 'lesson'}, event='participant'))
        item = await broker.next(0.1)
        self.assertEqual(item['text'], 'Synthetic turn')
        self.assertEqual(item['event'], 'participant')
        broker.reply(item['id'], {'action': 'respond', 'speech': 'Who else would like to share?'})
        self.assertEqual(await request, Decision('respond', speech='Who else would like to share?'))
        self.assertEqual(broker.jobs, {})

    async def test_timeout_and_close_clear_private_jobs(self):
        broker = HarnessBroker('fixture-token')
        brain = HarnessFacilitator(broker, timeout=0.01)
        with self.assertRaises(TimeoutError):
            await brain.decide('private fixture', {})
        self.assertEqual(broker.jobs, {})
        request = asyncio.create_task(brain.decide('private fixture', {}))
        await broker.next(.1)
        await brain.close()
        with self.assertRaises(asyncio.CancelledError):
            await request
        self.assertEqual(broker.jobs, {})

    async def test_invalid_reply_cannot_mutate_pending_decision(self):
        broker = HarnessBroker('fixture-token')
        brain = HarnessFacilitator(broker)
        request = asyncio.create_task(brain.decide('turn', {}))
        job = await broker.next(.1)
        for value in ({'action': 'translate'}, {'action': 'listen', 'speech': 'unexpected'}, {'action': 'respond', 'speech': ''}, {'action': 'read_scripture', 'quote': 'invented'}):
            with self.assertRaises(ValueError):
                broker.reply(job['id'], value)
        broker.reply(job['id'], {'action': 'listen'})
        self.assertEqual((await request).action, 'listen')


class HarnessHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_browser_and_unauthenticated_clients_cannot_read_private_context(self):
        from aiohttp import web
        from aiohttp.test_utils import TestClient, TestServer
        broker = HarnessBroker('fixture-token')
        app = web.Application()
        broker.mount(app)
        async with TestClient(TestServer(app)) as client:
            for headers in ({}, {'X-DBS-Harness': 'wrong'}, {'X-DBS-Harness': 'fixture-token', 'Origin': 'http://localhost'}, {'X-DBS-Harness': 'fixture-token', 'Sec-Fetch-Site': 'same-origin'}):
                response = await client.get('/internal/harness/next', headers=headers)
                self.assertEqual(response.status, 403)
            brain = HarnessFacilitator(broker)
            request = asyncio.create_task(brain.decide('private fixture', {}))
            await asyncio.sleep(0)
            response = await client.get('/internal/harness/next', headers={'X-DBS-Harness': 'fixture-token'})
            job = (await response.json())['job']
            response = await client.post('/internal/harness/reply', headers={'X-DBS-Harness': 'fixture-token'}, json={'id': job['id'], 'decision': {'action': 'listen'}})
            self.assertEqual(response.status, 200)
            self.assertEqual((await request).action, 'listen')
