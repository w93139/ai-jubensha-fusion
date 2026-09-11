"""Versioned finale statements, on the existing durable play/usage ledger."""
import asyncio
from copy import deepcopy
from dataclasses import replace

from sqlalchemy.exc import IntegrityError, OperationalError

from src.db.models.package_play import ScriptPackagePlay
from src.fusion.budget import UsageAmount, usage_metadata_is_valid
from src.fusion.context_window import bounded_context
from src.fusion.package_dialogue_model import (
    FINALE_MOTIVATION_POLICY, FINALE_MOTIVATION_POLICIES, FinaleMotivationModel, finale_motivation_metadata,
    validate_finale_motivation, finale_motivation_input_size,
)
from src.fusion.package_full_play_rules import PackageFullPlayRules
from src.fusion.package_guided_flow import valid_hash
from src.fusion.package_role_model import PackageRoleModelError
from src.fusion.package_validation import content_hash

FINALE_BINDING_CONTRACT = 'package-text-play-binding/1.3'
COMMAND = 'package-finale-motivation-command/1.0'
START = 'package-finale-motivation-start/1.0'
OUTPUT_TOKENS = 192
SKIP_REASONS = {'MODEL_UNAVAILABLE', 'INPUT_UNAVAILABLE', 'BUDGET_EXCEEDED', 'QUESTION_LIMIT', 'EVENT_LIMIT'}


def play_error(code):
    # Imported only when executing; package_play owns the public error type.
    from src.fusion.package_play import PackagePlayError
    return PackagePlayError(code)


def require(value):
    if not value:
        raise ValueError('FINALE_MOTIVATION_EVENT_INVALID')


def unfinished(state):
    return any(entry['status'] in ('READY', 'PENDING') for entry in state.finale_speeches)


class FinaleMotivationMixin:
    def _setup_finale_motivation(self, policy, full_settings):
        if policy not in (None, *FINALE_MOTIVATION_POLICIES):
            raise play_error('FINALE_MOTIVATION_POLICY_INVALID')
        self.finale_policy = policy
        self.finale_model = FinaleMotivationModel(client=getattr(self.model, 'client', None),
            settings=replace(full_settings, max_output_tokens=OUTPUT_TOKENS) if full_settings else None,
            policy=policy or FINALE_MOTIVATION_POLICY)

    @staticmethod
    def _finale_event_count(state):
        if not state.finale_speeches:
            return 0
        return 1 + sum(0 if e['status'] == 'READY' else
                       2 if e['status'] in ('DONE', 'EMPTY') and 'request_id' in e else 1
                       for e in state.finale_speeches)

    @staticmethod
    def _finale_base(binding):
        base = {**binding['model'], 'max_input_bytes': binding['full_input']['max_input_bytes']}
        original, reserved = base['max_output_tokens'], base['reserved_output_tokens']
        base.update(max_output_tokens=OUTPUT_TOKENS,
            reserved_output_tokens=reserved - original + OUTPUT_TOKENS if type(original) is int and type(reserved) is int else None)
        return base

    @staticmethod
    def _finale_key(binding, actor=None):
        return content_hash({'play': binding['play_id'], 'policy': binding['finale_motivation_policy'], 'actor': actor})

    def _finale_context(self, state, binding, actor, revision=None):
        require(isinstance(state.engine, PackageFullPlayRules))
        require(actor != binding['selected_character_id'])
        source = state.engine.proposal_context(actor)
        public = [{k: m[k] for k in ('collection', 'id', 'text', 'kind')}
                  for m in source['materials'] if m.get('public')]
        claims = [{k: c[k] for k in ('id', 'sequence', 'speaker', 'text', 'kind')}
                  for c in [*state.discussion, *state.proposals, *state.responses]]
        context = {'schema_version': 'finale-motivation-context/1.0', 'play_id': binding['play_id'],
            'package_hash': binding['package_hash'], 'revision': state.revision if revision is None else revision,
            'character': source['character'], 'current_phase': source['current_phase'],
            'materials': public, 'discussion': sorted(claims, key=lambda c: c['sequence'])}
        if binding['finale_motivation_policy'] == 'finale-motivation/1.1':
            context['schema_version'] = 'finale-motivation-context/1.1'
            completed = state.engine.state()['completed_action_ids']
            actions = {a['id']: a['label'] for a in state.engine._package['mechanics']['actions'] if a['id'] in completed}
            visible = {m['id'] for m in public if m['collection'] == 'evidence'}
            context['evidence_origins'] = [{'id':m['id'], 'labels':[actions[a] for a in m['release'].get('required_action_ids', []) if a in actions]}
                for m in state.engine._package['evidence'] if m['id'] in visible]
        return bounded_context(context, binding['full_input']['max_input_bytes'], finale_motivation_input_size)

    def _finale_reason(self, binding):
        return (self._model_reason(binding)
                or ('CONFIG_CHANGED' if self.finale_model.metadata() != finale_motivation_metadata(self._finale_base(binding), binding['finale_motivation_policy']) else None)
                or (None if self.finale_model.available else 'MODEL_UNAVAILABLE'))

    def _start_finale_motivation(self, row, binding, state):
        if (binding.get('finale_motivation_policy') not in FINALE_MOTIVATION_POLICIES
                or state.finale_speeches or not isinstance(state.engine, PackageFullPlayRules)
                or state.engine.view()['full_game']['phase_kind'] != 'FINALE'):
            return
        self._append(row, binding, state, 'ACTION', {'schema_version': START,
            'expected_revision': state.revision, 'idempotency_key': self._finale_key(binding)},
            {'policy': binding['finale_motivation_policy']})

    def _consume_finale_motivation(self, state, kind, request, data, binding):
        starting = request.get('schema_version') == START
        command = request.get('schema_version') == COMMAND
        pending = state.pending.get(request.get('request_id')) if kind == 'AI_RESULT' else None
        result = pending is not None and pending.get('operation') == 'FINALE_MOTIVATION'
        if not (starting or command or result):
            return False
        require(binding.get('finale_motivation_policy') in FINALE_MOTIVATION_POLICIES
                and isinstance(state.engine, PackageFullPlayRules)
                and state.engine.view()['full_game']['phase_kind'] == 'FINALE'
                and not state.engine.view()['settled'])
        if starting:
            require(kind == 'ACTION' and not state.finale_speeches and not state.pending
                    and request == {'schema_version': START, 'expected_revision': state.revision,
                                    'idempotency_key': self._finale_key(binding)}
                    and data == {'policy': binding['finale_motivation_policy']})
            state.finale_policy = binding['finale_motivation_policy']
            state.finale_speeches = [{'character_id': actor, 'text': '', 'status': 'READY'}
                for actor in state.engine._characters if actor != binding['selected_character_id']]
            return True
        if command:
            actor = request.get('character_id')
            entry = next((e for e in state.finale_speeches if e['character_id'] == actor), None)
            require(entry is not None and entry['status'] == 'READY' and not state.pending
                and request == {'schema_version': COMMAND, 'expected_revision': state.revision,
                    'idempotency_key': self._finale_key(binding, actor), 'character_id': actor,
                    'action': 'SKIP' if kind == 'ACTION' else 'MOTIVATE'})
            if kind == 'ACTION':
                require(set(data) == {'reason'} and data['reason'] in SKIP_REASONS)
                entry.update(status='EMPTY', reason=data['reason'])
                return True
            require(kind == 'AI_REQUEST' and state.questions < self._question_limit(state))
            context = self._finale_context(state, binding, actor)
            require(set(data) == {'context_hash', 'prepared_hash', 'reservation', 'issued_at', 'expires_at', 'model'}
                and data['model'] == finale_motivation_metadata(self._finale_base(binding), binding['finale_motivation_policy'])
                and (state.finale_model is None or state.finale_model == data['model'])
                and data['context_hash'] == content_hash(context) and valid_hash(data['prepared_hash'])
                and type(data['issued_at']) is int and type(data['expires_at']) is int
                and 0 < data['expires_at'] - data['issued_at'] <= 300
                and usage_metadata_is_valid(data['reservation']))
            reserved = UsageAmount.from_metadata(data['reservation'])
            require(0 < reserved.prompt_tokens <= data['model']['max_input_bytes'] + 4096
                and reserved.completion_tokens == data['model']['reserved_output_tokens']
                and not reserved.cached_prompt_tokens and not reserved.reasoning_tokens
                and data['reservation'] == state.policy.amount(reserved.prompt_tokens, reserved.completion_tokens).to_metadata()
                and state.policy.paid_calls_enabled and state.policy.decide(state.used, state.reserved(), reserved).allowed)
            state.finale_model = data['model']
            state.pending[request['idempotency_key']] = {**deepcopy(data), 'revision': state.revision + 1,
                'character_id': actor, 'operation': 'FINALE_MOTIVATION'}
            entry.update(status='PENDING', request_id=request['idempotency_key'])
            state.questions += 1
            return True
        require(request == {'request_id': request['request_id'], 'idempotency_key': request['request_id']}
            and set(data) == {'status', 'refs', 'usage', 'accounted', 'received_at', 'motivation'}
            and data['refs'] == [] and data['status'] in ('OK', 'INVALID', 'UNKNOWN', 'EXPIRED', 'STALE')
            and type(data['received_at']) is int and data['received_at'] >= pending['issued_at'])
        if data['status'] in ('UNKNOWN', 'EXPIRED'):
            require(data['usage'] is None)
        if data['status'] == 'EXPIRED':
            require(data['received_at'] >= pending['expires_at'])
        accounted = self._account(state.policy, pending['reservation'], data['usage'])
        require(data['accounted'] == accounted.to_metadata())
        entry = next(e for e in state.finale_speeches if e['character_id'] == pending['character_id'])
        require(entry['status'] == 'PENDING')
        text = ''
        if data['status'] == 'OK':
            require(state.revision == pending['revision'] and data['received_at'] <= pending['expires_at'] and data['usage'] is not None)
            context = self._finale_context(state, binding, pending['character_id'], pending['revision'] - 1)
            require(content_hash(context) == pending['context_hash'])
            text = validate_finale_motivation(data['motivation'], context)['text']
        else:
            require(data['motivation'] is None)
        entry.update(text=text, status='DONE' if text else 'EMPTY')
        state.used += accounted
        del state.pending[request['request_id']]
        state.last_status = data['status']
        # No speech observation: this never grants memories or changes sealed forms.
        return True

    def _begin_finale_motivation(self, identifier, actor, owner):
        self._lock(ScriptPackagePlay, 'play_id', identifier)
        row = self._row(identifier, owner)
        package, binding = self._resolve(row)
        state = self._replay(row, package, binding)
        entry = next((e for e in state.finale_speeches if e['character_id'] == actor), None)
        if entry is None or entry['status'] not in ('READY', 'PENDING'):
            return self._view(row, package, binding, state), None
        key = self._finale_key(binding, actor)
        if state.pending:
            pending = state.pending.get(key)
            if pending and self.now() >= pending['expires_at']:
                return self._finish(identifier, key, owner, None, expired=True), None
            return self._view(row, package, binding, state), None
        self._current(row)
        reason = ('MODEL_UNAVAILABLE' if self._finale_reason(binding) else
                  'QUESTION_LIMIT' if state.questions >= self._question_limit(state) else
                  None)
        prepared = None
        if reason is None:
            try:
                context = self._finale_context(state, binding, actor)
                prepared = self.finale_model.prepare(context)
            except (PackageRoleModelError, ValueError):
                reason = 'INPUT_UNAVAILABLE'
        if prepared is not None:
            reserved = state.policy.amount(prepared['input_tokens'], prepared['output_tokens'])
            if not state.policy.decide(state.used, state.reserved(), reserved).allowed:
                reason = 'BUDGET_EXCEEDED'
        request = {'schema_version': COMMAND, 'expected_revision': state.revision,
            'idempotency_key': key, 'character_id': actor, 'action': 'SKIP' if reason else 'MOTIVATE'}
        if reason:
            self._append(row, binding, state, 'ACTION', request, {'reason': reason})
            self.db.commit()
            return self._view(row, package, binding, state), None
        issued = int(self.now())
        self._append(row, binding, state, 'AI_REQUEST', request, {
            'context_hash': content_hash(context), 'prepared_hash': content_hash(prepared),
            'reservation': reserved.to_metadata(), 'model': self.finale_model.metadata(),
            'issued_at': issued, 'expires_at': issued + min(300, max(10, int(binding['model'].get('timeout_seconds', 25)) + 15))})
        self.db.commit()
        return key, prepared

    async def complete_finale_motivations(self, identifier, owner):
        """POST/phase-transition continuation; reads and replays never dispatch."""
        view = self.get(identifier, owner)
        if not view.get('finale_motivation') or view['finale_motivation']['complete']:
            return view
        try:
            for entry in view['finale_speeches']:
                first, prepared = self._begin_finale_motivation(identifier, entry['character_id'], owner)
                if prepared is None:
                    view = first
                else:
                    try:
                        result = await self.finale_model.call(prepared)
                    except asyncio.CancelledError:
                        self._finish(identifier, first, owner, None)
                        raise
                    except Exception:
                        result = None
                    view = self._finish(identifier, first, owner, result)
                if view['pending_ai']:
                    break
            return view
        except (OperationalError, IntegrityError):
            self.db.rollback()
            raise play_error('PACKAGE_PLAY_WRITE_CONFLICT') from None
