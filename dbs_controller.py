"""Provider-neutral generative facilitation and transactional session state."""
import asyncio
from copy import deepcopy

from dbs_conversation import ConversationFlow
from dbs_facilitator import Decision
from dbs_prompts import EN, ES


class ConversationController:
    mode = 'generative'

    def __init__(self, lesson, language='en', *, facilitator, max_people=7, text_mode=False):
        self.lesson = lesson
        self.flow = ConversationFlow(lesson, language, max_people, text_mode=text_mode)
        self.facilitator = facilitator
        self.history = []
        self.last_decision = None
        self._idle_requested = False
        self._started = False
        self._closed = False
        self._revision = 0
        self._transaction = asyncio.Lock()
        self._copy = ES if language == 'es' else EN

    @property
    def provider(self):
        return self.facilitator.provider

    @property
    def model(self):
        return self.facilitator.model

    def render(self, prompt):
        if prompt.key == 'assistant':
            return prompt.values['text']
        if prompt.key == 'scripture':
            return self.lesson.scripture or self._copy['manual_scripture']
        return self.lesson.questions[prompt.key]

    def prompt_origin(self, prompt):
        if prompt.key == 'assistant':
            return prompt.values.get('origin', 'deterministic')
        return 'canonical' if prompt.key != 'scripture' or self.lesson.scripture else 'deterministic'

    def snapshot(self):
        return deepcopy((self.flow.__dict__, self.history, self.last_decision,
                         self._idle_requested, self._started))

    def restore(self, snapshot):
        self._revision += 1
        state, self.history, self.last_decision, self._idle_requested, self._started = deepcopy(snapshot)
        self.flow.__dict__.clear()
        self.flow.__dict__.update(state)

    def _context(self, speaker='', solo=True, identifiers=()):
        pending = self.flow.pending
        ready = bool(identifiers or (pending and pending.speaker == speaker and pending.identifiers))
        return {
            'phase': self.flow.phase, 'index': self.flow.index,
            'current_question_key': self.flow.question_key,
            'current_question': self.render_canonical_question(),
            'roster': [{'name': p.name, 'speaker': p.speaker} for p in self.flow.roster],
            'pending_name': {'name': pending.name, 'speaker': pending.speaker} if pending else None,
            'speaker': speaker, 'solo': solo, 'history': deepcopy(self.history[-16:]),
            'last_action': self.last_decision.action if self.last_decision else '',
            'voice_enrollment_ready': solo and (self.flow.text_mode or (bool(speaker) and speaker != 'UU' and ready)),
            'scripture_available': bool(self.lesson.verses and self.lesson.scripture.strip()),
        }

    def render_canonical_question(self):
        return self.lesson.questions.get(self.flow.question_key, '')

    def _record(self, prompts):
        for prompt in prompts:
            self.history.append({'role': 'assistant', 'text': self.render(prompt)[:4000]})
        self.history[:] = self.history[-16:]
        return prompts

    async def _decide(self, text, *, speaker='', identifiers=(), solo=True, source='voice', event='participant'):
        if self._closed or self.flow.phase == 'done':
            return []
        snapshot = self.snapshot()
        revision = self._revision
        try:
            decision = await self.facilitator.decide(text, self._context(speaker, solo, identifiers), source=source, event=event)
            if revision != self._revision or self._closed:
                return []
            if event != 'participant' and decision.action not in ('respond', 'listen'):
                decision = Decision('listen')
            if event == 'participant':
                self.history.append({'role': 'user', 'text': text[:4000], 'speaker': speaker[:80]})
                self._idle_requested = False
                self.flow.attach_identifiers(speaker, identifiers, solo=solo)
            self.last_decision = decision
            prompts = self.flow.apply(decision, speaker=speaker, identifiers=identifiers, solo=solo)
            if decision.action == 'stop':
                self.history.clear()
                return prompts
            return self._record(prompts)
        except BaseException:
            if revision == self._revision:
                self.restore(snapshot)
            raise

    async def start(self):
        async with self._transaction:
            if self._started:
                return []
            prompts = await self._decide('', event='opening')
            self._started = True
            return prompts

    async def idle(self):
        async with self._transaction:
            if self._idle_requested or self.flow.paused or self.flow.phase != 'lesson':
                return []
            prompts = await self._decide('', event='idle')
            self._idle_requested = True
            return prompts

    async def accept(self, text, *, speaker='', identifiers=(), solo=True, source='voice'):
        async with self._transaction:
            identifiers = tuple(v for v in identifiers if isinstance(v, str) and v.strip())
            if self.flow.text_mode and not speaker:
                speaker = self.flow.pending.speaker if self.flow.pending else f'text-{len(self.flow.roster) + 1}'
            return await self._decide(text, speaker=speaker, identifiers=identifiers, solo=solo, source=source)

    def complete_playback(self, prompts):
        """Acknowledge successful playback of the final canonical closing question.

        Procedural speech, failed/interrupted playback, and batches played after
        moving to another question cannot complete the session.
        """
        if (not self._closed and not self.flow.paused and self.flow.phase == 'closing'
                and self.flow.index == len(self.flow.steps) - 1
                and any(p.key == self.flow.question_key for p in prompts)):
            self._revision += 1
            self.flow.phase = 'done'

    def control(self, action):
        if self._closed:
            return []
        if action not in ('next', 'previous', 'repeat', 'read_scripture', 'pause', 'resume', 'stop', 'safety', 'finish_enrollment'):
            raise ValueError('Unknown control action')
        self._revision += 1
        self.last_decision = Decision('pause' if action == 'safety' else action)
        prompts = self.flow.apply(self.last_decision)
        copy_key = {'pause': 'paused', 'resume': 'resumed', 'stop': 'stopped', 'safety': 'safety'}.get(action)
        if copy_key:
            prompts = [self.flow.words(self._copy[copy_key], self._copy[copy_key])]
        if action == 'stop':
            self.history.clear()
            return prompts
        return self._record(prompts)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        self._revision += 1
        self.flow.apply(Decision('stop'))
        self.history.clear()
        self.last_decision = None
        await self.facilitator.close()
