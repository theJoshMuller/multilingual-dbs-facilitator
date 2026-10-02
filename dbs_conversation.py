"""Navigation/enrollment tools for the LLM facilitator, not a scripted dialogue.

The model owns conversational wording and turn-taking. These tools only preserve
canonical question/Scripture text, real voice bindings, and navigation bounds.
"""
from dataclasses import replace

from dbs_flow import DBSFlow, Participant, Prompt, valid_name


class ConversationFlow(DBSFlow):
    def __init__(self, lesson, language='en', max_people=7, *, text_mode=False):
        super().__init__(lesson.steps, text_mode=text_mode, max_people=max_people, manual_scripture=not lesson.scripture.strip())
        self.lesson = lesson
        self.language = language
        self.index = 0  # The custom opening covers f.001 / thankfulness.
        self.questions = [i for i, key in enumerate(self.steps) if key not in ('f.008', 'scripture')]

    def words(self, english, spanish):
        return Prompt('assistant', {'text': spanish if self.language == 'es' else english, 'origin': 'deterministic'})

    @staticmethod
    def generated(speech):
        return [Prompt('assistant', {'text': speech, 'origin': 'generated'})] if speech else []

    @property
    def question_key(self):
        return self.steps[self.index]

    def start(self):
        raise RuntimeError('Use ConversationController.start for lifecycle facilitation')

    def idle(self):
        raise RuntimeError('Use ConversationController.idle for lifecycle facilitation')

    def attach_identifiers(self, speaker, identifiers, *, solo=True):
        identifiers = tuple(v for v in identifiers if isinstance(v, str) and v.strip())
        if solo and self.pending and self.pending.speaker == speaker and identifiers:
            self.pending = replace(self.pending, identifiers=identifiers)

    def navigate(self, direction):
        before = self.index
        options = [i for i in self.questions if (i > before if direction > 0 else i < before)]
        target = (min(options) if direction > 0 else max(options)) if options else before
        crossing = direction > 0 and before < self.steps.index('scripture') < target
        if crossing and self.manual_scripture:
            target = self.steps.index('scripture')
        self.index = target
        self.phase = 'lesson'
        self.paused = False
        self.contributed.clear()
        # Crossing from fellowship into retelling includes the original story
        # introduction and exact passage; Previous itself does not replay a story.
        if target == len(self.steps) - 1:
            self.phase = 'closing'
        if crossing:
            prompts = [Prompt('f.008')] if 'f.008' in self.steps else []
            prompts.append(Prompt('scripture'))
            if not self.manual_scripture:
                prompts.append(Prompt(self.steps[target]))
            return prompts
        return [Prompt(self.steps[target])]

    def apply(self, decision, *, speaker='', identifiers=(), solo=True):
        identifiers = tuple(v for v in identifiers if isinstance(v, str) and v.strip())
        action, name = decision.action, decision.name
        if self.phase == 'done' and action != 'stop':
            return []
        # Recording an introduction must leave the microphone to the group,
        # even if the model supplies an acknowledgement or next-person invitation.
        speech = [] if action == 'introduce' else self.generated(decision.speech)
        if action == 'stop':
            self.phase = 'done'
            self.roster.clear()
            self.pending = None
            self.contributed.clear()
            self.paused = False
            return speech
        if action == 'pause':
            self.paused = True
            return speech
        if action == 'resume':
            self.paused = False
            return speech
        if self.paused:
            # Clear navigation is also a deliberate way out of a paused reading.
            if action not in ('next', 'previous', 'repeat', 'read_scripture'):
                return speech
            self.paused = False
        if action in ('introduce', 'clarify_name'):
            if solo and self.pending and self.pending.speaker == speaker and not identifiers:
                identifiers = self.pending.identifiers
            if not solo or not speaker or speaker == 'UU' or not valid_name(name):
                return [self.words('Could you tell me your name, one person at a time?', '¿Me dices tu nombre, una persona a la vez?')]
            existing = next((p for p in self.roster if p.speaker == speaker), None)
            if existing:
                if existing.name.casefold() == name.casefold():
                    if action == 'introduce' and self.pending and self.pending.speaker == speaker:
                        if not (self.text_mode or set(existing.identifiers).intersection(identifiers)):
                            return [self.words('I may have mixed up the voices. Could you clarify who is speaking?', 'Puede que haya confundido las voces. ¿Quién está hablando?')]
                        self.pending = None
                        if self.phase == 'confirm_name':
                            self.phase = 'introductions'
                    return speech
                if not (self.text_mode or set(existing.identifiers).intersection(identifiers)):
                    return [self.words('I may have mixed up the voices. Could you clarify who is speaking?', 'Puede que haya confundido las voces. ¿Quién está hablando?')]
            pending_other = self.pending if self.pending and self.pending.speaker != speaker else None
            reserved = bool(pending_other and not any(p.speaker == pending_other.speaker for p in self.roster))
            if not existing and len(self.roster) + reserved >= self.max_people:
                return [self.words(f'The participant limit is {self.max_people}; let us clarify the introductions already shared.', f'El límite es de {self.max_people} participantes; aclaremos las presentaciones que ya escuchamos.')]
            if (pending_other and pending_other.name.casefold() == name.casefold()) or any(
                    p is not existing and p.name.casefold() == name.casefold() for p in self.roster):
                return [self.words('Could you use a distinct name so I can keep track of everyone?', '¿Puedes usar un nombre distinto para reconocer a cada persona?')]
            if not self.text_mode and not identifiers:
                return [self.words(f'Thanks, {name}. Tell us a little more about what you are thankful for while I get familiar with your voice.', f'Gracias, {name}. Cuéntanos un poco más sobre lo que agradeces mientras reconozco tu voz.')]
            person = Participant(name, speaker, identifiers)
            if action == 'clarify_name':
                if self.pending and self.pending.speaker != speaker:
                    return [self.words('Let me hear from the person who just introduced themselves.', 'Escuchemos a la persona que acaba de presentarse.')]
                self.pending = person
                self.phase = 'confirm_name'
                return speech or [self.words(f'{name}—did I catch your name correctly?', f'{name}, ¿entendí bien tu nombre?')]
            # A clear self-introduction is enough; only uncertainty needs a gate.
            if existing:
                self.roster[self.roster.index(existing)] = person
            else:
                self.roster.append(person)
            if self.pending and self.pending.speaker == speaker:
                self.pending = None
            if self.phase == 'confirm_name' and not self.pending:
                self.phase = 'introductions'
            return speech
        if action in ('confirm_name', 'reject_name'):
            if not self.pending:
                return speech
            if not solo or speaker != self.pending.speaker:
                return [self.words('Let me hear from the person who just introduced themselves.', 'Escuchemos a la persona que acaba de presentarse.')]
            if action == 'reject_name':
                self.pending = None
                self.phase = 'introductions'
                return speech
            if not self.text_mode and not self.pending.identifiers:
                return [self.words('Tell us a little more so I can recognize your voice.', 'Cuéntanos un poco más para poder reconocer tu voz.')]
            existing = next((p for p in self.roster if p.speaker == speaker), None)
            if not existing and len(self.roster) >= self.max_people:
                return [self.words(f'The participant limit is {self.max_people}; let us clarify the introductions already shared.', f'El límite es de {self.max_people} participantes; aclaremos las presentaciones que ya escuchamos.')]
            if any(p is not existing and p.name.casefold() == self.pending.name.casefold() for p in self.roster):
                return [self.words('Could you use a distinct name so I can keep track of everyone?', '¿Puedes usar un nombre distinto para reconocer a cada persona?')]
            if existing:
                self.roster[self.roster.index(existing)] = self.pending
            else:
                self.roster.append(self.pending)
            self.pending = None
            self.phase = 'introductions'
            return speech
        if action == 'finish_enrollment':
            if name:
                # The final turn can contain both its own name and group readiness.
                introduction = self.apply(replace(decision, action='introduce', speech=''),
                                          speaker=speaker, identifiers=identifiers, solo=solo)
                if introduction:
                    return introduction
            if not self.roster or self.pending:
                return [self.words('Let us finish the introductions first.', 'Terminemos primero las presentaciones.')]
            self.pending = None
            if self.phase == 'introductions' or self.phase == 'confirm_name':
                self.index = 0
                return [*speech, *self.navigate(1)]
            return speech
        if action in ('next', 'previous'):
            self.pending = None
            return [*speech, *self.navigate(1 if action == 'next' else -1)]
        if action == 'repeat':
            # Always return the WHOLE original question, never the generic redirect.
            return [*speech, Prompt(self.question_key)]
        if action == 'read_scripture':
            return [*speech, Prompt('scripture')]
        if action == 'grounding_challenge':
            # Evidence is checked by the brain; the current canonical question
            # remains visible and is repeated after the gentle, generated challenge.
            return [*speech, Prompt(self.question_key)]
        if action in ('respond', 'listen'):
            if speaker:
                self.contributed.add(speaker)
            return speech if action == 'respond' else []
        raise ValueError('Unknown facilitation action')
