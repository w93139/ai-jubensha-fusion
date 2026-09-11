"""Explicit 1.4 whole-game rules, preserving all earlier package engines."""
from copy import deepcopy
import unicodedata

from src.fusion.package_memory_rules import PackageMemoryRules
from src.fusion.package_play_rules import PlayRulesError
from src.fusion.structured_finale import StructuredFinale
from src.fusion.table_decisions import CollectiveRound
from src.schemas.package_primitives import normalize_heard_text

RULES_CONTRACT = 'package-play-rules/1.3'


class PackageFullPlayRules(PackageMemoryRules):
    _schema_versions = ('script-package/1.4',)

    def __init__(self, package, human_character_id):
        super().__init__(package, human_character_id)
        self._turn = 0
        self._round = None
        self._closed_investigations = set()
        self._resolved_rounds = []
        self._call = None
        self._call_number = 0
        self._private_messages = []
        self._finale = None
        finale = self._package['full_play']['finale']
        options = [*finale['votes']['identities'], *(o for q in finale['questions'] for o in q['options'])]
        self._term_catalog = {normalize_heard_text(t) for o in options for t in o.get('heard_terms', [])}
        self._heard_terms = {actor: set() for actor in self._characters}

    def _phase_kind(self):
        phase = self._phases[self._phase_index]['id']
        return next(p['kind'] for p in self._package['full_play']['phases'] if p['phase_id'] == phase)

    def _options(self):
        return [a for a in self._actions.values() if self._available(a, self._human)]

    def _can_finish(self):
        if self._settled or getattr(self, '_call', None) is not None or getattr(self, '_round', None) is not None:
            return False
        kind = self._phase_kind()
        if kind == 'READING':
            return True
        if kind == 'FINALE':
            return bool(self._finale and self._finale.view(self._human)['all_sealed'] and self._finale.result()['complete'])
        return self._phases[self._phase_index]['id'] in self._closed_investigations or not self._options()

    def apply(self, action, target=None, actor_character_id=None):
        if action == 'PERFORM_ACTION':
            raise PlayRulesError('FULL_PLAY_COLLECTIVE_VOTE_REQUIRED')
        if action == 'SHARE_MATERIAL':
            self.require_discussion()
        if action == 'SETTLE' and not self._can_finish():
            raise PlayRulesError('FULL_PLAY_FINALE_INCOMPLETE')
        super().apply(action, target, actor_character_id)
        if action == 'ADVANCE_PHASE' and self._phase_kind() == 'FINALE':
            self._finale = StructuredFinale(self._package['full_play']['finale'],
                {s['id'] for s in self._package['sources']}, {m['id']: m['character_id'] for m in self._package['memories']},
                {t['id'] for t in self._package['truth']}, set(self._memory_grants),
                {actor: {(m['collection'], m['id']) for m in self.proposal_context(actor)['materials']}
                 for actor in self._characters}, self._heard_terms)

    def require_discussion(self):
        if self._settled or self._phase_kind() == 'FINALE':
            raise PlayRulesError('FULL_PLAY_DISCUSSION_CLOSED')

    def apply_reply(self, actor, refs):
        self.require_discussion()
        return super().apply_reply(actor, refs)

    def table_apply(self, action: str, actor: str, payload: dict | None, sequence: int):
        # Domain callers get the same all-or-nothing behavior as DB events.
        candidate = self._transaction_copy()
        candidate._table_apply(action, actor, payload, sequence)
        self.__dict__ = candidate.__dict__

    def _transaction_copy(self):
        # The constructor owns a copy of the validated package. These package
        # indexes are read-only throughout play, so transactions can share them.
        # Every mutable grant, ballot, call, memory and finale is still copied;
        # failed transitions therefore cannot alter the original game state.
        # Do not share defaultdict indexes: even a lookup may insert a key.
        static = ('_package', '_characters', '_phases', '_items', '_actions',
                  '_budgets', '_memories', '_ranks', '_term_catalog')
        return deepcopy(self, {id(getattr(self, key)): getattr(self, key) for key in static})

    def _table_apply(self, action, actor, payload, sequence):
        self._character(actor)
        if self._settled or type(sequence) is not int or sequence <= self._observed_sequence:
            raise PlayRulesError('FULL_PLAY_ACTION_INVALID')
        kind = self._phase_kind()
        phase = self._phases[self._phase_index]['id']
        if action == 'SEAL_FINALE':
            if kind != 'FINALE' or self._finale is None:
                raise PlayRulesError('FULL_PLAY_FINALE_NOT_READY')
            self._finale.seal(actor, payload)
            return
        self.require_discussion()
        if kind != 'INVESTIGATION':
            raise PlayRulesError('FULL_PLAY_INVESTIGATION_NOT_READY')
        if action == 'OPEN_BALLOT':
            if payload is not None or actor != self._human or self._round is not None or self._call is not None or phase in self._closed_investigations:
                raise PlayRulesError('FULL_PLAY_BALLOT_NOT_AVAILABLE')
            orders = {a['action_id']: a['order'] for a in self._package['full_play']['action_order']}
            self._round = CollectiveRound(list(self._characters), [{'id': a['id'], 'order': orders[a['id']]} for a in self._options()],
                                          list(self._characters)[self._turn % len(self._characters)])
            return
        if action in ('CAST_BALLOT', 'BREAK_TIE'):
            if self._round is None:
                raise PlayRulesError('FULL_PLAY_BALLOT_NOT_OPEN')
            if action == 'CAST_BALLOT':
                self._round.cast(actor, payload)
            else:
                if type(payload) is not dict or set(payload) != {'choice_id'}:
                    raise PlayRulesError('FULL_PLAY_ACTION_INVALID')
                self._round.break_tie(actor, payload['choice_id'])
            result = self._round.outcome()
            if result['status'] == 'CHOSEN':
                # Only this resolved collective decision can invoke the old
                # deterministic cost/grant transition; model text cannot.
                super().apply('PERFORM_ACTION', {'action_id': result['choice_id']}, self._human)
                self._turn += 1
            elif result['status'] == 'SKIPPED':
                self._closed_investigations.add(phase)
            else:
                return
            self._resolved_rounds.append({'phase_id': phase, 'sequence': sequence,
                                         'ballot': self._round.state(), 'outcome': result})
            self._round = None
            return
        if action == 'START_CALL':
            if (type(payload) is not dict or set(payload) != {'peer_character_id'} or self._call is not None or self._round is not None):
                raise PlayRulesError('FULL_PLAY_CALL_UNAVAILABLE')
            peer = payload['peer_character_id']
            self._character(peer)
            if peer == actor:
                raise PlayRulesError('FULL_PLAY_CALL_PEER_INVALID')
            self._call_number += 1
            self._call = {'id': f'call-{self._call_number}', 'character_ids': [actor, peer]}
            return
        if action in ('STOP_CALL', 'PRIVATE_SPEAK'):
            if self._call is None or actor not in self._call['character_ids']:
                raise PlayRulesError('FULL_PLAY_CALL_NOT_PARTICIPANT')
            if action == 'STOP_CALL':
                if payload is not None:
                    raise PlayRulesError('FULL_PLAY_ACTION_INVALID')
                self._call = None
            else:
                if (type(payload) is not dict or set(payload) != {'text'} or type(payload['text']) is not str
                        or not payload['text'].strip() or len(payload['text']) > 1000
                        or any(unicodedata.category(c) in ('Cc', 'Cf', 'Cs') and c not in '\n\t' for c in payload['text'])):
                    raise PlayRulesError('FULL_PLAY_PRIVATE_TEXT_INVALID')
                self._private_messages.append({'id': f'private-{sequence}', 'sequence': sequence, 'phase_id': phase,
                    'speaker': actor, 'text': payload['text'].strip(), 'kind': 'CLAIM',
                    'call_id': self._call['id'], 'audience': self._call['character_ids'][:]})
            return
        raise PlayRulesError('FULL_PLAY_ACTION_INVALID')

    def guided_investigate(self, action_id=None, *, finish=False):
        """Explicit human command; no fabricated collective or model ballots."""
        candidate = self._transaction_copy()
        candidate.require_discussion()
        phase = candidate._phases[candidate._phase_index]['id']
        if (candidate._phase_kind() != 'INVESTIGATION' or candidate._call is not None
                or (phase in candidate._closed_investigations and not finish)):
            raise PlayRulesError('GUIDED_INVESTIGATION_NOT_AVAILABLE')
        if not finish:
            super(PackageFullPlayRules, candidate).apply('PERFORM_ACTION', {'action_id': action_id}, candidate._human)
        elif action_id is not None:
            raise PlayRulesError('GUIDED_INVESTIGATION_NOT_AVAILABLE')
        if candidate._round is not None:
            candidate._resolved_rounds.append({'phase_id': phase, 'sequence': candidate._observed_sequence + 1,
                'ballot': candidate._round.state(), 'outcome': {'status': 'REPLACED_BY_HUMAN_CHOICE',
                    'choice_id': action_id, 'policy': 'package-guided-play/1.0'}})
            candidate._round = None
        if finish:
            candidate._closed_investigations.add(phase)
        self.__dict__ = candidate.__dict__

    def guided_material(self, collection, identifier, actor):
        self._character(actor)
        if actor == self._human or self._settled or self._phase_kind() != 'INVESTIGATION':
            raise PlayRulesError('GUIDED_RETELLING_NOT_AVAILABLE')
        if collection == 'memory':
            item = self._memories.get(identifier)
            acquired = identifier in self._memory_grants
        elif collection in ('knowledge', 'evidence'):
            item = self._items[collection].get(identifier)
            acquired = (collection, identifier) in self._unlocked
        else:
            raise PlayRulesError('GUIDED_RETELLING_NOT_AVAILABLE')
        if (not item or item['character_id'] != actor or not acquired
                or (item.get('retelling') != 'MUST_RETELL' and item.get('disclosure') != 'MUST_SHARE')):
            raise PlayRulesError('GUIDED_RETELLING_NOT_AVAILABLE')
        return deepcopy(item)

    def personal_discussion(self, actor):
        self._character(actor)
        return deepcopy([m for m in self._private_messages if actor in m['audience']])

    def phone_context(self, idle_turn):
        """Anonymous controller selects a seat; callers cannot probe other lines."""
        self.require_discussion()
        if self._phase_kind() != 'INVESTIGATION' or self._round is not None:
            raise PlayRulesError('FULL_PLAY_CALL_UNAVAILABLE')
        peers = [dict(id=c['id'],name=c['name']) for c in self._package['characters']]
        if self._call is None:
            actor = [c['id'] for c in peers if c['id'] != self._human][idle_turn % 4]
            context = self.proposal_context(actor)
            return {k:context[k] for k in ('character','current_phase')} | dict(stage='IDLE',
                peers=[c for c in peers if c['id']!=actor], planning_materials=context['materials'],
                materials=[], reply_to=None, private_claims=self.personal_discussion(actor))
        messages = [m for m in self._private_messages if m['call_id']==self._call['id']]
        if not messages:
            raise PlayRulesError('FULL_PLAY_CALL_WAITING_FOR_SPEECH')
        actor = next(c for c in self._call['character_ids'] if c!=messages[-1]['speaker'])
        if actor == self._human:
            raise PlayRulesError('FULL_PLAY_CALL_WAITING_FOR_HUMAN')
        context=self.dialogue_context(actor)
        return {**context,'stage':'CONNECTED','peers':[c for c in peers if c['id'] in self._call['character_ids'] and c['id']!=actor],
                'planning_materials':[], 'reply_to':messages[-1]['id'], 'private_claims':deepcopy(messages)}

    def apply_phone(self, actor, decision, sequence):
        from src.fusion.package_call_model import call_text
        candidate=self._transaction_copy()
        kind=decision['kind']
        if kind=='INVITE':
            candidate._table_apply('START_CALL',actor,{'peer_character_id':decision['peer_character_id']},sequence)
            # Fixed transport greeting, never a model-generated factual claim.
            candidate._table_apply('PRIVATE_SPEAK',actor,{'text':'想与你单独核对一下调查情况。'},sequence)
            candidate._private_messages[-1]['origin']='PROGRAM_OPENING'
        elif kind=='SPEAK':
            candidate._table_apply('PRIVATE_SPEAK',actor,{'text':call_text(decision)},sequence)
        elif kind=='END':candidate._table_apply('STOP_CALL',actor,None,sequence)
        elif kind!='PASS':raise PlayRulesError('FULL_PLAY_CALL_ACTION_INVALID')
        self.__dict__=candidate.__dict__

    def pause_phone(self, sequence):
        if self._call is None or self._human in self._call['character_ids']:
            raise PlayRulesError('FULL_PLAY_PHONE_PAUSE_UNAVAILABLE')
        self.table_apply('STOP_CALL',self._call['character_ids'][0],None,sequence)

    def decision_context(self, actor, action):
        """Only the acting seat's legal choice/form; no other ballots or keys."""
        self._character(actor)
        if actor == self._human or self._settled:
            raise PlayRulesError('FULL_PLAY_AI_CHARACTER_INVALID')
        context = self.proposal_context(actor)
        context.pop('options')
        choices, questions, accusations, trusted = [], [], [], []
        if action in ('CAST_BALLOT', 'BREAK_TIE'):
            if self._round is None:
                raise PlayRulesError('FULL_PLAY_BALLOT_NOT_OPEN')
            view = self._round.view(actor)
            if action == 'CAST_BALLOT':
                if view['ballot'] is not None or view['status'] != 'WAITING':
                    raise PlayRulesError('FULL_PLAY_BALLOT_ALREADY_SUBMITTED')
                ids = {c['id'] for c in self._round.state()['choices']}
            else:
                if view['status'] != 'TIE' or view['decider'] != actor:
                    raise PlayRulesError('FULL_PLAY_TIE_NOT_AVAILABLE')
                ids = set(view['tied_choice_ids'])
            choices = [{k: a[k] for k in ('id', 'label', 'cost')} for a in self._options() if a['id'] in ids]
        elif action == 'SEAL_FINALE':
            if self._finale is None:
                raise PlayRulesError('FULL_PLAY_FINALE_NOT_READY')
            view = self._finale.view(actor)
            if view['sealed']:
                raise PlayRulesError('FULL_PLAY_FINALE_ALREADY_SEALED')
            questions = view['questions']
            accusations = view['votes']['accusation_options']
            trusted = view['votes']['trust_character_ids']
        else:
            raise PlayRulesError('FULL_PLAY_DECISION_INVALID')
        return {**context, 'action': action, 'options': choices, 'questions': questions,
                'accusation_options': accusations, 'trust_character_ids': trusted}

    def private_reply_context(self, actor, reply_to):
        self.require_discussion()
        self._character(actor)
        if (actor == self._human or self._call is None or actor not in self._call['character_ids']
                or self._human not in self._call['character_ids']
                or not any(m['id'] == reply_to and m['speaker'] != actor and m['call_id'] == self._call['id']
                           and actor in m['audience'] for m in self._private_messages)):
            raise PlayRulesError('FULL_PLAY_PRIVATE_REPLY_UNAVAILABLE')
        return self.dialogue_context(actor)

    def speech_strategy(self, actor):
        """Own unlocked, non-speakable goals/behavior; never a speech citation catalog."""
        self._character(actor)
        return [{'collection':'knowledge', 'id':identifier, 'text':item['text'], 'kind':item['kind']}
                for identifier, item in self._items['knowledge'].items()
                if item['character_id'] == actor and item['disclosure'] == 'KEEP_PRIVATE'
                and item.get('retelling') is None and ('knowledge', identifier) in self._unlocked]

    def dialogue_context(self, actor):
        context = super().dialogue_context(actor)
        context['materials'].extend({'collection': 'knowledge', 'id': identifier, 'text': item['text'],
            'kind': item['kind'], 'retelling': item['retelling']} for identifier, item in self._items['knowledge'].items()
            if item.get('retelling') is not None and item['character_id'] == actor and ('knowledge', identifier) in self._unlocked)
        return context

    def observe_event(self, sequence, speech=None):
        phase = self._phases[self._phase_index]['id']
        if (type(sequence) is not int or sequence <= self._observed_sequence
                or (speech is not None and (type(speech) is not dict or set(speech) != {'speaker', 'text', 'phase_id'}
                    or speech['speaker'] not in self._characters or speech['phase_id'] != phase
                    or type(speech['text']) is not str or not speech['text'].strip() or len(speech['text']) > 4000))):
            raise PlayRulesError('PACKAGE_MEMORY_EVENT_INVALID')
        audience = set(self._characters)
        messages = getattr(self, '_private_messages', [])
        if speech is None and messages and messages[-1]['sequence'] == sequence and messages[-1].get('origin') != 'PROGRAM_OPENING':
            speech = messages[-1]
            audience = set(speech['audience'])
        held = {actor: self._held_evidence(actor) for actor in self._characters}
        if not self._settled and self._phase_kind() != 'FINALE':
            normalized = normalize_heard_text(speech['text']) if speech else ''
            if speech:
                matched = {term for term in self._term_catalog if term in normalized}
                for actor in audience:
                    self._heard_terms[actor].update(matched)
            for identifier, memory in self._memories.items():
                if identifier in self._memory_grants or self._ranks[memory['phase_id']] > self._phase_index:
                    continue
                actor = memory['character_id']
                for trigger in memory['triggers']:
                    cause = None
                    if (trigger['kind'] == 'OTHER_HEARD_SPEECH' and speech and actor in audience and actor != speech['speaker']
                            and any(normalize_heard_text(k) in normalized for k in trigger['keywords'])):
                        cause = {'kind': 'OTHER_HEARD_SPEECH', 'speaker': speech['speaker'],
                                 'channel': 'PRIVATE' if 'call_id' in speech else 'PUBLIC'}
                    elif trigger['kind'] == 'ACQUIRED_EVIDENCE' and trigger['evidence_id'] in held[actor] - self._seen_evidence[actor]:
                        cause = {'kind': 'ACQUIRED_EVIDENCE', 'evidence_id': trigger['evidence_id']}
                    if cause is not None:
                        self._memory_grants[identifier] = {'id': identifier, 'sequence': sequence, 'phase_id': phase, 'cause': cause}
                        break
        self._seen_evidence = held
        self._observed_sequence = sequence

    def state(self):
        return {**super().state(), 'schema_version': 'package-play-state/1.3',
                'full_play': {'turn': self._turn, 'ballot': self._round.state() if self._round else None,
                    'closed_investigations': sorted(self._closed_investigations), 'resolved_rounds': deepcopy(self._resolved_rounds),
                    'call': deepcopy(self._call), 'call_number': self._call_number,
                    'private_messages': deepcopy(self._private_messages),
                    'heard_terms': {actor: sorted(terms) for actor, terms in self._heard_terms.items()},
                    'finale': self._finale.state() if self._finale else None}}

    def vote_disclosure(self, finale_speeches=()):
        if self._finale is None:
            raise PlayRulesError('FULL_PLAY_FINALE_NOT_READY')
        return self._finale.vote_disclosure(finale_speeches)

    def view(self):
        result = super().view()
        for item in result['private_knowledge']:
            permission = self._items['knowledge'][item['id']].get('retelling')
            if permission:
                item['retelling'] = permission
        ballot = self._round.view(self._human) if self._round else None
        if ballot is not None:
            ballot['choices'] = [{k: self._actions[c['id']][k] for k in ('id', 'label', 'cost')}
                                 for c in self._round.state()['choices']]
        result['full_game'] = {'schema_version': 'full-game-view/1.0', 'phase_kind': self._phase_kind(),
            'ballot': ballot,
            'can_open_ballot': self._phase_kind() == 'INVESTIGATION' and not self._settled and self._round is None
                and self._call is None and self._phases[self._phase_index]['id'] not in self._closed_investigations and bool(self._options()),
            'phone_busy': self._call is not None,
            'call': deepcopy(self._call) if self._call and self._human in self._call['character_ids'] else None,
            'private_discussion': self.personal_discussion(self._human),
            'finale': self._finale.view(self._human) if self._finale else None, 'result': None}
        visible = {(name, item['id']) for name in ('knowledge', 'evidence')
                   for key in ('public_' + name, 'private_' + name) for item in result[key]}
        visible |= {('memory', item['id']) for item in result['memories']['entries']}
        result['visuals'] = [{k: v[k] for k in ('id', 'collection', 'material_id', 'label')}
                             for v in self._package.get('visuals', [])
                             if (v['collection'], v['material_id']) in visible]
        if self._settled:
            finale = self._finale.result()
            truths = {t['id']: t['text'] for t in self._package['truth']}
            result['full_game']['result'] = {'totals': finale['totals'], 'goals': finale['goals'],
                'endings': [{**e, 'texts': [truths[t] for t in e['truth_ids']]} for e in finale['endings']]}
        return result
