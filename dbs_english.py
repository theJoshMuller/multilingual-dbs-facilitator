"""One-mic English DBS with in-session facilitation and official NIV text."""
import asyncio
import copy
import logging
import time

from dbs_assemblyai import AssemblyStream, read_worktree_key
from dbs_controller import ConversationController
from dbs_conversation import ConversationFlow
from dbs_curriculum import Lesson, validate_verses
from dbs_facilitator import Decision
from dbs_flow import Participant, Prompt
from dbs_harness import HarnessFacilitator
from dbs_mixed_turns import TurnLedger
from dbs_web import DemoSession
from dbs_youversion import QUESTIONS, canonical_questions, fetch_selected

ENGLISH_OPENING = (
    "Hello! Welcome to a new session of discovering God. We're going to do a Discovery Bible Study together, "
    "which is a way for a small group to learn to Discover, Share, and Obey God's word together. "
    "If you're on your own, please gather at least 1 or 2 others to this together. "
    "If you're already in a group, let's start by talking about how everyone is doing. "
    "Could you please share your name, and then share something you're thankful for from this last week? "
    "When the last person has shared, simply say that you're ready for the next question"
)

STEPS = [*QUESTIONS[:4], 'scripture', *QUESTIONS[4:]]


def question_lesson():
    questions = canonical_questions('en')
    questions['f.001'] = ENGLISH_OPENING
    return Lesson('eng', 'English', STEPS[:], questions, [], 'NIV', '')


def build_english_lesson():
    lesson = question_lesson()
    source = fetch_selected('en', include_verses=True)
    lesson.verses = validate_verses(source['verses'])
    lesson.copyright = source['publisher'] + ' · ' + source['copyright']
    lesson.scripture_url = 'https://www.bible.com/bible/111/GEN.1.NIV'
    return lesson, {k: v for k, v in source.items() if k not in ('content', 'verses')}


class EnglishFlow(ConversationFlow):
    """AAI labels never masquerade as Speechmatics voice identifiers."""
    def __init__(self, lesson, ledger):
        super().__init__(lesson)
        self.ledger = ledger
        self.order = None
        self.reported_names = []

    def sync_names(self):
        self.roster = [Participant(value['name'], label, ()) for label, value in self.ledger.names.items()]
        if self.pending and self.pending.speaker not in self.ledger.pending:
            self.pending = None
            if self.phase == 'confirm_name':
                self.phase = 'introductions'

    def apply(self, decision, *, speaker='', identifiers=(), solo=True):
        self.sync_names()
        if decision.action == 'introduce':
            if decision.name and decision.name not in self.reported_names:
                self.reported_names.append(decision.name)
                self.reported_names[:] = self.reported_names[-7:]
            if solo and self.ledger.introduce(self.order, decision.name):
                self.pending = Participant(decision.name, speaker, ())
                self.phase = 'confirm_name'
            return self.generated(decision.speech)
        if decision.action in ('confirm_name', 'reject_name'):
            if self.pending and solo and self.pending.speaker == speaker:
                if decision.action == 'confirm_name':
                    self.ledger.confirm(self.order, self.pending.name)
                    self.sync_names()
                    if speaker in self.ledger.names:
                        self.pending = None
                        self.phase = 'introductions'
                else:
                    self.ledger.pending.pop(speaker, None)
                    self.pending = None
                    self.phase = 'introductions'
            return self.generated(decision.speech)
        if decision.action == 'finish_enrollment':
            if self.phase not in ('introductions', 'confirm_name'):
                return self.generated(decision.speech)
            self.pending = None
            self.phase = 'lesson'
            return [*self.generated(decision.speech), *self.navigate(1)]
        return super().apply(decision, speaker=speaker, solo=solo)


class EnglishController(ConversationController):
    def prompt_origin(self, prompt):
        if prompt.key == 'f.001':
            return 'user_authored'
        return super().prompt_origin(prompt)

    def snapshot(self):
        state = super().snapshot()
        state[0].pop('ledger', None)
        return state

    def restore(self, snapshot):
        ledger = self.flow.ledger
        super().restore(snapshot)
        self.flow.ledger = ledger
        self.flow.sync_names()

    def _context(self, speaker='', solo=True, identifiers=()):
        context = super()._context(speaker, solo, ())
        context['voice_enrollment_ready'] = solo
        context['reported_names_unverified'] = self.flow.reported_names[:]
        context['human_identity_verified'] = False
        return context


class EnglishSession(DemoSession):
    def __init__(self, ws, broker, *, provider=None, prepared=None):
        self.ledger = TurnLedger()
        controller = EnglishController(copy.deepcopy(prepared[0]) if prepared else question_lesson(), facilitator=HarnessFacilitator(broker))
        controller.flow = EnglishFlow(controller.lesson, self.ledger)
        super().__init__(ws, 'en', controller=controller)
        self.provider = provider or AssemblyStream(read_worktree_key(), languages=('en',))
        self.provenance = dict(prepared[1]) if prepared else {}
        self.last_turn_end = 0
        self.opening_pending = True

    def state(self):
        flow = self.controller.flow
        flow.sync_names()
        return {**super().state(), 'mode': 'assemblyai-english',
                'parser': self.controller.provider, 'model': self.controller.model,
                'facilitation_mode': 'generative', 'question_key': flow.question_key,
                'current_question': self.controller.render_canonical_question(),
                'spoken_prompt': self.current_prompt, 'source': self.provenance,
                'scripture_mode': 'official_youversion' if self.provenance else 'verifying_official_youversion',
                'source_ready': bool(self.provenance),
                'provider_ready': self.provider.ready,
                'opening_pending': self.opening_pending,
                'listen': self.provider.ready and not self.busy and not flow.paused and not self.opening_pending and flow.phase != 'done',
                'wake_listen': False,
                'roster': [{'name': p.name, 'speaker': p.speaker, 'voice_enrolled': False,
                            'identity_status': 'confirmed_same_label'} for p in flow.roster],
                'unverified_names': flow.reported_names, 'human_identity_verified': False}

    async def feed_audio(self, data):
        if self.closed.is_set():
            return
        if not data or len(data) % 2 or len(data) > 6400:
            await self.fail('invalid_audio', 'Expected mono 16 kHz int16 PCM.', fatal=True)
            return
        now = time.monotonic()
        self.audio_budget = min(64000, self.audio_budget + (now-self.audio_budget_at)*40000) - len(data)
        self.audio_budget_at = self.last_input = now
        if self.audio_budget < 0:
            await self.fail('audio_rate', 'Microphone audio arrived faster than realtime.', fatal=True)
            return
        self.received_bytes += len(data)
        listening = self.provider.ready and not self.busy and not self.controller.flow.paused and not self.opening_pending
        if listening:
            self.accepted_bytes += len(data)
        try:
            await self.provider.send_audio(data, playback=not listening)
        except Exception:  # noqa: BLE001 -- never expose provider authentication/errors
            await self.fail('provider_audio', 'Microphone transport failed; stopping.', fatal=True)

    async def on_provider(self, message):
        if message['type'] == 'Begin':
            self.stream = self.provider
            await self.report_state()
            await self.enqueue('opening')
            return
        if message['type'] == 'Termination':
            await self.emit('provider_termination', audio_duration_seconds=message.get('audio_duration_seconds'), session_duration_seconds=message.get('session_duration_seconds'))
            return
        for event in self.ledger.accept(message):
            self.controller.flow.sync_names()
            ignored = self.busy or self.closed.is_set() or self.controller.flow.paused or self.opening_pending
            await self.emit('transcript', **{k: v for k, v in event.items() if k != 'type'}, ignored=ignored)
            if event.get('new_final') and not event.get('revision') and not ignored:
                self.last_activity = time.monotonic()
                await self.enqueue(event)

    async def handle_control(self, action):
        action = {'scripture': 'read_scripture', 'everyone': 'finish_enrollment'}.get(action, action)
        if action == 'stop':
            await self.emit('cancel_audio')
            self.closed.set()
            return
        if action not in ('next', 'previous', 'repeat', 'read_scripture', 'pause', 'resume', 'finish_enrollment'):
            await self.fail('invalid_control', 'Use natural spoken confirmation or a study control.')
            return
        if not self.provider.ready:
            await self.fail('still_connecting', 'The voice session is still connecting. Use Stop to cancel startup.')
            return
        if self.opening_pending and action not in ('pause', 'resume'):
            await self.fail('opening_pending', 'The welcome is still being prepared. Use Pause or Stop to interrupt.')
            return
        if not self.provenance and action not in ('pause', 'resume'):
            await self.fail('source_loading', 'Official NIV is still being verified; Scripture and navigation are temporarily unavailable.')
            return
        item = {'control': action}
        if self.busy or action == 'pause':
            await self.preempt(item)
        else:
            await self.enqueue(item)

    async def process(self, item):
        self.busy = True
        await self.report_state()
        before = self.controller.snapshot()
        opening_pending_before = self.opening_pending
        started = time.monotonic()
        selected = False
        try:
            if item == 'opening':
                if not self.opening_pending or self.controller.flow.paused:
                    return
                await self.emit('status', stage='thinking', message='The prepared welcome is ready; William will invite names and thankfulness.')
                prompts = await self.controller.start()
                prompts.append(Prompt('f.001'))
                self.opening_pending = False
            elif isinstance(item, dict) and 'control' in item:
                action = item['control']
                prompts = self.controller.control(action)
                if action == 'resume':
                    prompts += self.resume_prompts
                    self.resume_prompts = []
                    if self.opening_pending:
                        prompts += await self.controller.start()
                        prompts.append(Prompt('f.001'))
                        self.opening_pending = False
            else:
                await self.emit('status', stage='thinking', message='William is considering the completed contribution through the active Codex session.')
                self.controller.flow.order = item['turn_order']
                context_speaker = item.get('speaker_label') or 'PENDING'
                prompts = await self.controller.accept(item['text'], speaker=context_speaker,
                    solo=item.get('identity_eligible', False), source='voice')
            selected = True
            self.remaining_prompts = list(prompts)
            decision = self.controller.last_decision or Decision('listen')
            await self.emit('decision', input=item.get('text', '') if isinstance(item, dict) else '',
                intent=decision.action, source='voice' if isinstance(item, dict) and 'text' in item else 'control',
                parser=self.controller.provider, model=self.controller.model,
                phase_before=before[0]['phase'], phase_after=self.controller.flow.phase,
                prompts=[p.key for p in prompts], elapsed_ms=round((time.monotonic()-started)*1000, 1), reason=decision.note)
            if any(p.key == 'scripture' for p in prompts):
                await self.emit('scripture_source', **self.provenance)
            await self.speak(prompts)
            self.controller.complete_playback(prompts)
        except asyncio.CancelledError:
            if not selected:
                self.controller.restore(before)
                self.opening_pending = opening_pending_before
            raise
        except Exception as exc:  # noqa: BLE001 -- transactional state and sanitized error
            logging.getLogger(__name__).warning("English turn failed: %s", type(exc).__name__)
            self.controller.restore(before)
            self.opening_pending = opening_pending_before
            await self.emit('cancel_audio')
            await self.fail('turn_failed', 'Facilitation or playback failed. The study did not advance; stopping.', fatal=True)
        finally:
            self.busy = False
            self.last_activity = time.monotonic()
            if not self.closed.is_set():
                await self.report_state()
                await self.emit('status', stage='paused' if self.controller.flow.paused else 'listening', message='Paused.' if self.controller.flow.paused else 'Listening to the group.')

    async def tick(self):
        while not self.closed.is_set():
            await asyncio.sleep(1)
            if time.monotonic()-self.last_input > 15:
                await self.fail('microphone_stalled', 'No microphone packets; stream is stopping.', fatal=True)
            await self.emit('metrics', received_bytes=self.received_bytes, input_seconds=self.accepted_bytes/32000, queue_depth=self.queue.qsize())

    async def run(self):
        try:
            if not self.provenance:
                await self.emit('status', stage='loading_source', message='Verifying official YouVersion NIV and original Waha questions.')
                lesson, self.provenance = await asyncio.wait_for(asyncio.to_thread(build_english_lesson), 60)
                self.controller.lesson = self.controller.flow.lesson = lesson
            self.controller.flow.manual_scripture = False
            await self.emit('scripture_source', **self.provenance)
            await self.emit('status', stage='connecting_stt', message='Source ready; connecting the shared microphone before the opening.')
            self.tasks = [asyncio.create_task(self.consume()), asyncio.create_task(self.tick())]
            await self.provider.run(self.on_provider, self.closed, max_seconds=1800)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 -- source/provider boundary
            if not self.closed.is_set():
                await self.fail('english_unavailable', 'Official NIV, canonical questions or AssemblyAI could not be verified. No substitute/generated text was used.', fatal=True)
        finally:
            self.closed.set()
            if self.active:
                self.active.cancel()
            for task in self.tasks:
                task.cancel()
            await asyncio.gather(*self.tasks, return_exceptions=True)
            if self.active:
                await asyncio.gather(self.active, return_exceptions=True)
            await self.controller.close()
            self.ledger.clear()
            self.controller.flow.reported_names.clear()
            self.remaining_prompts.clear()
            self.resume_prompts.clear()
            self.stream = None
            if not self.ws.closed:
                await self.emit('cancel_audio')
                await self.emit('ended', message='Study stopped; AssemblyAI stream and temporary identities cleared.')
                await self.ws.close()
