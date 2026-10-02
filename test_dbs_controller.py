import unittest

from dbs_controller import ConversationController
from dbs_facilitator import Decision
from dbs_flow import Prompt
from test_dbs_facilitator import lesson


class Brain:
    provider = 'injected'
    model = 'offline'

    def __init__(self):
        self.decision = Decision('listen')
        self.calls = []
        self.closed = False

    async def decide(self, text, context, *, source='voice', event='participant'):
        self.calls.append((text, context, source, event))
        return self.decision

    async def close(self):
        self.closed = True


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.brain = Brain()
        self.controller = ConversationController(lesson(), facilitator=self.brain)

    async def test_generated_and_canonical_rendering(self):
        self.brain.decision = Decision('respond', speech='Take the time you need, Ana.')
        prompt = (await self.controller.accept('Could we slow down?'))[0]
        self.assertEqual(self.controller.render(prompt), self.brain.decision.speech)
        self.assertEqual(self.controller.prompt_origin(prompt), 'generated')
        self.assertEqual(self.controller.render(Prompt('a.001')), lesson().questions['a.001'])
        self.assertEqual(self.controller.render(Prompt('scripture')), lesson().scripture)
        self.assertEqual(self.controller.prompt_origin(Prompt('scripture')), 'canonical')

    async def test_three_people_optional_name_clarification_and_identity(self):
        for number, name in enumerate(('Ana', 'Ben', 'Cara'), 1):
            speaker = f'S{number}'
            self.brain.decision = Decision('clarify_name', name=name)
            await self.controller.accept(f'I am {name}', speaker=speaker, identifiers=(f'opaque-{number}',))
            self.brain.decision = Decision('confirm_name')
            await self.controller.accept('you got it', speaker='Other')
            self.assertEqual(len(self.controller.flow.roster), number - 1)
            await self.controller.accept('that is me', speaker=speaker)
        self.assertEqual([p.name for p in self.controller.flow.roster], ['Ana', 'Ben', 'Cara'])
        self.brain.decision = Decision('listen')
        self.assertEqual(await self.controller.accept('I noticed light.', speaker='S1'), [])

    async def test_name_and_thankfulness_share_one_intro_before_next_question(self):
        opening = "Welcome! Start with your name, then share something you are thankful for."
        self.brain.decision = Decision('respond', speech=opening)
        prompts = await self.controller.start()
        self.assertEqual(self.controller.render(prompts[0]), opening)
        self.assertEqual(self.controller.prompt_origin(prompts[0]), 'generated')
        contribution = "I'm Ana. I'm thankful my sister is recovering."
        self.brain.decision = Decision('introduce', name='Ana', speech='Who else would like to share?')
        await self.controller.accept(contribution, speaker='S1', identifiers=('opaque-1',))
        self.assertEqual(self.brain.calls[-1][0], contribution)
        self.assertIsNone(self.controller.flow.pending)
        self.assertEqual(self.controller.flow.roster[0].name, 'Ana')
        self.assertEqual(self.controller.history[-2]['text'], contribution)
        self.assertEqual(self.controller.flow.roster[0].name, 'Ana')
        prompts = self.controller.control('finish_enrollment')
        self.assertEqual(prompts[-1].key, 'a.001')
        self.assertFalse(any(prompt.key == 'f.001' for prompt in prompts))

    async def test_explicit_lifecycle_and_one_idle_per_lull(self):
        self.brain.decision = Decision('respond', speech='Welcome. What are you thankful for?')
        await self.controller.start()
        self.assertEqual(self.brain.calls[-1][0], '')
        self.assertEqual(self.brain.calls[-1][3], 'opening')
        self.assertFalse(any(t['role'] == 'user' for t in self.controller.history))
        self.controller.flow.phase = 'lesson'
        await self.controller.idle()
        self.assertEqual(self.brain.calls[-1][3], 'idle')
        count = len(self.brain.calls)
        self.assertEqual(await self.controller.idle(), [])
        self.assertEqual(len(self.brain.calls), count)

    async def test_full_rollback_controls_and_close(self):
        snapshot = self.controller.snapshot()
        self.brain.decision = Decision('next', speech='Let us continue.')
        await self.controller.accept('Next please')
        self.controller.restore(snapshot)
        self.assertEqual(self.controller.flow.index, 0)
        self.assertEqual(self.controller.history, [])
        self.assertIsNone(self.controller.last_decision)
        calls = len(self.brain.calls)
        self.controller.control('next')
        self.assertEqual(len(self.brain.calls), calls)
        await self.controller.close()
        self.assertTrue(self.brain.closed)
        self.assertEqual(self.controller.history, [])

    async def test_text_mode_and_context_readiness(self):
        controller = ConversationController(lesson(), facilitator=self.brain, text_mode=True)
        self.brain.decision = Decision('introduce', name='Ana')
        await controller.accept('I am Ana', source='text')
        self.assertTrue(self.brain.calls[-1][1]['voice_enrollment_ready'])
        self.assertEqual(controller.flow.roster[0].identifiers, ())
        self.brain.decision = Decision('listen')
        await self.controller.accept('hello', speaker='S1', identifiers=('',))
        self.assertFalse(self.brain.calls[-1][1]['voice_enrollment_ready'])

    async def test_lifecycle_cannot_execute_historical_navigation(self):
        self.brain.decision = Decision('next', speech='Moving on.')
        self.assertEqual(await self.controller.start(), [])
        self.assertEqual(self.controller.flow.index, 0)

    async def test_stop_clears_context_and_local_safety_is_deterministic(self):
        self.brain.decision = Decision('respond', speech='Take your time.')
        await self.controller.accept('hello')
        prompt = self.controller.control('safety')[0]
        self.assertTrue(self.controller.flow.paused)
        self.assertEqual(self.controller.last_decision.action, 'pause')
        self.assertEqual(self.controller.prompt_origin(prompt), 'deterministic')
        self.controller.control('stop')
        self.assertEqual(self.controller.history, [])
        self.assertEqual(self.controller.flow.phase, 'done')

    async def test_provider_failure_rolls_back_and_finish_button_is_local(self):
        class FailingBrain(Brain):
            async def decide(self, *args, **kwargs):
                raise RuntimeError('offline failure')
        controller = ConversationController(lesson(), facilitator=FailingBrain())
        snapshot = controller.snapshot()
        with self.assertRaises(RuntimeError):
            await controller.accept('hello')
        self.assertEqual(controller.snapshot(), snapshot)
        self.controller.control('finish_enrollment')
        self.assertEqual(self.brain.calls, [])

    async def test_late_decision_cannot_undo_local_stop(self):
        import asyncio
        entered = asyncio.Event()
        release = asyncio.Event()

        class SlowBrain(Brain):
            async def decide(self, *args, **kwargs):
                entered.set()
                await release.wait()
                return Decision('next')
        controller = ConversationController(lesson(), facilitator=SlowBrain())
        task = asyncio.create_task(controller.accept('Next please'))
        await entered.wait()
        controller.control('stop')
        release.set()
        self.assertEqual(await task, [])
        self.assertEqual(controller.history, [])
        self.assertEqual(controller.last_decision.action, 'stop')

    async def test_model_stop_clears_session_history(self):
        self.brain.decision = Decision('respond', speech='Take your time.')
        await self.controller.accept('hello')
        self.brain.decision = Decision('stop', speech='We will stop here.')
        prompts = await self.controller.accept('Stop the session')
        self.assertEqual(self.controller.render(prompts[0]), 'We will stop here.')
        self.assertEqual(self.controller.history, [])

    async def test_async_transactions_serialize_before_context_and_rollback(self):
        import asyncio
        entered = asyncio.Event()
        release = asyncio.Event()

        class OverlapBrain(Brain):
            async def decide(self, text, context, **kwargs):
                self.calls.append((text, context))
                if text == 'first':
                    entered.set()
                    await release.wait()
                    raise RuntimeError('first failed')
                return Decision('next')
        brain = OverlapBrain()
        controller = ConversationController(lesson(), facilitator=brain)
        first = asyncio.create_task(controller.accept('first'))
        await entered.wait()
        second = asyncio.create_task(controller.accept('second'))
        await asyncio.sleep(0)
        self.assertEqual(len(brain.calls), 1)
        release.set()
        with self.assertRaises(RuntimeError):
            await first
        await second
        self.assertEqual(controller.flow.question_key, 'a.001')
        self.assertEqual([t['text'] for t in controller.history if t['role'] == 'user'], ['second'])

    async def test_idle_waits_for_participant_then_uses_updated_context(self):
        import asyncio
        entered = asyncio.Event()
        release = asyncio.Event()

        class OverlapBrain(Brain):
            async def decide(self, text, context, **kwargs):
                self.calls.append((text, context, kwargs))
                if text:
                    entered.set()
                    await release.wait()
                    return Decision('next')
                return Decision('listen')
        brain = OverlapBrain()
        controller = ConversationController(lesson(), facilitator=brain)
        controller.flow.phase = 'lesson'
        participant = asyncio.create_task(controller.accept('next'))
        await entered.wait()
        idle = asyncio.create_task(controller.idle())
        await asyncio.sleep(0)
        self.assertEqual(len(brain.calls), 1)
        release.set()
        await participant
        await idle
        self.assertEqual(brain.calls[-1][1]['current_question_key'], 'a.001')
        self.assertEqual(brain.calls[-1][1]['history'][0]['text'], 'next')

    async def test_final_playback_ack_pause_resume_previous_and_rollback(self):
        self.controller.control('next')
        snapshot = self.controller.snapshot()
        final = self.controller.control('next')
        self.assertEqual(self.controller.flow.phase, 'closing')
        self.controller.control('pause')
        self.assertTrue(self.controller.flow.paused)
        self.controller.complete_playback(final)
        self.assertEqual(self.controller.flow.phase, 'closing')
        self.controller.control('resume')
        self.assertFalse(self.controller.flow.paused)
        repeated = self.controller.control('repeat')
        self.assertEqual(repeated[-1].key, 'a.002')
        self.controller.control('previous')
        self.assertEqual(self.controller.flow.question_key, 'a.001')
        self.controller.complete_playback(final)
        self.assertEqual(self.controller.flow.phase, 'lesson')
        self.controller.control('next')
        self.controller.restore(snapshot)
        self.assertEqual(self.controller.flow.question_key, 'a.001')
        final = self.controller.control('next')
        self.controller.complete_playback([Prompt('assistant', {'text': 'procedure'})])
        self.assertEqual(self.controller.flow.phase, 'closing')
        self.controller.complete_playback(final)
        self.assertEqual(self.controller.flow.phase, 'done')

    async def test_introduction_idle_nudges_once_and_never_finishes_enrollment(self):
        self.brain.decision = Decision('respond', speech='Anyone else? Tell me when everyone has shared.')
        prompts = await self.controller.idle()
        self.assertTrue(prompts, 'Introductions must be eligible for a generated nudge')
        self.assertEqual(self.controller.render(prompts[0]), self.brain.decision.speech)
        self.assertEqual(self.brain.calls[-1][3], 'idle')
        self.assertEqual(self.controller.flow.question_key, 'f.001')
        count = len(self.brain.calls)
        self.assertEqual(await self.controller.idle(), [])
        self.assertEqual(len(self.brain.calls), count)
        self.brain.decision = Decision('finish_enrollment')
        await self.controller.accept("William, that's all of us.", speaker='S1')
        self.brain.decision = Decision('next')
        self.assertEqual(await self.controller.idle(), [])
        self.assertEqual(self.controller.flow.question_key, 'f.001')

    async def test_direct_navigation_rearms_nudge_for_new_question(self):
        self.controller.flow.phase = 'lesson'
        self.brain.decision = Decision('respond', speech='Would anyone else like to share?')
        await self.controller.idle()
        self.controller.control('next')
        count = len(self.brain.calls)
        await self.controller.idle()
        self.assertEqual(len(self.brain.calls), count + 1)

    async def test_paused_introduction_does_not_call_model_for_nudge(self):
        self.controller.control('pause')
        self.assertEqual(await self.controller.idle(), [])
        self.assertEqual(self.brain.calls, [])
