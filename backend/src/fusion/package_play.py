"""Version-bound text play with durable, one-attempt model reservations.

Normal writes leave commit to the caller. ``ask`` owns two explicit commits:
the reservation before network I/O and the result afterwards. A crashed request
therefore cannot lose its reservation or cause a retry to call the model again.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from decimal import Decimal
from hashlib import sha256
import json
import re
import time
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import case, func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from src.db.models.package_play import ScriptPackagePlay, ScriptPackagePlayEvent
from src.db.models.package_runtime import ScriptPackagePlaySession
from src.fusion.budget import BudgetPolicy, UsageAmount, usage_metadata_is_valid
from src.fusion.package_play_rules import RULES_CONTRACT, PackagePlayRules, PlayRulesError
from src.fusion.package_play_engine import play_engine, rules_contract
from src.fusion.package_investigation_rules import PackageInvestigationRules
from src.fusion.package_memory_rules import PackageMemoryRules
from src.fusion.package_full_play_rules import PackageFullPlayRules
from src.fusion.table_decisions import TableDecisionError
from src.fusion.structured_finale import StructuredFinaleError
from src.fusion.package_role_model import PackageRoleModel, PackageRoleModelError
from src.fusion.package_proposal_model import PackageProposalModel, proposal_metadata, validate_proposal, render_proposal
from src.fusion.package_proposal_model import GuidedPackageProposalModel, GUIDED_PROPOSAL_VERSION, proposal_model_transition
from src.fusion.package_proposal_model import FullPackageProposalModel, full_proposal_metadata, proposal_context_window
from src.fusion.package_dialogue_model import PackageDialogueModel, dialogue_metadata, validate_speech, render_speech
from src.fusion.package_dialogue_model import FullPackageDialogueModel, StrategyFullPackageDialogueModel, CatalogFullPackageDialogueModel, ClarifyingFullPackageDialogueModel, dialogue_context_window
from src.fusion.package_dialogue_model import ExcerptFullPackageDialogueModel, WrappedExcerptFullPackageDialogueModel, PassageFullPackageDialogueModel, CompactPassageFullPackageDialogueModel, TaskPassageFullPackageDialogueModel, FocusedTaskPassageFullPackageDialogueModel, ScopedTaskPackageDialogueModel, scope_retelling_context, refuses_meta_request
from src.fusion.required_retelling import required_retelling_task
from src.fusion.package_table_model import PackageTableModel, BoundPackageTableModel, table_metadata, validate_table_decision, table_context_window
from src.fusion.package_call_model import PackageCallModel, StrategyPackageCallModel, CatalogPackageCallModel, ClarifyingPackageCallModel, call_metadata, validate_call, call_window
from src.fusion.package_call_model import ExcerptPackageCallModel, WrappedExcerptPackageCallModel, PassagePackageCallModel, CompactPassagePackageCallModel, AttributedPackageCallModel
from src.fusion.package_runtime import PackageRuntimeService, PackageRuntimeError, PublicationReader
from src.fusion.package_request_scope import REQUEST_SCOPE_POLICY, refuses_player_request, needs_question_clarification
from src.fusion.package_validation import canonical_json, content_hash
from src.schemas.package_play import CreatePackagePlayRequest, PackagePlayActionRequest, PackagePlayAskRequest, PackagePlaySpeakRequest, PackagePlayProposalRequest, PackagePlayRespondRequest, FullPlayActionRequest
from src.schemas.package_play import FullPlayDecisionRequest, FullPlayPrivateReplyRequest
from src.schemas.package_play import FullPlayPhoneRequest, FullPlayPhonePauseRequest, GuidedPlayRequest, ScriptedRetellingRequest
from src.fusion.package_guided_flow import GUIDED_POLICY, safe_text, digest, valid_hash
from src.fusion.package_round_workspace import plan_round, capture_workspace, project_workspace
from src.schemas.package_play import TopicCommand
from src.fusion.package_single_player import SINGLE_POLICY, topic_response_request, topic_status
from src.fusion.package_role_content import resolve_role_content, role_content_required
from src.fusion.topic_answer_checks import (BASIS_VERSION, BASIS_GUIDANCE, DISCLOSURE_VERSION,
                                            DISCLOSURE_GUIDANCE, validate_topic_answer)


BINDING_CONTRACT = "package-text-play-binding/1.0"
FULL_BINDING_CONTRACT = 'package-text-play-binding/1.2'
EVENT_CONTRACT = "package-text-play-event/1.0"
MAX_QUESTIONS = 30
MAX_STATEMENTS = 100
DISCUSSION_EVENT_CONTRACT = "package-text-play-event/1.1"
PLAY_PATTERN = r"play-[0-9a-f]{32}"


class PackagePlayError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        self.code, self.status_code = code, status_code
        super().__init__(code)


@dataclass
class Replay:
    engine: PackagePlayRules | PackageInvestigationRules
    revision: int
    previous: str
    policy: BudgetPolicy
    pending: dict = field(default_factory=dict)
    used: UsageAmount = field(default_factory=UsageAmount)
    questions: int = 0
    dialogue: list = field(default_factory=list)
    last_status: str | None = None
    discussion: list = field(default_factory=list)
    proposal_model: dict | None = None
    proposals: list = field(default_factory=list)
    proposal_requests: list = field(default_factory=list)
    response_model: dict | None = None
    responses: list = field(default_factory=list)
    response_requests: list = field(default_factory=list)
    retelling_attempts: list = field(default_factory=list)
    table_model: dict | None = None
    table_requests: list = field(default_factory=list)
    private_response_requests: list = field(default_factory=list)
    table_commands: list = field(default_factory=list)
    phone_model: dict | None = None
    phone_requests: list = field(default_factory=list)
    guided_actions: list = field(default_factory=list)
    scripted_retells: list = field(default_factory=list)
    host_hint_entries: list = field(default_factory=list)
    topic_turns: list = field(default_factory=list)
    topic_commands: list = field(default_factory=list)
    # HTTP-only index; deliberately absent from state() and historical hashes.
    workspace_index: dict = field(default_factory=dict)

    def state(self) -> dict:
        result = {"rules": self.engine.state(), "pending": self.pending,
                "used": self.used.to_metadata(), "questions": self.questions,
                "dialogue": [{"character_id": item["character_id"], "refs": item["refs"]}
                             for item in self.dialogue], "last_status": self.last_status}
        # Preserve every legacy initial/event hash until an explicit SPEAK.
        if self.discussion:
            result["discussion"] = {"schema_version": "package-discussion-state/1.0", "entries": self.discussion}
        if self.proposal_model is not None:
            result["proposals"] = {"schema_version": "package-proposal-state/1.0", "model": self.proposal_model,
                                   "entries": self.proposals, "requests": self.proposal_requests}
        if self.response_model is not None:
            result["responses"] = {"schema_version": "package-dialogue-state/1.0", "model": self.response_model,
                "entries": self.responses, "requests": self.response_requests, "retelling_attempts": self.retelling_attempts}
        if self.table_model is not None:
            result['table_decisions'] = {'schema_version': 'package-table-decision-state/1.0',
                'model': self.table_model, 'requests': self.table_requests}
        if self.private_response_requests:
            result['private_responses'] = {'schema_version': 'package-private-dialogue-state/1.0',
                                          'requests': self.private_response_requests}
        if self.table_commands:
            result['full_commands'] = self.table_commands
        if self.phone_model is not None:
            result['phone_turns']={'schema_version':'package-phone-state/1.0','model':self.phone_model,
                'requests':self.phone_requests,'retelling_attempts':self.retelling_attempts}
        if self.guided_actions or self.scripted_retells or self.host_hint_entries:
            result['guided'] = {'schema_version': GUIDED_POLICY, 'actions': self.guided_actions,
                               'retellings': self.scripted_retells, 'hints': self.host_hint_entries}
        if self.topic_commands:
            result['single_player'] = {'turns': self.topic_turns, 'commands': self.topic_commands}
        return result

    def reserved(self) -> UsageAmount:
        result = UsageAmount()
        for item in self.pending.values():
            result += UsageAmount.from_metadata(item["reservation"])
        return result


class PackagePlayService:
    def __init__(self, db: Session, publisher: PublicationReader | None = None,
                 model: PackageRoleModel | None = None, policy: BudgetPolicy | None = None,
                 now=None, proposal_model: PackageProposalModel | None = None,
                 dialogue_model: PackageDialogueModel | None = None, table_model: PackageTableModel | None = None,
                 full_input_bytes: int = 65536, full_table_output_tokens: int = 4096,
                 speech_policy: str = 'role-speech/1.0', table_policy: str = 'package-table-model/1.0',
                 include_interactions: bool = False, request_scope_policy: str | None = None,
                 presentation_repair=None, guided_content=None, single_player_content=None, single_player_required=None):
        if type(include_interactions) is not bool:
            raise PackagePlayError('PACKAGE_PLAY_VIEW_POLICY_INVALID')
        if request_scope_policy not in (None, REQUEST_SCOPE_POLICY):
            raise PackagePlayError('PACKAGE_PLAY_REQUEST_SCOPE_POLICY_INVALID')
        self.include_interactions = include_interactions
        self.presentation_repair = presentation_repair
        self.guided_content = dict(guided_content or {})
        self.single_player_content = dict(single_player_content or {})
        self.single_player_required = dict(single_player_required or {})
        self.request_scope_policy = request_scope_policy
        self.db = db
        self.runtime = PackageRuntimeService(db, publisher)
        self.publisher = self.runtime.publisher
        self.model = model if model is not None else PackageRoleModel()
        self.proposal_model = proposal_model if proposal_model is not None else PackageProposalModel(
            client=getattr(self.model, "client", None), settings=getattr(self.model, "settings", None))
        self.dialogue_model = dialogue_model if dialogue_model is not None else PackageDialogueModel(
            client=getattr(self.model, "client", None), settings=getattr(self.model, "settings", None))
        if type(full_input_bytes) is not int or not 1024 <= full_input_bytes <= 98304:
            raise PackagePlayError('FULL_PLAY_INPUT_POLICY_INVALID')
        self.full_input = {'schema_version': 'full-play-input-policy/1.0', 'max_input_bytes': full_input_bytes}
        if type(full_table_output_tokens) is not int or not 64 <= full_table_output_tokens <= 4096:
            raise PackagePlayError('FULL_PLAY_OUTPUT_POLICY_INVALID')
        self.full_output = {'schema_version': 'full-play-table-output-policy/1.0', 'max_output_tokens': full_table_output_tokens}
        settings = getattr(self.model, 'settings', None)
        full_settings = replace(settings, max_input_bytes=full_input_bytes) if settings is not None else None
        if table_policy not in ('package-table-model/1.0', 'package-table-model/1.1'):
            raise PackagePlayError('FULL_PLAY_TABLE_POLICY_INVALID')
        table_type = BoundPackageTableModel if table_policy == 'package-table-model/1.1' else PackageTableModel
        self.table_model = table_model if table_model is not None else table_type(
            client=getattr(self.model, 'client', None),
            settings=replace(full_settings, max_output_tokens=full_table_output_tokens) if full_settings is not None else None)
        self.full_proposal_model = FullPackageProposalModel(client=getattr(self.model, 'client', None), settings=full_settings)
        self.guided_proposal_model = GuidedPackageProposalModel(client=getattr(self.model, 'client', None), settings=full_settings)
        if speech_policy not in ('role-speech/1.0', 'role-speech/1.1', 'role-speech/1.2', 'role-speech/1.3', 'role-speech/1.4', 'role-speech/1.5', 'role-speech/1.6', 'role-speech/1.7', 'role-speech/1.8', 'role-speech/1.9', 'role-speech/1.10'):
            raise PackagePlayError('FULL_PLAY_SPEECH_POLICY_INVALID')
        dialogue_type = StrategyFullPackageDialogueModel if speech_policy == 'role-speech/1.1' else FullPackageDialogueModel
        phone_type = StrategyPackageCallModel if speech_policy == 'role-speech/1.1' else PackageCallModel
        if speech_policy == 'role-speech/1.2':
            dialogue_type, phone_type = CatalogFullPackageDialogueModel, CatalogPackageCallModel
        if speech_policy == 'role-speech/1.3':
            dialogue_type, phone_type = ClarifyingFullPackageDialogueModel, ClarifyingPackageCallModel
        if speech_policy == 'role-speech/1.4':
            dialogue_type, phone_type = ExcerptFullPackageDialogueModel, ExcerptPackageCallModel
        if speech_policy == 'role-speech/1.5':
            dialogue_type, phone_type = WrappedExcerptFullPackageDialogueModel, WrappedExcerptPackageCallModel
        if speech_policy == 'role-speech/1.6':
            dialogue_type, phone_type = PassageFullPackageDialogueModel, PassagePackageCallModel
        if speech_policy == 'role-speech/1.7':
            dialogue_type, phone_type = CompactPassageFullPackageDialogueModel, CompactPassagePackageCallModel
        if speech_policy == 'role-speech/1.8':
            dialogue_type, phone_type = TaskPassageFullPackageDialogueModel, CompactPassagePackageCallModel
        if speech_policy == 'role-speech/1.9':
            dialogue_type, phone_type = FocusedTaskPassageFullPackageDialogueModel, AttributedPackageCallModel
        if speech_policy == 'role-speech/1.10':
            dialogue_type, phone_type = ScopedTaskPackageDialogueModel, AttributedPackageCallModel
        self.full_dialogue_model = dialogue_type(client=getattr(self.model, 'client', None), settings=full_settings)
        self.phone_model = phone_type(client=getattr(self.model,'client',None),settings=full_settings)
        self.policy = policy if policy is not None else BudgetPolicy.from_env(self.model.metadata()["provider"])
        self.now = now or time.time

    @staticmethod
    def _actor(owner: int) -> None:
        if type(owner) is not int or owner <= 0:
            raise PackagePlayError("PACKAGE_PLAY_IDENTITY_REQUIRED", 401)

    @staticmethod
    def _request(body, schema) -> dict:
        try:
            raw = body.model_dump(exclude_unset=True) if isinstance(body, schema) else body
            result = schema.model_validate(raw).model_dump(exclude_none=schema is not FullPlayActionRequest)
            canonical_json(result).encode("utf-8")
            return result
        except (ValidationError, TypeError, ValueError, UnicodeError):
            raise PackagePlayError("PACKAGE_PLAY_REQUEST_INVALID", 422) from None

    @staticmethod
    def _json(raw: str, digest: str) -> dict:
        result = json.loads(raw)
        if type(result) is not dict or canonical_json(result) != raw or content_hash(result) != digest:
            raise ValueError
        return result

    @staticmethod
    def _budget(snapshot: dict) -> BudgetPolicy:
        policy = BudgetPolicy.from_snapshot(snapshot)
        if (policy is None or policy.to_snapshot() != snapshot
                or type(snapshot["token_limit"]) is not int or type(snapshot["paid_calls_enabled"]) is not bool):
            raise ValueError
        return policy

    def _opening(self, identifier: str, owner: int):
        try:
            return self.runtime.resolve_binding(identifier, owner)
        except PackageRuntimeError as exc:
            code = "PACKAGE_PLAY_OPENING_NOT_FOUND" if exc.status_code == 404 else "PACKAGE_PLAY_SNAPSHOT_INVALID"
            raise PackagePlayError(code, exc.status_code) from None

    def _row(self, identifier: str, owner: int) -> ScriptPackagePlay:
        self._actor(owner)
        if type(identifier) is not str or re.fullmatch(PLAY_PATTERN, identifier) is None:
            raise PackagePlayError("PACKAGE_PLAY_NOT_FOUND", 404)
        row = self.db.query(ScriptPackagePlay).filter_by(play_id=identifier, owner_user_id=owner).populate_existing().one_or_none()
        if row is None:
            raise PackagePlayError("PACKAGE_PLAY_NOT_FOUND", 404)
        return row

    def _resolve(self, row: ScriptPackagePlay) -> tuple[dict, dict]:
        try:
            binding = self._json(row.binding_json, row.binding_hash)
            opening, package = self._opening(row.opening_session_id, row.owner_user_id)
            request = self._request(binding["request"], CreatePackagePlayRequest)
            expected = {"schema_version": BINDING_CONTRACT, "rules_contract": rules_contract(package),
                        "play_id": row.play_id, "owner_user_id": row.owner_user_id,
                        "opening_session_id": opening.session_id, "opening_binding_hash": opening.binding_hash,
                        "release_id": row.release_id, "release_hash": json.loads(opening.binding_json)["release_hash"],
                        "version_id": row.version_id, "package_hash": row.package_hash,
                        "selected_character_id": row.selected_character_id,
                        "request": request, "request_hash": row.request_hash,
                        "model": binding["model"], "budget": binding["budget"],
                        "initial_state_hash": binding["initial_state_hash"]}
            if package.get('schema_version') == 'script-package/1.4':
                full = binding['full_input']
                if (set(full) != {'schema_version', 'max_input_bytes'} or full['schema_version'] != 'full-play-input-policy/1.0'
                        or type(full['max_input_bytes']) is not int or not 1024 <= full['max_input_bytes'] <= 98304):
                    raise ValueError
                expected.update(schema_version=binding['schema_version'], full_input=full)
                if binding['schema_version'] == FULL_BINDING_CONTRACT:
                    output = binding['full_output']
                    if (set(output) != {'schema_version', 'max_output_tokens'}
                            or output['schema_version'] != 'full-play-table-output-policy/1.0'
                            or type(output['max_output_tokens']) is not int or not 64 <= output['max_output_tokens'] <= 4096):
                        raise ValueError
                    expected['full_output'] = output
                elif binding['schema_version'] != 'package-text-play-binding/1.1':
                    raise ValueError
            if (binding != expected or content_hash(request) != row.request_hash
                    or request != {"opening_session_id": row.opening_session_id, "idempotency_key": row.idempotency_key}
                    or any(getattr(row, key) != getattr(opening, key) for key in
                           ("owner_user_id", "release_id", "version_id", "package_hash", "selected_character_id"))):
                raise ValueError
            self._budget(binding["budget"])
            if type(binding["model"]) is not dict:
                raise ValueError
            return package, binding
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise PackagePlayError("PACKAGE_PLAY_SNAPSHOT_INVALID") from None

    def _current(self, row) -> None:
        try:
            release = self.publisher.get_release(row.release_id, require_current=True)
            if (release["id"] != row.release_id or release["version_id"] != row.version_id
                    or release["package_hash"] != row.package_hash
                    or release["release_hash"] != json.loads(row.binding_json)["release_hash"]):
                raise ValueError
        except (ValueError, TypeError, KeyError):
            raise PackagePlayError("PACKAGE_PLAY_RELEASE_UNAVAILABLE") from None
        except OperationalError:
            raise PackagePlayError("PACKAGE_PLAY_WRITE_CONFLICT") from None

    def _lock(self, model, column: str, value: str) -> None:
        if self.db.get_bind().dialect.name == "sqlite":
            result = self.db.execute(text(f"UPDATE {model.__tablename__} SET id = id WHERE {column} = :value"), {"value": value})
            exists = result.rowcount == 1
        else:
            exists = self.db.execute(select(model.id).where(getattr(model, column) == value).with_for_update()).scalar_one_or_none() is not None
        if not exists:
            raise PackagePlayError("PACKAGE_PLAY_WRITE_CONFLICT")

    def _existing(self, request: dict, owner: int):
        row = self.db.query(ScriptPackagePlay).filter_by(owner_user_id=owner, idempotency_key=request["idempotency_key"]).populate_existing().one_or_none()
        if row is not None and row.request_hash != content_hash(request):
            raise PackagePlayError("PACKAGE_PLAY_KEY_CONFLICT")
        return row

    def create(self, body, owner: int) -> dict:
        self._actor(owner)
        request = self._request(body, CreatePackagePlayRequest)
        opening, package = self._opening(request["opening_session_id"], owner)
        existing = self._existing(request, owner)
        if existing is not None:
            return self.get(existing.play_id, owner)
        self._current(opening)
        try:
            self._lock(ScriptPackagePlaySession, "session_id", opening.session_id)
            opening, package = self._opening(request["opening_session_id"], owner)
            existing = self._existing(request, owner)
            if existing is not None:
                return self.get(existing.play_id, owner)
            state = Replay(play_engine(package, opening.selected_character_id), 0, "", self.policy)
            binding = {"schema_version": BINDING_CONTRACT, "rules_contract": rules_contract(package),
                       "play_id": "play-" + uuid4().hex, "owner_user_id": owner,
                       "opening_session_id": opening.session_id, "opening_binding_hash": opening.binding_hash,
                       "release_id": opening.release_id, "release_hash": json.loads(opening.binding_json)["release_hash"],
                       "version_id": opening.version_id, "package_hash": opening.package_hash,
                       "selected_character_id": opening.selected_character_id,
                       "request": request, "request_hash": content_hash(request),
                       "model": self.model.metadata(), "budget": self.policy.to_snapshot(),
                       "initial_state_hash": content_hash(state.state())}
            if isinstance(state.engine, PackageFullPlayRules):
                binding.update(schema_version=FULL_BINDING_CONTRACT, full_input=self.full_input.copy(), full_output=self.full_output.copy())
            row = ScriptPackagePlay(**{key: binding[key] for key in (
                "play_id", "owner_user_id", "opening_session_id", "release_id", "version_id", "package_hash",
                "selected_character_id", "request_hash")}, idempotency_key=request["idempotency_key"],
                binding_json=canonical_json(binding), binding_hash=content_hash(binding))
            with self.db.begin_nested():
                self.db.add(row)
                self.db.flush()
            return self._view(row, package, binding, state)
        except IntegrityError:
            existing = self._existing(request, owner)
            if existing is not None:
                return self.get(existing.play_id, owner)
            raise PackagePlayError("PACKAGE_PLAY_OPENING_CONFLICT") from None
        except OperationalError:
            raise PackagePlayError("PACKAGE_PLAY_WRITE_CONFLICT") from None
        except PlayRulesError as exc:
            raise PackagePlayError(exc.code) from None

    @staticmethod
    def _key(kind: str, request: dict) -> str:
        return content_hash({"kind": kind, "key": request["idempotency_key"]})

    @staticmethod
    def _account(policy: BudgetPolicy, reservation: dict, usage: dict | None) -> UsageAmount:
        if usage is None:
            return UsageAmount.from_metadata(reservation)
        if not usage_metadata_is_valid(usage) or set(usage) != set(UsageAmount().to_metadata()) or usage["cost_cny"] != "0":
            raise ValueError
        # UsageAmount.from_metadata/policy.amount intentionally clamp legacy
        # counters. A receipt here must retain every strictly known token.
        prompt, completion, cached = (usage[key] for key in
                                      ("prompt_tokens", "completion_tokens", "cached_prompt_tokens"))
        cost = (Decimal(prompt - cached) * policy.input_rate_cny
                + Decimal(cached) * policy.cached_input_rate_cny
                + Decimal(completion) * policy.output_rate_cny) / Decimal(1_000_000)
        return UsageAmount(prompt, completion, cached, cost, usage["reasoning_tokens"])

    def _consume(self, state: Replay, kind: str, request: dict, data: dict, binding: dict) -> None:
        if kind == 'ACTION' and request.get('schema_version') == 'package-topic-command/1.0':
            if (request != self._request(request, TopicCommand) or request['expected_revision'] != state.revision
                    or not isinstance(state.engine, PackageFullPlayRules) or state.pending):
                raise ValueError
            state.engine.require_discussion()
            phase = state.engine.state()['current_phase_id']
            sequence = state.revision + 1
            if request['action'] == 'ASK_TOPIC':
                p = request['payload']
                if (set(data) - {'answer_contract'} != {'title','question','basis','disclosure','fallback','catalog_hash'}
                        or not valid_hash(data['catalog_hash']) or not safe_text(data['question'], 800)
                        or not safe_text(data['fallback'], 1000) or not safe_text(data['title'], 120)
                        or p['character_id'] == binding['selected_character_id']
                        or any(all(t[k] == p[k] for k in ('topic_id','character_id','intent_id')) for t in state.topic_turns)):
                    raise ValueError
                known = {(m['collection'],m['id']): m for m in state.engine.dialogue_context(p['character_id'])['materials']}
                if not data['basis'] or any((r['collection'],r['id']) not in known
                        or digest(known[(r['collection'],r['id'])]['text']) != r['text_sha256'] for r in data['basis']):
                    raise ValueError
                if p['channel'] == 'PRIVATE':
                    state.engine.table_apply('PRIVATE_SPEAK', binding['selected_character_id'], {'text':data['question']}, sequence)
                    reply_to = f'private-{sequence}'
                    state.engine.private_reply_context(p['character_id'], reply_to)
                    self._observe_memories(state)
                else:
                    if state.engine.view()['full_game']['phone_busy']:
                        raise PlayRulesError('SINGLE_TOPIC_CALL_ACTIVE')
                    if len(state.discussion) >= self._statement_limit(state):
                        raise PlayRulesError('PACKAGE_PLAY_DISCUSSION_LIMIT')
                    reply_to = f'statement-{sequence}'
                    entry = {'id':reply_to,'sequence':sequence,'phase_id':phase,'kind':'CLAIM',
                             'speaker':binding['selected_character_id'],'text':data['question']}
                    state.discussion.append(entry)
                    self._observe_memories(state, entry)
                state.topic_turns.append({'id': f'topic-{sequence}', 'phase_id':phase, 'sequence':sequence,
                                         'reply_to':reply_to, **deepcopy(p), **deepcopy(data)})
            else:
                turn = next((t for t in state.topic_turns if t['id'] == request['payload']['turn_id']), None)
                if turn is None or turn['phase_id'] != phase or topic_status(turn, state) not in ('READY','FAILED') or data != {'text':turn['fallback']}:
                    raise ValueError
                if turn['channel'] == 'PRIVATE':
                    state.engine.private_reply_context(turn['character_id'], turn['reply_to'])
                    state.engine.table_apply('PRIVATE_SPEAK', turn['character_id'], {'text':data['text']}, sequence)
                    self._observe_memories(state)
                else:
                    if state.engine.view()['full_game']['phone_busy']:
                        raise PlayRulesError('SINGLE_TOPIC_CALL_ACTIVE')
                    if len(state.discussion) >= self._statement_limit(state):
                        raise PlayRulesError('PACKAGE_PLAY_DISCUSSION_LIMIT')
                    entry = {'id':f'statement-{sequence}', 'sequence':sequence, 'phase_id':phase,
                             'kind':'CLAIM','speaker':turn['character_id'],'text':data['text']}
                    state.discussion.append(entry)
                    self._observe_memories(state, entry)
                turn['answer'] = data['text']
                turn['answer_id'] = f"{'private' if turn['channel'] == 'PRIVATE' else 'statement'}-{sequence}"
            state.topic_commands.append({'idempotency_key':request['idempotency_key'], 'action':request['action'], 'sequence':sequence})
            return
        if kind == 'ACTION' and request.get('schema_version') == 'package-scripted-retelling-command/1.0':
            if (request != self._request(request, ScriptedRetellingRequest) or request['expected_revision'] != state.revision
                    or not isinstance(state.engine, PackageFullPlayRules) or state.pending
                    or set(data) != {'policy', 'catalog_hash', 'collection', 'material_id', 'owner', 'text_sha256', 'speech'}
                    or data['policy'] != GUIDED_POLICY or not valid_hash(data['catalog_hash'])
                    or not safe_text(data['speech'], 1500)):
                raise ValueError
            if len(state.discussion) >= self._statement_limit(state):
                raise PlayRulesError('PACKAGE_PLAY_DISCUSSION_LIMIT')
            material = state.engine.guided_material(data['collection'], data['material_id'], data['owner'])
            if digest(material['text']) != data['text_sha256'] or any(
                    (e['collection'], e['material_id'], e['owner']) == (data['collection'], data['material_id'], data['owner'])
                    for e in state.scripted_retells):
                raise ValueError
            if material.get('disclosure') == 'MUST_SHARE':
                state.engine.apply('SHARE_MATERIAL', {'collection': data['collection'], 'id': data['material_id']}, data['owner'])
            entry = {'id': f"statement-{state.revision + 1}", 'sequence': state.revision + 1,
                     'phase_id': state.engine.state()['current_phase_id'], 'kind': 'CLAIM',
                     'speaker': data['owner'], 'text': data['speech']}
            state.discussion.append(entry)
            state.scripted_retells.append({'entry_id': request['entry_id'], 'sequence': state.revision + 1,
                **{k: data[k] for k in ('catalog_hash', 'collection', 'material_id', 'owner')}})
            self._observe_memories(state, entry)
            return
        if kind == 'ACTION' and request.get('schema_version') == 'package-guided-command/1.0':
            if (request != self._request(request, GuidedPlayRequest) or request['expected_revision'] != state.revision
                    or not isinstance(state.engine, PackageFullPlayRules) or state.pending
                    or data.get('policy') != GUIDED_POLICY):
                raise ValueError
            action = request['action']
            if action == 'REQUEST_HINT':
                full = state.engine.view()['full_game']
                if state.engine.state()['settled'] or (full['finale'] and full['finale']['sealed']):
                    raise PlayRulesError('GUIDED_HINT_NOT_AVAILABLE')
                hint = data.get('hint')
                if (set(data) != {'policy', 'catalog_hash', 'hint'} or not valid_hash(data['catalog_hash'])
                        or type(hint) is not dict or set(hint) != {'topic_id', 'title', 'phase_id', 'level', 'text'}
                        or hint['phase_id'] != state.engine.state()['current_phase_id']
                        or hint['topic_id'] != request['payload']['topic_id'] or hint['level'] != request['payload']['level']
                        or not safe_text(hint['text']) or not safe_text(hint['title'], 120)
                        or any(e['topic_id'] == hint['topic_id'] and e['level'] == hint['level'] for e in state.host_hint_entries)):
                    raise ValueError
                state.host_hint_entries.append({**hint, 'sequence': state.revision + 1})
            elif action == 'INVESTIGATE_ROUND':
                batch = plan_round(state.engine, request['payload']['action_ids'])
                if data != {'policy': GUIDED_POLICY, 'batch': batch}:
                    raise ValueError
                for step in batch['steps']:
                    state.engine.guided_investigate(step['action_id'])
            else:
                if data != {'policy': GUIDED_POLICY}:
                    raise ValueError
                if action == 'INVESTIGATE':
                    state.engine.guided_investigate(request['payload']['action_id'])
                elif action == 'FINISH_INVESTIGATION':
                    state.engine.guided_investigate(finish=True)
                elif action == 'PRESENT_REQUIRED':
                    if state.engine.view()['full_game']['phase_kind'] != 'INVESTIGATION' or state.engine.state()['settled']:
                        raise PlayRulesError('GUIDED_RETELLING_NOT_AVAILABLE')
            state.guided_actions.append({'action': action, 'request_id': request['idempotency_key'], 'sequence': state.revision + 1})
            if action == 'INVESTIGATE_ROUND':
                state.guided_actions[-1]['batch'] = deepcopy(batch)
            self._observe_memories(state)
            return
        if kind=='ACTION' and request.get('schema_version')=='package-phone-pause-command/1.0':
            if (request!=self._request(request,FullPlayPhonePauseRequest) or data!={}
                    or request['expected_revision']!=state.revision or not isinstance(state.engine,PackageFullPlayRules)):
                raise ValueError
            state.engine.pause_phone(state.revision+1)
            state.table_commands.append({'request_id':request['idempotency_key'],'revision':state.revision,'action':'PAUSE_PHONE'})
            self._observe_memories(state)
            return
        if kind == 'ACTION' and request.get('schema_version') == 'package-full-play-command/1.0':
            if (request != self._request(request, FullPlayActionRequest) or data != {}
                    or request['expected_revision'] != state.revision):
                raise ValueError
            if not isinstance(state.engine, PackageFullPlayRules):
                raise PlayRulesError('FULL_PLAY_UNSUPPORTED')
            try:
                state.engine.table_apply(request['action'], binding['selected_character_id'], request.get('payload'), state.revision + 1)
            except (TableDecisionError, StructuredFinaleError) as exc:
                raise PlayRulesError(str(exc)) from None
            state.table_commands.append({'request_id': request['idempotency_key'], 'revision': state.revision,
                                         'action': request['action']})
            self._observe_memories(state)
            return
        if kind == "ACTION" and request.get("schema_version") == "package-discussion-command/1.0":
            if (request != self._request(request, PackagePlaySpeakRequest) or data != {}
                    or request["expected_revision"] != state.revision):
                raise ValueError
            if state.engine.state()["settled"]:
                raise PlayRulesError("PACKAGE_PLAY_ALREADY_SETTLED")
            if isinstance(state.engine, PackageFullPlayRules):
                state.engine.require_discussion()
            if len(state.discussion) >= self._statement_limit(state):
                raise PlayRulesError("PACKAGE_PLAY_DISCUSSION_LIMIT")
            state.discussion.append({"id": f"statement-{state.revision + 1}", "sequence": state.revision + 1,
                "phase_id": state.engine.state()["current_phase_id"], "kind": "CLAIM",
                "speaker": binding["selected_character_id"], "text": request["text"]})
            self._observe_memories(state, state.discussion[-1])
            return
        if kind == "ACTION":
            if request != self._request(request, PackagePlayActionRequest) or data != {} or request["expected_revision"] != state.revision:
                raise ValueError
            state.engine.apply(request["action"], request.get("target"))
            self._observe_memories(state)
            return
        if kind == "AI_REQUEST":
            calling = request.get('schema_version') == 'package-phone-command/1.0'
            deciding = calling or request.get('schema_version') == 'package-table-decision-command/1.0'
            if isinstance(state.engine, PackageFullPlayRules) and not deciding:
                state.engine.require_discussion()
            proposing = request.get("schema_version") == "package-investigation-command/1.0"
            private = request.get('schema_version') == 'package-private-dialogue-command/1.0'
            responding = private or request.get("schema_version") == "package-dialogue-command/1.0"
            schema = FullPlayPhoneRequest if calling else FullPlayPrivateReplyRequest if private else FullPlayDecisionRequest if deciding else PackagePlayRespondRequest if responding else PackagePlayProposalRequest if proposing else PackagePlayAskRequest
            if (request != self._request(request, schema) or request["expected_revision"] != state.revision
                    or request.get("character_id") == binding["selected_character_id"]
                    or state.pending or state.questions >= self._question_limit(state) or state.engine.view()["settled"]):
                raise ValueError
            context = (self._phone_context(state,binding,version=data.get('model',{}).get('schema_version')) if calling else self._table_context(state, binding, request['character_id'], request['action'], version=data.get('model', {}).get('schema_version')) if deciding
                       else self._dialogue_context(state, binding, request["character_id"], request["reply_to"], private=private, version=data.get("model",{}).get("schema_version")) if responding
                       else self._proposal_context(state, binding, request["character_id"]) if proposing
                       else state.engine.role_context(request["character_id"]))
            if proposing:
                full_proposal = isinstance(state.engine, PackageFullPlayRules)
                expected_proposal = (full_proposal_metadata(self._full_base(binding), data.get('model', {}).get('schema_version'))
                    if full_proposal else proposal_metadata(binding['model']))
                if (data.get('model') != expected_proposal or not (proposal_model_transition(state.proposal_model,
                    expected_proposal, self._full_base(binding)) if full_proposal else
                    state.proposal_model is None or state.proposal_model == expected_proposal)):
                    raise ValueError
            if responding and (type(data.get("model")) is not dict
                               or data["model"] != dialogue_metadata(self._full_base(binding) if isinstance(state.engine, PackageFullPlayRules) else binding['model'], data['model'].get('schema_version'))
                               or (isinstance(state.engine, PackageFullPlayRules) and data['model'].get('schema_version') not in ('package-dialogue-model/1.3', 'package-dialogue-model/1.4', 'package-dialogue-model/1.5', 'package-dialogue-model/1.6', 'package-dialogue-model/1.7', 'package-dialogue-model/1.8', 'package-dialogue-model/1.9', 'package-dialogue-model/1.10', 'package-dialogue-model/1.11', 'package-dialogue-model/1.12', 'package-dialogue-model/1.13'))
                               or (state.response_model is not None and state.response_model != data["model"])):
                raise ValueError
            if deciding and not calling and (data.get('model') != table_metadata(self._full_table_base(binding), data.get('model', {}).get('schema_version'))
                             or (state.table_model is not None and state.table_model != data['model'])):
                raise ValueError
            if calling and (data.get('model') != call_metadata(self._full_base(binding),data.get('model',{}).get('schema_version'))
                            or (state.phone_model is not None and state.phone_model != data['model'])):
                raise ValueError
            if (set(data) != {"context_hash", "prepared_hash", "reservation", "issued_at", "expires_at"} | ({"model"} if proposing or responding or deciding else set())
                    or data["context_hash"] != content_hash(context)
                    or type(data["prepared_hash"]) is not str or re.fullmatch(r"[0-9a-f]{64}", data["prepared_hash"]) is None
                    or type(data["issued_at"]) is not int or type(data["expires_at"]) is not int
                    or not 0 < data["expires_at"] - data["issued_at"] <= 300
                    or not usage_metadata_is_valid(data["reservation"])):
                raise ValueError
            reserved = UsageAmount.from_metadata(data["reservation"])
            if (reserved.prompt_tokens <= 0 or reserved.completion_tokens <= 0
                    or reserved.prompt_tokens > (self._full_base(binding) if isinstance(state.engine, PackageFullPlayRules) and (proposing or responding or deciding) else binding["model"])["max_input_bytes"] + binding["model"]["chat_token_envelope"]
                    or reserved.completion_tokens != (self._full_table_base(binding) if deciding and not calling else binding['model'])["reserved_output_tokens"]
                    or reserved.cached_prompt_tokens or reserved.reasoning_tokens
                    or data["reservation"] != state.policy.amount(reserved.prompt_tokens, reserved.completion_tokens).to_metadata()
                    or not state.policy.paid_calls_enabled
                    or not state.policy.decide(state.used, state.reserved(), reserved).allowed):
                raise ValueError
            state.pending[request["idempotency_key"]] = {**data, "revision": state.revision + 1,
                                                        "character_id": context['character']['id'] if calling else request["character_id"]}
            if proposing:
                state.proposal_model = data["model"]
                state.pending[request["idempotency_key"]]["operation"] = "PROPOSE"
                state.proposal_requests.append({"request_id": request["idempotency_key"],
                    "character_id": request["character_id"], "revision": state.revision, "status": "PENDING"})
            if responding:
                state.response_model = data["model"]
                state.pending[request["idempotency_key"]].update(operation='RESPOND_PRIVATE' if private else "RESPOND", reply_to=request["reply_to"])
                if context.get('response_task') is not None:
                    state.pending[request['idempotency_key']]['response_task'] = context['response_task']
                receipts = state.private_response_requests if private else state.response_requests
                receipts.append({"request_id": request["idempotency_key"],
                    "character_id": request["character_id"], "reply_to": request["reply_to"],
                    "revision": state.revision, "status": "PENDING"})
            if calling:
                state.phone_model=data['model']
                state.pending[request['idempotency_key']].update(operation='PHONE',stage=context['stage'])
                state.phone_requests.append({'request_id':request['idempotency_key'],'revision':state.revision,
                    'character_id':context['character']['id'],'stage':context['stage'],'reply_to':context['reply_to'],'status':'PENDING'})
            elif deciding:
                state.table_model = data['model']
                state.pending[request['idempotency_key']].update(operation='DECIDE', action=request['action'])
                state.table_requests.append({'request_id': request['idempotency_key'], 'character_id': request['character_id'],
                    'action': request['action'], 'revision': state.revision, 'status': 'PENDING'})
            state.questions += 1
            return
        if kind != "AI_RESULT" or set(request) != {"idempotency_key", "request_id"} or request["idempotency_key"] != request["request_id"]:
            raise ValueError
        pending = state.pending.get(request["request_id"])
        proposing = bool(pending and pending.get("operation") == "PROPOSE")
        private = bool(pending and pending.get('operation') == 'RESPOND_PRIVATE')
        responding = private or bool(pending and pending.get("operation") == "RESPOND")
        calling = bool(pending and pending.get('operation') == 'PHONE')
        deciding = calling or bool(pending and pending.get('operation') == 'DECIDE')
        if pending is None or set(data) != {"status", "refs", "usage", "accounted", "received_at"} | ({'decision'} if deciding else {"proposal"} if proposing else {"speech"} if responding else set()):
            raise ValueError
        status = data["status"]
        if (status not in {"OK", "INVALID", "UNKNOWN", "STALE", "EXPIRED"}
                or type(data["received_at"]) is not int or data["received_at"] < pending["issued_at"]):
            raise ValueError
        if status in {"UNKNOWN", "EXPIRED"} and data["usage"] is not None:
            raise ValueError
        if status == "EXPIRED" and data["received_at"] < pending["expires_at"]:
            raise ValueError
        accounted = self._account(state.policy, pending["reservation"], data["usage"])
        if data["accounted"] != accounted.to_metadata():
            raise ValueError
        if status == "OK":
            if (state.revision != pending["revision"] or data["received_at"] > pending["expires_at"]
                    or data["received_at"] < pending["issued_at"] or data["usage"] is None):
                raise ValueError
            if calling:
                context=self._phone_context(state,binding,pending['revision']-1,pending=pending)
                if content_hash(context)!=pending['context_hash'] or data['refs']!=[]:raise ValueError
                decision=validate_call(data['decision'],context,pending['model']['schema_version'])
                state.engine.apply_phone(pending['character_id'],decision,state.revision+1)
                if decision['kind']=='SPEAK':
                    memory_ids=sorted({ref['id'] for segment in decision['speech']['segments'] for ref in segment['basis'] if ref['collection']=='memory'})
                    state.retelling_attempts.extend({'character_id':pending['character_id'],'memory_id':identifier,
                        'sequence':state.revision+1,'status':'ATTEMPTED_UNVERIFIED'} for identifier in memory_ids)
            elif deciding:
                context = self._table_context(state, binding, pending['character_id'], pending['action'], pending['revision'] - 1)
                if content_hash(context) != pending['context_hash'] or data['refs'] != []:
                    raise ValueError
                decision = validate_table_decision(data['decision'], context)
                state.engine.table_apply(pending['action'], pending['character_id'], decision, state.revision + 1)
            elif responding:
                context = self._dialogue_context(state, binding, pending["character_id"], pending["reply_to"], pending["revision"] - 1, private=private)
                if content_hash(context) != pending["context_hash"] or data["refs"] != []:
                    raise ValueError
                rendered = render_speech(data['speech'], context, state.revision + 1, pending['model']['schema_version'])
                validate_topic_answer(data['speech'], next((t for t in state.topic_turns if t['reply_to'] == pending['reply_to']),None))
                if private:
                    state.engine.table_apply('PRIVATE_SPEAK', pending['character_id'], {'text': rendered['text']}, state.revision + 1)
                else:
                    state.responses.append(rendered)
                memory_ids = sorted({ref["id"] for segment in data["speech"]["segments"] for ref in segment["basis"]
                                     if ref["collection"] == "memory"})
                state.retelling_attempts.extend({"character_id": pending["character_id"], "memory_id": identifier,
                    "sequence": state.revision + 1, "status": "ATTEMPTED_UNVERIFIED"} for identifier in memory_ids)
            elif proposing:
                context = self._proposal_context(state, binding, pending["character_id"], pending["revision"] - 1)
                if content_hash(context) != pending["context_hash"] or data["refs"] != []:
                    raise ValueError
                state.proposals.append(render_proposal(data["proposal"], context, state.revision + 1, pending["model"]["schema_version"]))
            else:
                state.engine.apply_reply(pending["character_id"], data["refs"])
                reply = state.engine.render_reply(pending["character_id"], data["refs"])
                state.dialogue.append({**reply, "refs": data["refs"]})
        elif data["refs"] != []:
            raise ValueError
        if proposing and status != "OK" and data["proposal"] is not None:
            raise ValueError
        if responding and status != "OK" and data["speech"] is not None:
            raise ValueError
        if deciding and status != 'OK' and data['decision'] is not None:
            raise ValueError
        if deciding:
            receipt = next(item for item in (state.phone_requests if calling else state.table_requests) if item['request_id'] == request['request_id'])
            receipt['status'] = status
        if proposing:
            receipt = next(item for item in state.proposal_requests if item["request_id"] == request["request_id"])
            receipt["status"] = status
        if responding:
            receipts = state.private_response_requests if private else state.response_requests
            receipt = next(item for item in receipts if item["request_id"] == request["request_id"])
            receipt["status"] = status
            if status == 'OK':
                topic = next((t for t in state.topic_turns if topic_response_request(t)['idempotency_key'] == request['request_id']),None)
                if topic is not None:
                    topic['answer_id'] = f'private-{state.revision + 1}' if private else rendered['id']
            task = pending.get('response_task')
            if task is not None:
                state.retelling_attempts.append({'attempt_kind': 'ASSIGNED_PUBLIC_TASK',
                    'character_id': pending['character_id'], 'memory_id': task['target']['id'],
                    'channel': 'PUBLIC', 'request_sequence': pending['revision'],
                    'response_id': rendered['id'] if status == 'OK' else None,
                    'text': rendered['text'] if status == 'OK' else None, 'result_status': status,
                    'status': 'ATTEMPTED_UNVERIFIED' if status == 'OK' else 'FAILED_UNVERIFIED'})
        self._observe_memories(state, (state.responses[-1] if responding else state.proposals[-1])
                               if (responding or proposing) and not private and status == "OK" else None)
        state.used += accounted
        del state.pending[request["request_id"]]
        state.last_status = status

    def _replay(self, row, package: dict, binding: dict) -> Replay:
        try:
            state = Replay(play_engine(package, row.selected_character_id, binding["rules_contract"]),
                           0, row.binding_hash, self._budget(binding["budget"]))
            if content_hash(state.state()) != binding["initial_state_hash"]:
                raise ValueError
            capture_workspace(state)
            limit = len(package["phases"]) + len(package["knowledge"]) + len(package["evidence"]) + MAX_QUESTIONS * 2 + MAX_STATEMENTS
            if package["schema_version"] in ("script-package/1.2", "script-package/1.3", 'script-package/1.4'):
                limit += len(package["mechanics"]["actions"])
            if isinstance(state.engine, PackageFullPlayRules):
                limit = 2000
            events = self.db.query(ScriptPackagePlayEvent).filter_by(play_id=row.play_id).order_by(ScriptPackagePlayEvent.revision).populate_existing().yield_per(100)
            for event in events:
                if (type(event.revision) is not int or event.revision != state.revision + 1 or event.revision > limit
                        or event.previous_event_hash != state.previous):
                    raise ValueError
                request = self._json(event.request_json, event.request_hash)
                payload = self._json(event.event_json, event.event_hash)
                if event.idempotency_key != self._key(event.kind, request):
                    raise ValueError
                self._consume(state, event.kind, request, payload["data"], binding)
                expected = {"schema_version": self._event_contract(state), "play_id": row.play_id, "revision": event.revision,
                            "kind": event.kind, "request_hash": event.request_hash,
                            "previous_event_hash": state.previous, "state_hash": content_hash(state.state()), "data": payload["data"]}
                if payload != expected or event.state_hash != expected["state_hash"]:
                    raise ValueError
                state.revision, state.previous = event.revision, event.event_hash
                capture_workspace(state)
            return state
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            raise PackagePlayError("PACKAGE_PLAY_HISTORY_INVALID") from None

    @staticmethod
    def _event_contract(state: Replay) -> str:
        if state.phone_model is not None:
            return 'package-text-play-event/1.6'
        if isinstance(state.engine, PackageFullPlayRules):
            return 'package-text-play-event/1.5'
        if state.response_model is not None:
            return "package-text-play-event/1.4"
        if isinstance(state.engine, PackageMemoryRules):
            return "package-text-play-event/1.3"
        if state.proposal_model is not None:
            return "package-text-play-event/1.2"
        return DISCUSSION_EVENT_CONTRACT if state.discussion else EVENT_CONTRACT

    @staticmethod
    def _observe_memories(state: Replay, speech: dict | None = None):
        if isinstance(state.engine, PackageMemoryRules):
            state.engine.observe_event(state.revision + 1,
                {key: speech[key] for key in ("speaker", "text", "phase_id")} if speech else None)

    @staticmethod
    def _proposal_context(state: Replay, binding: dict, character: str, revision: int | None = None) -> dict:
        if not isinstance(state.engine, PackageInvestigationRules):
            raise PlayRulesError("PACKAGE_PLAY_PROPOSAL_UNSUPPORTED")
        context = state.engine.proposal_context(character)
        if not context["options"]:
            raise PlayRulesError("PACKAGE_PLAY_PROPOSAL_NO_OPTIONS")
        claims = [{key: entry[key] for key in ("id", "sequence", "speaker", "text", "kind")}
                  for entry in [*state.discussion, *state.proposals, *state.responses]]
        value = {"schema_version": "package-investigation-context/1.0", "play_id": binding["play_id"],
                "package_hash": binding["package_hash"], "revision": state.revision if revision is None else revision,
                **context, "discussion": sorted(claims, key=lambda entry: entry["sequence"])}
        if isinstance(state.engine, PackageFullPlayRules):
            value['schema_version'] = 'package-investigation-context/1.1'
            # This public suggestion only cites public claims; the separate
            # formal decision context also includes actually heard phone claims.
            return proposal_context_window(value, PackagePlayService._full_base(binding)['max_input_bytes'])
        return value

    @staticmethod
    def _dialogue_context(state: Replay, binding: dict, character: str, reply_to: str, revision: int | None = None, private=False, version=None) -> dict:
        if not isinstance(state.engine, PackageMemoryRules):
            raise PlayRulesError("PACKAGE_PLAY_DIALOGUE_UNSUPPORTED")
        phase = state.engine.state()["current_phase_id"]
        if private and not isinstance(state.engine, PackageFullPlayRules):
            raise PlayRulesError('FULL_PLAY_UNSUPPORTED')
        if not private and not any(item["id"] == reply_to and item["phase_id"] == phase for item in state.discussion):
            raise PlayRulesError("PACKAGE_PLAY_REPLY_TARGET_INVALID")
        context = state.engine.private_reply_context(character, reply_to) if private else state.engine.dialogue_context(character)
        topic = next((t for t in state.topic_turns if t['reply_to'] == reply_to and t['character_id'] == character
                      and t['channel'] == ('PRIVATE' if private else 'PUBLIC')), None)
        claims = [{key: entry[key] for key in ("id", "sequence", "speaker", "text", "kind")}
                  for entry in [*state.discussion, *state.proposals, *state.responses]]
        value = {"schema_version": "package-dialogue-context/1.0", "play_id": binding["play_id"],
                "package_hash": binding["package_hash"], "revision": state.revision if revision is None else revision,
                **context, "reply_to": reply_to, "discussion": sorted(claims, key=lambda entry: entry["sequence"])}
        if isinstance(state.engine, PackageFullPlayRules):
            version = version or (state.response_model or {}).get('schema_version', 'package-dialogue-model/1.3')
            value['schema_version'] = 'package-dialogue-context/1.1'
            if version in ('package-dialogue-model/1.4', 'package-dialogue-model/1.5', 'package-dialogue-model/1.6', 'package-dialogue-model/1.7', 'package-dialogue-model/1.8', 'package-dialogue-model/1.9', 'package-dialogue-model/1.10', 'package-dialogue-model/1.11', 'package-dialogue-model/1.12', 'package-dialogue-model/1.13'):
                value['schema_version'] = 'package-dialogue-context/1.2'
                value['strategy_materials'] = state.engine.speech_strategy(character)
            value['channel'] = 'PRIVATE' if private else 'PUBLIC'
            value['discussion'].extend({k: e[k] for k in ('id', 'sequence', 'speaker', 'text', 'kind')}
                                        for e in state.engine.personal_discussion(character))
            value['discussion'].sort(key=lambda e: e['sequence'])
            if version in ('package-dialogue-model/1.11', 'package-dialogue-model/1.12', 'package-dialogue-model/1.13'):
                value['schema_version'] = 'package-dialogue-context/1.3'
                completed = {e['material_id'] for e in state.scripted_retells
                             if e['owner'] == character and e['collection'] == 'memory'}
                assignment = {**value, 'materials': [m for m in value['materials']
                    if m['collection'] != 'memory' or m['id'] not in completed]}
                task = None if topic is not None or refuses_meta_request(value) else required_retelling_task(assignment, state.retelling_attempts)
                if task is not None:
                    value['response_task'] = task
            if version == 'package-dialogue-model/1.13':
                value = scope_retelling_context(value)
            topic = next((t for t in state.topic_turns if t['reply_to'] == reply_to and t['character_id'] == character
                          and t['channel'] == ('PRIVATE' if private else 'PUBLIC')), None)
            if topic is not None:
                # Frozen event policy, not today's editable content catalogue.
                # Only this topic's authorized material and own thread reach AI.
                allowed = {(r['collection'], r['id']) for r in topic['basis']}
                value['materials'] = [m for m in context['materials'] if (m['collection'],m['id']) in allowed]
                value.pop('response_task', None)
                if 'strategy_materials' in value:
                    value['strategy_materials'] = state.engine.speech_strategy(character)
                    value['strategy_materials'].append({'collection':'knowledge','id':'single-player-disclosure',
                        'kind':'CLAIM', 'text':'本次交流只回应当前议题，保留以下来源审核后的披露边界；这不是新剧情事实：'
                        + canonical_json(topic['disclosure'])})
                    if topic.get('answer_contract'):
                        value['strategy_materials'].append({'collection':'knowledge','id':'single-player-answer-check',
                            'kind':'CLAIM','text':'本次有源表达约束（不是剧情事实）：'+canonical_json(topic['answer_contract'])
                            +'。required_terms的每组至少说出一项，保留听说/似乎等限定；公开资料与reported_passages不能写成你的亲历。'
                            +'不同归属请分段引用，不把别人的见闻混入我的经历；不要使用肯定、一定、绝对等未经核实的断言。'})
                        if topic['answer_contract'].get('public_document_basis'):
                            value['strategy_materials'][-1]['text'] += (
                                '其中public_document_basis是已取得的文献，可明确说报纸或头条写了什么；'
                                '阅读文献不等于你亲眼见过其中报道的事件，须保留文献归属。')
                        if topic['answer_contract'].get('schema_version') in (BASIS_VERSION, DISCLOSURE_VERSION):
                            # Historical context hashes depend on the exact 1.0
                            # text above; new guidance belongs only to 1.1 turns.
                            value['strategy_materials'][-1]['text'] += BASIS_GUIDANCE
                        if topic['answer_contract'].get('schema_version') == DISCLOSURE_VERSION:
                            value['strategy_materials'][-1]['text'] += DISCLOSURE_GUIDANCE
                related = {t['reply_to'] for t in state.topic_turns if t['topic_id'] == topic['topic_id']
                           and t['character_id'] == character and t['channel'] == topic['channel']}
                related |= {t['answer_id'] for t in state.topic_turns if t['topic_id'] == topic['topic_id']
                            and t['character_id'] == character and t['channel'] == topic['channel'] and 'answer_id' in t}
                value['discussion'] = [e for e in value['discussion'] if e['id'] in related]
            return dialogue_context_window(value, PackagePlayService._full_base(binding)['max_input_bytes'], version)
        return value

    def _dialogue_reason(self, binding: dict, state: Replay) -> str | None:
        reason = self._model_reason(binding)
        if reason:
            return reason
        full = isinstance(state.engine, PackageFullPlayRules)
        model = self.full_dialogue_model if full else self.dialogue_model
        expected = dialogue_metadata(self._full_base(binding), model.model_contract) if full else dialogue_metadata(binding['model'])
        if (model.metadata() != expected
                or (state.response_model is not None and model.metadata() != state.response_model)):
            return "CONFIG_CHANGED"
        return None if model.available else "MODEL_UNAVAILABLE"

    @staticmethod
    def _full_base(binding):
        return {**binding['model'], 'max_input_bytes': binding.get('full_input', binding['model'])['max_input_bytes']}

    @staticmethod
    def _full_table_base(binding):
        base = PackagePlayService._full_base(binding)
        if 'full_output' in binding:
            limit = binding['full_output']['max_output_tokens']
            old_limit, reserved = base['max_output_tokens'], base['reserved_output_tokens']
            base.update(max_output_tokens=limit,
                reserved_output_tokens=reserved - old_limit + limit if type(old_limit) is int and type(reserved) is int else None)
        return base

    @staticmethod
    def _question_limit(state):
        return 400 if isinstance(state.engine, PackageFullPlayRules) else MAX_QUESTIONS

    @staticmethod
    def _statement_limit(state):
        return 600 if isinstance(state.engine, PackageFullPlayRules) else MAX_STATEMENTS

    @staticmethod
    def _table_context(state, binding, character, action, revision=None, version=None):
        if not isinstance(state.engine, PackageFullPlayRules):
            raise PlayRulesError('FULL_PLAY_UNSUPPORTED')
        context = state.engine.decision_context(character, action)
        claims = [{k: c[k] for k in ('id', 'sequence', 'speaker', 'text', 'kind')} for c in
                  [*state.discussion, *state.proposals, *state.responses, *state.engine.personal_discussion(character)]]
        context = {'schema_version': 'package-table-context/1.0', 'play_id': binding['play_id'],
                'package_hash': binding['package_hash'], 'revision': state.revision if revision is None else revision,
                **context, 'discussion': sorted(claims, key=lambda c: c['sequence'])}
        version = version or (state.table_model or {}).get('schema_version', 'package-table-model/1.0')
        return table_context_window(context, PackagePlayService._full_base(binding)['max_input_bytes'], version)

    def _table_reason(self, binding, state):
        reason = self._model_reason(binding)
        if reason:
            return reason
        if (self.table_model.metadata() != table_metadata(self._full_table_base(binding), self.table_model.model_contract)
                or (state.table_model is not None and self.table_model.metadata() != state.table_model)):
            return 'CONFIG_CHANGED'
        return None if self.table_model.available else 'MODEL_UNAVAILABLE'

    @staticmethod
    def _phone_context(state,binding,revision=None,pending=None,version=None):
        if not isinstance(state.engine,PackageFullPlayRules):raise PlayRulesError('FULL_PLAY_UNSUPPORTED')
        idle_turn=sum(r['stage']=='IDLE' for r in state.phone_requests)-(bool(pending) and pending['stage']=='IDLE')
        context=state.engine.phone_context(idle_turn)
        private=context.pop('private_claims')
        claims=[{k:c[k] for k in ('id','sequence','speaker','text','kind')}
                for c in [*state.discussion,*state.proposals,*state.responses,*private]]
        value={'schema_version':'package-call-context/1.0','play_id':binding['play_id'],
               'package_hash':binding['package_hash'],'revision':state.revision if revision is None else revision,
               **context,'discussion':sorted(claims,key=lambda c:c['sequence'])}
        version = version or (state.phone_model or {}).get('schema_version','package-call-model/1.0')
        if version in ('package-call-model/1.1', 'package-call-model/1.2', 'package-call-model/1.3', 'package-call-model/1.4', 'package-call-model/1.5', 'package-call-model/1.6', 'package-call-model/1.7', 'package-call-model/1.8'):
            value['schema_version'] = 'package-call-context/1.1'
            value['strategy_materials'] = (state.engine.speech_strategy(context['character']['id'])
                                           if context['stage'] == 'CONNECTED' else [])
        return call_window(value,PackagePlayService._full_base(binding)['max_input_bytes'],version)

    def _phone_reason(self,binding,state):
        reason=self._model_reason(binding)
        if reason:return reason
        if (self.phone_model.metadata()!=call_metadata(self._full_base(binding),self.phone_model.model_contract)
                or (state.phone_model is not None and state.phone_model!=self.phone_model.metadata())):
            return 'CONFIG_CHANGED'
        return None if self.phone_model.available else 'MODEL_UNAVAILABLE'

    def _proposal_adapter(self, binding, full):
        if not full:
            return self.proposal_model
        return self.guided_proposal_model if self._guided_catalog(binding) else self.full_proposal_model

    def _proposal_reason(self, binding: dict, state: Replay) -> str | None:
        reason = self._model_reason(binding)
        if reason:
            return reason
        full = isinstance(state.engine, PackageFullPlayRules)
        model = self._proposal_adapter(binding, full)
        expected = full_proposal_metadata(self._full_base(binding), model.metadata()['schema_version']) if full else proposal_metadata(binding['model'])
        if (model.metadata() != expected or not (proposal_model_transition(state.proposal_model, expected, self._full_base(binding))
                if full else state.proposal_model is None or state.proposal_model == expected)):
            return "CONFIG_CHANGED"
        return None if model.available else "MODEL_UNAVAILABLE"

    def _append(self, row, binding: dict, state: Replay, kind: str, request: dict, data: dict) -> None:
        if isinstance(state.engine, PackageFullPlayRules):
            # Every dispatched reservation owns a future result slot. Ordinary
            # actions may not consume it while network I/O is in progress.
            needed = 1 + len(state.pending) + (kind == 'AI_REQUEST') - (kind == 'AI_RESULT' and bool(state.pending))
            # Preserve a free transport pause while an AI-only call is open,
            # including the result that may establish the line.
            pausing=request.get('schema_version')=='package-phone-pause-command/1.0'
            full=state.engine.view()['full_game']
            opening_result=(kind=='AI_RESULT' and data.get('status')=='OK'
                and state.pending.get(request.get('request_id'),{}).get('operation')=='PHONE'
                and (data.get('decision') or {}).get('kind')=='INVITE'
                and (data.get('decision') or {}).get('peer_character_id')!=binding.get('selected_character_id'))
            closing_result=(kind=='AI_RESULT' and data.get('status')=='OK'
                and state.pending.get(request.get('request_id'),{}).get('operation')=='PHONE'
                and (data.get('decision') or {}).get('kind')=='END')
            if not pausing and ((full['phone_busy'] and not full['call'] and not closing_result)
                    or request.get('schema_version')=='package-phone-command/1.0' or opening_result):
                needed+=1
            if state.revision + needed > 2000:
                raise PackagePlayError('FULL_PLAY_EVENT_LIMIT')
        self._consume(state, kind, request, data, binding)
        payload = {"schema_version": self._event_contract(state), "play_id": row.play_id, "revision": state.revision + 1,
                   "kind": kind, "request_hash": content_hash(request), "previous_event_hash": state.previous,
                   "state_hash": content_hash(state.state()), "data": data}
        event = ScriptPackagePlayEvent(play_id=row.play_id, revision=state.revision + 1, kind=kind,
            idempotency_key=self._key(kind, request), request_json=canonical_json(request), request_hash=payload["request_hash"],
            previous_event_hash=state.previous, state_hash=payload["state_hash"], event_json=canonical_json(payload), event_hash=content_hash(payload))
        with self.db.begin_nested():
            self.db.add(event)
            self.db.flush()
        state.revision, state.previous = event.revision, event.event_hash
        capture_workspace(state)

    def _model_reason(self, binding: dict) -> str | None:
        if self.model.metadata() != binding["model"] or self.policy.to_snapshot() != binding["budget"]:
            return "CONFIG_CHANGED"
        if not self.model.available:
            return self.model.unavailable_reason or "MODEL_UNAVAILABLE"
        if not self.policy.paid_calls_enabled:
            return "PAID_CALLS_DISABLED"
        return self.policy.decide(UsageAmount(), UsageAmount(), UsageAmount()).reason

    def _view(self, row, package: dict, binding: dict, state: Replay) -> dict:
        projection = state.engine.view()
        reserved = state.reserved()
        reason = self._model_reason(binding)
        if projection["settled"]:
            reason = "SETTLED"
        elif isinstance(state.engine, PackageFullPlayRules) and projection['full_game']['phase_kind'] == 'FINALE':
            reason = 'FINALE_SEALING'
        elif state.questions >= self._question_limit(state):
            reason = "QUESTION_LIMIT"
        proposal_reason = reason or self._proposal_reason(binding, state)
        proposal_roles = []
        if isinstance(state.engine, PackageInvestigationRules) and not projection["settled"]:
            proposal_roles = [item["id"] for item in package["characters"] if item["id"] != row.selected_character_id
                              and state.engine.proposal_context(item["id"])["options"]]
        if not proposal_roles:
            proposal_reason = proposal_reason or "NO_OPTIONS"
        response_view = {}
        if isinstance(state.engine, PackageFullPlayRules):
            response_view['table_commands'] = state.table_commands
            phone_reason=self._phone_reason(binding,state)
            phone_available=False
            if not projection['settled'] and state.questions<self._question_limit(state):
                try:
                    self._phone_context(state,binding,version=self.phone_model.model_contract)
                    phone_available=phone_reason is None
                except (PlayRulesError,PackageRoleModelError):pass
            response_view['phone_turns']={'schema_version':'package-phone-view/1.0','available':phone_available,
                'can_pause':projection['full_game']['phone_busy'] and projection['full_game']['call'] is None,
                'requests':[{k:r[k] for k in ('request_id','revision','status')} for r in state.phone_requests]}
            table_reason = self._table_reason(binding, state)
            if projection['settled']:
                table_reason = 'SETTLED'
            elif state.questions >= self._question_limit(state):
                table_reason = 'QUESTION_LIMIT'
            choices = []
            for c in package['characters']:
                if c['id'] == row.selected_character_id:
                    continue
                for action in ('CAST_BALLOT', 'BREAK_TIE', 'SEAL_FINALE'):
                    try:
                        self._table_context(state, binding, c['id'], action, version=self.table_model.model_contract)
                    except PlayRulesError:
                        continue
                    except PackageRoleModelError as exc:
                        table_reason = table_reason or exc.code
                        continue
                    choices.append({'character_id': c['id'], 'action': action})
            response_view['table_decisions'] = {'schema_version': 'package-table-decision-view/1.0',
                'available': table_reason is None, 'reason': table_reason, 'options': choices,
                'requests': state.table_requests}
            call = projection['full_game']['call']
            reply_options = []
            if call and not projection['settled']:
                for actor in call['character_ids']:
                    if actor == row.selected_character_id:
                        continue
                    for message in projection['full_game']['private_discussion']:
                        if message['call_id'] == call['id'] and message['speaker'] != actor:
                            reply_options.append({'character_id': actor, 'reply_to': message['id']})
            response_view['private_replies'] = {'schema_version': 'package-private-dialogue-view/1.0',
                'available': not projection['settled'] and state.questions < self._question_limit(state)
                    and self._dialogue_reason(binding, state) is None,
                'options': reply_options, 'requests': state.private_response_requests}
        if isinstance(state.engine, PackageMemoryRules):
            response_reason = reason or self._dialogue_reason(binding, state)
            target_ids = [entry["id"] for entry in state.discussion if entry["phase_id"] == projection["current_phase"]["id"]]
            if not target_ids:
                response_reason = response_reason or "NO_STATEMENT"
            response_view["role_responses"] = {"schema_version": "package-dialogue-view/1.0",
                "available": response_reason is None, "reason": response_reason,
                "character_ids": [c["id"] for c in package["characters"] if c["id"] != row.selected_character_id],
                "reply_target_ids": target_ids, "entries": state.responses, "requests": state.response_requests}
        if self.include_interactions:
            # A read-only HTTP projection: all AI_REQUEST kinds share this cap.
            # Keep it outside Replay.state(), bindings and frozen legacy views.
            response_view['ai_interactions'] = {'schema_version': 'package-ai-interactions/1.0',
                'initiated': state.questions, 'limit': self._question_limit(state)}
        result = {"play_id": row.play_id, "opening_session_id": row.opening_session_id,
                "release_id": row.release_id, "version_id": row.version_id, "package_hash": row.package_hash,
                "selected_character_id": row.selected_character_id, "revision": state.revision,
                "runtime_ready": False, "status": "SETTLED" if projection["settled"] else "TEXT_PLAY",
                "script": {key: package[key] for key in ("title", "content_version", "player_count")},
                "characters": [{"id": item["id"], "name": item["name"]} for item in package["characters"]],
                "introduction": {"text": package["introduction"]["text"]}, **projection,
                "dialogue": [{key: value for key, value in item.items() if key != "refs"} for item in state.dialogue],
                "discussion": {"schema_version": "package-discussion-view/1.0", "limit": self._statement_limit(state),
                               "entries": state.discussion},
                "investigation_proposals": {"schema_version": "package-proposal-view/1.0", "entries": state.proposals,
                                           "available": proposal_reason is None, "reason": proposal_reason,
                                           "character_ids": proposal_roles, "requests": state.proposal_requests},
                "model": {"available": reason is None, "reason": reason},
                "budget": {"token_limit": state.policy.token_limit, "used_tokens": state.used.total_tokens,
                           "reserved_tokens": reserved.total_tokens, "cost_limit_cny": binding["budget"]["cost_limit_cny"],
                           "used_cost_cny": str(state.used.cost_cny), "reserved_cost_cny": str(reserved.cost_cny)},
                "pending_ai": any(item["expires_at"] > self.now() for item in state.pending.values()),
                "last_ai_status": state.last_status, **response_view}
        catalogue = self._guided_catalog(binding)
        if catalogue is not None and isinstance(state.engine, PackageFullPlayRules):
            full = projection['full_game']
            if (projection['settled'] and full['result'] is not None
                    and full['phase_kind'] == 'FINALE' and catalogue.package_hash == row.package_hash):
                result['post_game_qa'] = {'schema_version': 'package-post-game-qa/1.0',
                    'questions': catalogue.post_game_questions(projection['current_phase']['id'])}
            ready = not state.pending and not projection['settled']
            investigating = ready and full['phase_kind'] == 'INVESTIGATION' and not full['phone_busy']
            already = {(e['collection'], e['material_id'], e['owner']) for e in state.scripted_retells}
            result['guided_play'] = {'schema_version': GUIDED_POLICY, 'available': ready,
                'can_investigate_round': bool(investigating and projection['mechanics']['available_actions']
                    and projection['current_phase']['id'] not in state.engine._closed_investigations),
                'can_investigate': bool(investigating and projection['mechanics']['available_actions']
                    and projection['current_phase']['id'] not in state.engine._closed_investigations),
                'can_finish_investigation': bool(investigating), 'has_legacy_ballot': full['ballot'] is not None,
                'can_present_required': bool(ready and len(state.discussion) < self._statement_limit(state)
                    and catalogue.pending(state.engine, already)),
                'last_command': ({'idempotency_key': state.guided_actions[-1]['request_id'],
                                  'action': state.guided_actions[-1]['action'],
                                  'sequence': state.guided_actions[-1]['sequence']} if state.guided_actions else None)}
            if 'table_decisions' in result:
                result['table_decisions']['options'] = [c for c in result['table_decisions']['options'] if c['action'] == 'SEAL_FINALE']
        if catalogue is None and (state.guided_actions or state.scripted_retells or state.host_hint_entries):
            result['guided_play'] = {'schema_version': GUIDED_POLICY, 'available': False,
                'can_investigate': False, 'can_finish_investigation': False, 'has_legacy_ballot': False,
                'can_present_required': False,
                'last_command': ({'idempotency_key': state.guided_actions[-1]['request_id'],
                    'action': state.guided_actions[-1]['action'], 'sequence': state.guided_actions[-1]['sequence']}
                    if state.guided_actions else None)}
        if catalogue is not None or state.host_hint_entries:
            result['host_hints'] = {'schema_version': 'package-host-hints/1.0',
                'topics': (catalogue.topics(projection['current_phase']['id']) if catalogue and not projection['settled']
                    and not (projection.get('full_game', {}).get('finale') or {}).get('sealed') else []),
                'entries': deepcopy(state.host_hint_entries)}
        result = self.presentation_repair.apply(result) if self.presentation_repair else result
        single = self._single_catalog(binding)
        if self._single_enabled(binding,state):
            phase = projection['current_phase']['id']
            ready = single is not None and not state.pending and not projection['settled']
            turns = []
            for turn in state.topic_turns:
                status = topic_status(turn, state)
                current = turn['phase_id'] == phase and not projection['settled']
                reply = topic_response_request(turn)
                # A never-sent question can only start at its frozen revision.
                can_start = single is not None and current and state.revision == turn['sequence'] and not state.pending
                can_fallback = current and not state.pending and status in ('READY','FAILED')
                if status == 'READY' and can_start and single is not None:
                    try:
                        context = self._dialogue_context(state,binding,turn['character_id'],turn['reply_to'],
                            private=turn['channel']=='PRIVATE',version=self.full_dialogue_model.model_contract)
                        prepared = self.full_dialogue_model.prepare(context)
                        reservation = state.policy.amount(prepared['input_tokens'],prepared['output_tokens'])
                        unavailable = (self._dialogue_reason(binding,state) is not None or state.questions >= self._question_limit(state)
                                       or not state.policy.decide(state.used,state.reserved(),reservation).allowed)
                        can_fallback = bool(unavailable)
                    except (PlayRulesError, PackageRoleModelError):
                        can_fallback = True
                if turn['channel'] == 'PRIVATE':
                    try:
                        state.engine.private_reply_context(turn['character_id'],turn['reply_to'])
                    except PlayRulesError:
                        can_start = can_fallback = False
                elif projection['full_game']['phone_busy']:
                    can_start = can_fallback = False
                public = {k: deepcopy(turn[k]) for k in ('id','topic_id','title','phase_id','character_id','channel','intent_id','question','reply_to')}
                public.update(status=status, reply_request=reply if status in ('PENDING','FAILED','OK') or can_start else None,
                              can_fallback=can_fallback)
                receipt = next((r for r in state.response_requests + state.private_response_requests
                                if r['request_id'] == reply['idempotency_key']),None)
                if receipt:
                    public['receipt_status'] = receipt['status']
                if 'answer' in turn:
                    public['answer'] = turn['answer']
                turns.append(public)
            result['single_player'] = {'schema_version':SINGLE_POLICY, 'available':ready,
                'stage':deepcopy(single.stages[phase]) if single else None, 'operation_rules':single.rules if single else '',
                'topics':single.options(state.engine,[{**t,'status':topic_status(t,state)} for t in state.topic_turns]) if single and not projection['settled'] else [],
                'turns':turns,'last_command':deepcopy(state.topic_commands[-1]) if state.topic_commands else None}
            if single:
                result = single.replace_rules(result)
        if isinstance(state.engine, PackageFullPlayRules) and (catalogue is not None
                or state.guided_actions or self._single_enabled(binding, state)):
            capture_workspace(state)
            result['round_workspace'] = project_workspace(state, result)
        return result

    def _single_catalog(self, binding):
        return resolve_role_content(self.single_player_content, binding['package_hash'], binding['selected_character_id'])

    def _single_enabled(self, binding, state):
        return bool(self._single_catalog(binding) is not None or state.topic_commands
                    or role_content_required(self.single_player_required, binding['package_hash'], binding['selected_character_id']))

    def topic(self, identifier, body, owner):
        request = self._request(body, TopicCommand)
        row = self._row(identifier, owner)
        package, binding = self._resolve(row)
        if self._repeat(row, 'ACTION', request):
            return self.get(identifier, owner)
        self._current(row)
        try:
            self._lock(ScriptPackagePlay,'play_id',identifier)
            row = self._row(identifier,owner)
            package,binding = self._resolve(row)
            if self._repeat(row,'ACTION',request):
                return self.get(identifier,owner)
            state = self._replay(row,package,binding)
            if request['expected_revision'] != state.revision:
                raise PackagePlayError('PACKAGE_PLAY_REVISION_CONFLICT')
            if state.pending:
                raise PackagePlayError('PACKAGE_PLAY_AI_BUSY')
            catalog = self._single_catalog(binding)
            if request['action'] == 'ASK_TOPIC':
                if catalog is None:
                    raise PackagePlayError('SINGLE_TOPIC_NOT_AVAILABLE')
                data = catalog.select(state.engine,[{**t,'status':topic_status(t,state)} for t in state.topic_turns],request['payload'])
            else:
                view = self._view(row,package,binding,state)
                eligible = next((t for t in view.get('single_player',{}).get('turns',[])
                                 if t['id'] == request['payload']['turn_id'] and t['can_fallback']),None)
                if eligible is None:
                    raise PackagePlayError('SINGLE_FALLBACK_NOT_AVAILABLE')
                turn = next(t for t in state.topic_turns if t['id'] == eligible['id'])
                data = {'text':turn['fallback']}
            with self.db.begin_nested():
                self._append(row,binding,state,'ACTION',request,data)
                if request['action'] == 'USE_FALLBACK':
                    self._present_required(row,binding,state)
            return self._view(row,package,binding,state)
        except PlayRulesError as exc:
            raise PackagePlayError(exc.code) from None
        except (OperationalError,IntegrityError):
            raise PackagePlayError('PACKAGE_PLAY_WRITE_CONFLICT') from None

    def _guided_catalog(self, binding):
        return resolve_role_content(self.guided_content, binding['package_hash'], binding['selected_character_id'])

    def _present_required(self, row, binding, state):
        catalogue = self._guided_catalog(binding)
        if catalogue is None or state.pending or not isinstance(state.engine, PackageFullPlayRules):
            return
        while True:
            if len(state.discussion) >= self._statement_limit(state):
                return
            already = {(e['collection'], e['material_id'], e['owner']) for e in state.scripted_retells}
            entries = catalogue.pending(state.engine, already)
            if not entries:
                return
            entry = entries[0]
            request = {'schema_version': 'package-scripted-retelling-command/1.0', 'expected_revision': state.revision,
                'idempotency_key': content_hash({'catalog': catalogue.revision, 'entry': entry['id'], 'play': row.play_id}),
                'entry_id': entry['id']}
            data = {'policy': GUIDED_POLICY, 'catalog_hash': catalogue.revision, 'collection': entry['collection'],
                    'material_id': entry['material_id'], 'owner': entry['owner_character_id'],
                    'text_sha256': entry['text_sha256'], 'speech': entry['speech']}
            self._append(row, binding, state, 'ACTION', request, data)

    def guided(self, identifier: str, body, owner: int) -> dict:
        request = self._request(body, GuidedPlayRequest)
        row = self._row(identifier, owner)
        package, binding = self._resolve(row)
        if self._repeat(row, 'ACTION', request):
            return self.get(identifier, owner)
        catalogue = self._guided_catalog(binding)
        if catalogue is None:
            raise PackagePlayError('GUIDED_PLAY_NOT_AVAILABLE', 404)
        self._current(row)
        try:
            self._lock(ScriptPackagePlay, 'play_id', identifier)
            row = self._row(identifier, owner)
            package, binding = self._resolve(row)
            if self._repeat(row, 'ACTION', request):
                return self.get(identifier, owner)
            state = self._replay(row, package, binding)
            if request['expected_revision'] != state.revision:
                raise PackagePlayError('PACKAGE_PLAY_REVISION_CONFLICT')
            if state.pending:
                raise PackagePlayError('GUIDED_AI_PENDING')
            data = {'policy': GUIDED_POLICY}
            if request['action'] == 'INVESTIGATE_ROUND':
                data['batch'] = plan_round(state.engine, request['payload']['action_ids'])
            if request['action'] == 'REQUEST_HINT':
                hint = catalogue.hint(state.engine.state()['current_phase_id'], **request['payload'])
                if any(e['topic_id'] == hint['topic_id'] and e['level'] == hint['level'] for e in state.host_hint_entries):
                    return self._view(row, package, binding, state)
                data.update(catalog_hash=catalogue.revision, hint=hint)
            with self.db.begin_nested():
                self._append(row, binding, state, 'ACTION', request, data)
                if request['action'] != 'REQUEST_HINT':
                    self._present_required(row, binding, state)
                if request['action'] == 'FINISH_INVESTIGATION':
                    advance = {'action': 'ADVANCE_PHASE', 'expected_revision': state.revision,
                        'idempotency_key': content_hash({'guided_finish': request['idempotency_key'], 'play': identifier})}
                    self._append(row, binding, state, 'ACTION', advance, {})
            return self._view(row, package, binding, state)
        except PlayRulesError as exc:
            raise PackagePlayError(exc.code) from None
        except (OperationalError, IntegrityError):
            raise PackagePlayError('PACKAGE_PLAY_WRITE_CONFLICT') from None

    def get(self, identifier: str, owner: int) -> dict:
        row = self._row(identifier, owner)
        package, binding = self._resolve(row)
        return self._view(row, package, binding, self._replay(row, package, binding))

    def image(self, identifier: str, visual_id: str, owner: int, *, source_store=None) -> tuple[bytes, str]:
        """Only a frozen whole-image grant attached to an already visible item."""
        from src.fusion.source_bundles import SourceBundleStore, SourceBundleError
        row = self._row(identifier, owner)
        package, binding = self._resolve(row)
        state = self._replay(row, package, binding)
        projection = state.engine.view()
        if self.presentation_repair:
            projection = self.presentation_repair.apply({**projection, 'package_hash': row.package_hash,
                                                        'selected_character_id': row.selected_character_id})
        if visual_id not in {v['id'] for v in projection.get('visuals', [])}:
            raise PackagePlayError('PACKAGE_PLAY_IMAGE_NOT_FOUND', 404)
        visual = next((v for v in package.get('visuals', []) if v['id'] == visual_id), None)
        if visual is None and self.presentation_repair:
            visual = self.presentation_repair.visual(row.package_hash, visual_id)
        if visual is None:
            raise PackagePlayError('PACKAGE_PLAY_IMAGE_NOT_FOUND', 404)
        source = next(s for s in package['sources'] if s['id'] == visual['source_id'])
        try:
            # Read the original release even if later approvals changed; acquired
            # material remains readable under the immutable old opening binding.
            release = self.publisher.get_release(row.release_id, require_current=False)
            if (release['release_hash'] != binding['release_hash'] or release['package_hash'] != row.package_hash
                    or release['version_id'] != row.version_id or release['id'] != row.release_id):
                raise ValueError
            store = source_store or getattr(self.publisher, 'source_store', None) or SourceBundleStore()
            manifest = store.manifest(release['bundle_hash'])
            frozen = next((s for s in manifest.sources if s.relative_path == source['relative_path']), None)
            if (frozen is None or frozen.kind != 'original' or frozen.sha256 != source['sha256']
                    or frozen.media_type != source['media_type']):
                raise ValueError
            data, media = store.read_source(release['bundle_hash'], frozen.id)
            if (source['kind'] != 'original' or media not in ('image/png', 'image/jpeg')
                    or media != source['media_type'] or sha256(data).hexdigest() != source['sha256']):
                raise ValueError
            return data, media
        except (SourceBundleError, ValueError, TypeError, KeyError, OSError):
            raise PackagePlayError('PACKAGE_PLAY_IMAGE_UNAVAILABLE', 409) from None

    def find_for_opening(self, identifier: str, owner: int) -> dict | None:
        self._actor(owner)
        self._opening(identifier, owner)
        row = self.db.query(ScriptPackagePlay).filter_by(opening_session_id=identifier, owner_user_id=owner).populate_existing().one_or_none()
        return self.get(row.play_id, owner) if row else None

    def list_library(self, owner: int, offset: int = 0, limit: int = 20) -> dict:
        """Read-only account library. Replay only this page, return no materials."""
        self._actor(owner)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise PackagePlayError('PACKAGE_PLAY_LIBRARY_PAGE_INVALID', 422)
        latest_event = (select(func.max(ScriptPackagePlayEvent.created_at))
                        .where(ScriptPackagePlayEvent.play_id == ScriptPackagePlay.play_id)
                        .correlate(ScriptPackagePlay).scalar_subquery())
        activity = case((latest_event > ScriptPackagePlay.created_at, latest_event),
                        else_=ScriptPackagePlay.created_at)
        rows = (self.db.query(ScriptPackagePlay, activity.label('last_activity'))
                .filter(ScriptPackagePlay.owner_user_id == owner)
                .order_by(activity.desc(), ScriptPackagePlay.id.desc())
                .offset(offset).limit(limit + 1).all())
        items = []
        for row, updated_at in rows[:limit]:
            view = self.get(row.play_id, owner)
            kind = (view.get('full_game') or {}).get('phase_kind')
            phase_label = ('已结束' if view['settled'] else
                           {'READING': '阅读材料', 'INVESTIGATION': '共同调查',
                            'FINALE': '终局答卷'}.get(kind,
                                '阅读材料' if view['can_advance'] else '终局答卷'))
            items.append({
                'play_id': row.play_id, 'opening_session_id': row.opening_session_id,
                'title': view['script']['title'],
                'character_name': next(c['name'] for c in view['characters']
                                       if c['id'] == view['selected_character_id']),
                'phase_label': phase_label, 'settled': view['settled'],
                'revision': view['revision'], 'updated_at': updated_at.isoformat() + 'Z',
            })
        return {'items': items, 'has_more': len(rows) > limit}

    def _repeat(self, row, kind: str, request: dict) -> bool:
        event = self.db.query(ScriptPackagePlayEvent).filter_by(play_id=row.play_id, idempotency_key=self._key(kind, request)).populate_existing().one_or_none()
        if event is not None and (event.request_hash != content_hash(request) or event.kind != kind):
            raise PackagePlayError("PACKAGE_PLAY_KEY_CONFLICT")
        return event is not None

    def act(self, identifier: str, body, owner: int) -> dict:
        request = self._request(body, PackagePlayActionRequest)
        return self._act(identifier, request, owner)

    def speak(self, identifier: str, body, owner: int) -> dict:
        request = self._request(body, PackagePlaySpeakRequest)
        return self._act(identifier, request, owner)

    def table(self, identifier: str, body, owner: int) -> dict:
        request = self._request(body, FullPlayActionRequest)
        return self._act(identifier, request, owner)

    def discussion_context(self, identifier: str, owner: int, character_id: str) -> dict:
        """Internal input for future role decisions, never a truth/goal source.

        The caller cannot supply event text, speaker, phase or audience. This
        deliberately does not change the frozen legacy material-only prompt.
        """
        from src.fusion.role_decision import RoleEvent

        row = self._row(identifier, owner)
        package, binding = self._resolve(row)
        audience = [item["id"] for item in package["characters"]]
        if character_id not in audience:
            raise PackagePlayError("PACKAGE_PLAY_AI_CHARACTER_INVALID", 422)
        state = self._replay(row, package, binding)
        return {"schema_version": "package-discussion-context/1.0", "play_id": row.play_id,
                "binding_hash": row.binding_hash, "role_id": character_id, "revision": state.revision,
                "current_phase_id": state.engine.state()["current_phase_id"],
                "events": [RoleEvent.model_validate({**{key: value for key, value in item.items() if key != "phase_id"},
                                                    "audience": audience}).model_dump() for item in state.discussion]}

    def _act(self, identifier: str, request: dict, owner: int) -> dict:
        row = self._row(identifier, owner)
        package, binding = self._resolve(row)
        if self._repeat(row, "ACTION", request):
            return self.get(identifier, owner)
        self._current(row)
        try:
            self._lock(ScriptPackagePlay, "play_id", identifier)
            row = self._row(identifier, owner)
            package, binding = self._resolve(row)
            if self._repeat(row, "ACTION", request):
                return self.get(identifier, owner)
            state = self._replay(row, package, binding)
            if request["expected_revision"] != state.revision:
                raise PackagePlayError("PACKAGE_PLAY_REVISION_CONFLICT")
            with self.db.begin_nested():
                self._append(row, binding, state, "ACTION", request, {})
                self._present_required(row, binding, state)
            return self._view(row, package, binding, state)
        except PlayRulesError as exc:
            raise PackagePlayError(exc.code) from None
        except (OperationalError, IntegrityError):
            raise PackagePlayError("PACKAGE_PLAY_WRITE_CONFLICT") from None

    def _check_request_scope(self, state: Replay, request: dict, human: str):
        """Admission only; never rerun this against recorded events or results."""
        if self.request_scope_policy is None:
            return
        question = request.get('question')
        if request.get('schema_version') == 'package-dialogue-command/1.0':
            phase = state.engine.state()['current_phase_id']
            target = next((item for item in state.discussion
                if item['id'] == request['reply_to'] and item['phase_id'] == phase), None)
            if target is None:
                raise PlayRulesError('PACKAGE_PLAY_REPLY_TARGET_INVALID')
            question = target['text']
        elif request.get('schema_version') == 'package-private-dialogue-command/1.0':
            # Check current call, participants and exact target authorization
            # before obtaining any private message text.
            state.engine.private_reply_context(request['character_id'], request['reply_to'])
            target = next(item for item in state.engine.personal_discussion(request['character_id'])
                if item['id'] == request['reply_to'])
            question = target['text']
        elif request.get('schema_version') == 'package-phone-command/1.0':
            # The anonymous phone controller can also answer the human. Derive
            # its current authorized target through the engine, never a client
            # supplied speaker ID; AI-to-AI dialogue is not player admission.
            context = state.engine.phone_context(sum(r['stage'] == 'IDLE' for r in state.phone_requests))
            if context['stage'] == 'CONNECTED':
                target = next(item for item in context['private_claims'] if item['id'] == context['reply_to'])
                if target['speaker'] == human:
                    question = target['text']
        if question is not None and refuses_player_request(question):
            raise PackagePlayError('PACKAGE_PLAY_OUT_OF_SCOPE', 422)
        if question is not None and needs_question_clarification(question):
            raise PackagePlayError('PACKAGE_PLAY_QUESTION_CLARIFICATION_REQUIRED', 422)

    def _begin(self, identifier: str, request: dict, owner: int):
        calling = request.get('schema_version') == 'package-phone-command/1.0'
        deciding = calling or request.get('schema_version') == 'package-table-decision-command/1.0'
        proposing = request.get("schema_version") == "package-investigation-command/1.0"
        private = request.get('schema_version') == 'package-private-dialogue-command/1.0'
        responding = private or request.get("schema_version") == "package-dialogue-command/1.0"
        row = self._row(identifier, owner)
        package, binding = self._resolve(row)
        state = self._replay(row, package, binding)
        if self._repeat(row, "AI_REQUEST", request):
            pending = state.pending.get(request["idempotency_key"])
            if pending and self.now() >= pending["expires_at"]:
                return self._finish(identifier, request["idempotency_key"], owner, None, expired=True), None
            return self._view(row, package, binding, state), None
        if self._guided_catalog(binding) is not None and (calling or (deciding and request.get('action') != 'SEAL_FINALE')):
            raise PackagePlayError('GUIDED_HUMAN_CHOICE_REQUIRED')
        self._check_topic_admission(state,binding,request,responding,proposing,deciding)
        self._current(row)
        try:
            self._lock(ScriptPackagePlay, "play_id", identifier)
            row = self._row(identifier, owner)
            package, binding = self._resolve(row)
            if self._repeat(row, "AI_REQUEST", request):
                return self.get(identifier, owner), None
            state = self._replay(row, package, binding)
            if request["expected_revision"] != state.revision:
                raise PackagePlayError("PACKAGE_PLAY_REVISION_CONFLICT")
            self._check_topic_admission(state,binding,request,responding,proposing,deciding)
            if state.pending:
                pending_key, pending = next(iter(state.pending.items()))
                if self.now() >= pending["expires_at"]:
                    self._record_result(row, binding, state, pending_key, "EXPIRED", [], None)
                    self.db.commit()
                    raise PackagePlayError("PACKAGE_PLAY_REVISION_CONFLICT")
                raise PackagePlayError("PACKAGE_PLAY_AI_BUSY")
            if (deciding or private) and not isinstance(state.engine, PackageFullPlayRules):
                raise PlayRulesError('FULL_PLAY_UNSUPPORTED')
            reason = (self._phone_reason(binding,state) if calling else self._table_reason(binding, state) if deciding else self._dialogue_reason(binding, state) if responding else self._proposal_reason(binding, state)
                      if proposing else self._model_reason(binding))
            if isinstance(state.engine, PackageFullPlayRules) and not deciding:
                state.engine.require_discussion()
            if reason or state.questions >= self._question_limit(state) or state.engine.view()["settled"]:
                raise PackagePlayError("PACKAGE_PLAY_AI_UNAVAILABLE")
            if request.get("character_id") == row.selected_character_id:
                raise PackagePlayError("PACKAGE_PLAY_AI_CHARACTER_INVALID", 422)
            self._check_request_scope(state, request, row.selected_character_id)
            context = (self._phone_context(state,binding,version=self.phone_model.model_contract) if calling else self._table_context(state, binding, request['character_id'], request['action'], version=self.table_model.model_contract) if deciding
                       else self._dialogue_context(state, binding, request["character_id"], request["reply_to"], private=private, version=self.full_dialogue_model.model_contract if isinstance(state.engine, PackageFullPlayRules) else None) if responding
                       else self._proposal_context(state, binding, request["character_id"]) if proposing
                       else state.engine.role_context(request["character_id"]))
            model = self.phone_model if calling else self.table_model if deciding else self.dialogue_model if responding else self.proposal_model if proposing else self.model
            if isinstance(state.engine, PackageFullPlayRules) and (responding or proposing):
                model = self.full_dialogue_model if responding else self._proposal_adapter(binding, True)
            prepared = model.prepare(context, 'CALL_TURN' if calling else 'DECIDE' if deciding else "RESPOND" if responding else "PROPOSE" if proposing else request["question"])
            reserved = state.policy.amount(prepared["input_tokens"], prepared["output_tokens"])
            if not state.policy.decide(state.used, state.reserved(), reserved).allowed:
                raise PackagePlayError("PACKAGE_PLAY_BUDGET_EXCEEDED")
            issued = int(self.now())
            timeout = binding["model"].get("timeout_seconds", 25)
            data = {"context_hash": content_hash(context), "prepared_hash": content_hash(prepared),
                    "reservation": reserved.to_metadata(), "issued_at": issued,
                    "expires_at": issued + min(300, max(10, int(timeout) + 15))}
            if proposing or responding or deciding:
                data["model"] = model.metadata()
            self._append(row, binding, state, "AI_REQUEST", request, data)
            self.db.commit()
            return None, prepared
        except (PlayRulesError, PackageRoleModelError) as exc:
            raise PackagePlayError(exc.code) from None
        except (OperationalError, IntegrityError):
            raise PackagePlayError("PACKAGE_PLAY_WRITE_CONFLICT") from None

    def _check_topic_admission(self, state, binding, request, responding, proposing, deciding):
        if not self._single_enabled(binding,state):
            return
        if deciding:
            if request.get('action') == 'SEAL_FINALE':
                return
            raise PackagePlayError('SINGLE_TOPIC_REQUIRED')
        if self._single_catalog(binding) is None or proposing or not responding:
            raise PackagePlayError('SINGLE_TOPIC_REQUIRED')
        turn = next((t for t in state.topic_turns if topic_response_request(t) == request),None)
        if turn is None or topic_status(turn,state) != 'READY':
            raise PackagePlayError('SINGLE_TOPIC_REQUIRED')

    def _record_result(self, row, binding, state, request_id, status, refs, usage, proposal=None, speech=None, decision=None):
        accounted = self._account(state.policy, state.pending[request_id]["reservation"], usage)
        request = {"idempotency_key": request_id, "request_id": request_id}
        data = {"status": status, "refs": refs, "usage": usage,
                "accounted": accounted.to_metadata(), "received_at": int(self.now())}
        if state.pending[request_id].get("operation") == "PROPOSE":
            data["proposal"] = proposal
        if state.pending[request_id].get("operation") in ('RESPOND', 'RESPOND_PRIVATE'):
            data["speech"] = speech
        if state.pending[request_id].get('operation') in ('DECIDE','PHONE'):
            data['decision'] = decision
        self._append(row, binding, state, "AI_RESULT", request, data)

    def _finish(self, identifier: str, request_id: str, owner: int, result, expired=False) -> dict:
        result = result if type(result) is dict else None
        row = self._row(identifier, owner)
        current = False
        if not expired:
            try:
                self._current(row)
                current = True
            except PackagePlayError:
                # Accounting survives invalidation; no public grant is possible.
                self.db.rollback()
        try:
            self._lock(ScriptPackagePlay, "play_id", identifier)
            row = self._row(identifier, owner)
            package, binding = self._resolve(row)
            state = self._replay(row, package, binding)
            pending = state.pending.get(request_id)
            if pending is None:
                return self._view(row, package, binding, state)
            proposing = pending.get("operation") == "PROPOSE"
            private = pending.get('operation') == 'RESPOND_PRIVATE'
            responding = private or pending.get("operation") == "RESPOND"
            calling = pending.get('operation') == 'PHONE'
            deciding = calling or pending.get('operation') == 'DECIDE'
            usage = result.get("usage") if type(result) is dict else None
            if result is not None and result.get("model_attempted") is False:
                usage = UsageAmount().to_metadata()
            try:
                self._account(state.policy, pending["reservation"], usage)
            except (ValueError, TypeError, KeyError):
                usage = None
            status, refs, proposal, speech, decision = "UNKNOWN", [], None, None, None
            if expired:
                status, usage = "EXPIRED", None
            elif result and result.get("status") == "OK" and usage is not None:
                reason = (self._phone_reason(binding,state) if calling else self._table_reason(binding, state) if deciding else self._dialogue_reason(binding, state) if responding else self._proposal_reason(binding, state)
                          if proposing else self._model_reason(binding))
                if (not current or reason is not None or state.revision != pending["revision"]
                        or self.now() > pending["expires_at"] or state.engine.view()["settled"]):
                    status = "STALE"
                else:
                    # Validate atomically on a copy before appending authoritative state.
                    from copy import deepcopy
                    try:
                        if calling:
                            context=self._phone_context(state,binding,pending['revision']-1,pending=pending)
                            candidate=validate_call(result.get('decision'),context,pending['model']['schema_version'])
                            deepcopy(state.engine).apply_phone(pending['character_id'],candidate,state.revision+1)
                            decision,status=candidate,'OK'
                        elif deciding:
                            context = self._table_context(state, binding, pending['character_id'], pending['action'], pending['revision'] - 1)
                            candidate = validate_table_decision(result.get('decision'), context)
                            deepcopy(state.engine).table_apply(pending['action'], pending['character_id'], candidate, state.revision + 1)
                            decision, status = candidate, 'OK'
                        elif responding:
                            context = self._dialogue_context(state, binding, pending["character_id"], pending["reply_to"], pending["revision"] - 1, private=private)
                            speech = validate_speech(result.get("speech"), context, pending['model']['schema_version'])
                            validate_topic_answer(speech, next((t for t in state.topic_turns if t['reply_to'] == pending['reply_to']),None))
                            if private:
                                rendered = render_speech(speech, context, state.revision + 1, pending['model']['schema_version'])
                                deepcopy(state.engine).table_apply('PRIVATE_SPEAK', pending['character_id'], {'text': rendered['text']}, state.revision + 1)
                            status = "OK"
                        elif proposing:
                            context = self._proposal_context(state, binding, pending["character_id"], pending["revision"] - 1)
                            proposal = validate_proposal(result.get("proposal"), context, pending["model"]["schema_version"])
                            status = "OK"
                        else:
                            deepcopy(state.engine).apply_reply(pending["character_id"], result.get("refs"))
                            status, refs = "OK", result["refs"]
                    except (PlayRulesError, ValueError, TypeError, KeyError):
                        status, speech, proposal, decision = "INVALID", None, None, None
            elif usage is not None:
                status = "INVALID"
            self._record_result(row, binding, state, request_id, status, refs, usage, proposal, speech, decision)
            # A paid result is durable before any optional authored continuation.
            self.db.commit()
            if status == 'OK' and current and self._guided_catalog(binding) is not None:
                try:
                    self._lock(ScriptPackagePlay, 'play_id', identifier)
                    row = self._row(identifier, owner)
                    package, binding = self._resolve(row)
                    self._current(row)
                    state = self._replay(row, package, binding)
                    with self.db.begin_nested():
                        self._present_required(row, binding, state)
                    self.db.commit()
                except (PackagePlayError, PlayRulesError, OperationalError, IntegrityError):
                    # Keep the paid receipt; an eligible unsent template remains
                    # visible through can_present_required for an explicit retry.
                    self.db.rollback()
                    row = self._row(identifier, owner)
                    package, binding = self._resolve(row)
                    state = self._replay(row, package, binding)
            return self._view(row, package, binding, state)
        except (OperationalError, IntegrityError):
            raise PackagePlayError("PACKAGE_PLAY_WRITE_CONFLICT") from None

    async def ask(self, identifier: str, body, owner: int) -> dict:
        request = self._request(body, PackagePlayAskRequest)
        repeated, prepared = self._begin(identifier, request, owner)
        if prepared is None:
            return repeated
        try:
            result = await self.model.call(prepared)
        except Exception:
            # Never expose provider errors or retry an outcome we cannot know.
            result = None
        return self._finish(identifier, request["idempotency_key"], owner, result)

    async def propose(self, identifier: str, body, owner: int) -> dict:
        request = self._request(body, PackagePlayProposalRequest)
        repeated, prepared = self._begin(identifier, request, owner)
        if prepared is None:
            return repeated
        try:
            full = json.loads(prepared['messages'][1]['content'])['context']['schema_version'] == 'package-investigation-context/1.1'
            adapter = self.guided_proposal_model if prepared['messages'][0]['content'] == self.guided_proposal_model.prompt else self.full_proposal_model if full else self.proposal_model
            result = await adapter.call(prepared)
        except Exception:
            result = None
        return self._finish(identifier, request["idempotency_key"], owner, result)

    async def respond(self, identifier: str, body, owner: int) -> dict:
        request = self._request(body, PackagePlayRespondRequest)
        repeated, prepared = self._begin(identifier, request, owner)
        if prepared is None:
            return repeated
        try:
            full = json.loads(prepared['messages'][1]['content'])['context']['schema_version'] in ('package-dialogue-context/1.1', 'package-dialogue-context/1.2', 'package-dialogue-context/1.3', 'package-dialogue-context/1.4')
            result = await (self.full_dialogue_model if full else self.dialogue_model).call(prepared)
        except Exception:
            result = None
        return self._finish(identifier, request["idempotency_key"], owner, result)

    async def decide(self, identifier: str, body, owner: int) -> dict:
        request = self._request(body, FullPlayDecisionRequest)
        repeated, prepared = self._begin(identifier, request, owner)
        if prepared is None:
            return repeated
        try:
            result = await self.table_model.call(prepared)
        except Exception:
            result = None
        return self._finish(identifier, request['idempotency_key'], owner, result)

    async def reply_private(self, identifier: str, body, owner: int) -> dict:
        request = self._request(body, FullPlayPrivateReplyRequest)
        repeated, prepared = self._begin(identifier, request, owner)
        if prepared is None:
            return repeated
        try:
            result = await self.full_dialogue_model.call(prepared)
        except Exception:
            result = None
        return self._finish(identifier, request['idempotency_key'], owner, result)

    async def phone_step(self,identifier,body,owner):
        request=self._request(body,FullPlayPhoneRequest)
        repeated,prepared=self._begin(identifier,request,owner)
        if prepared is None:return repeated
        try:result=await self.phone_model.call(prepared)
        except Exception:result=None
        return self._finish(identifier,request['idempotency_key'],owner,result)

    def pause_phone(self,identifier,body,owner):
        return self._act(identifier,self._request(body,FullPlayPhonePauseRequest),owner)
