"""One paid Fusion turn through an isolated PostgreSQL schema.

The harness is intentionally narrower than the application: it has no prompt,
file, script, session, or output-path input.  It creates one tiny fictional game,
verifies the role projection immediately before network I/O, allows one SDK
request with retries disabled, inspects the persisted accounting receipt, and
drops the random schema before returning a content-free result.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
import json
import os
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Iterator, Sequence, cast
from uuid import uuid4

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from scripts.test_fusion_postgres_budget import (
    UnsafeTestDatabase,
    ValidatedTestDatabase,
    validate_test_database_environment,
)
from src.db.base import SQLAlchemyBase
from src.db.models import (
    BackgroundStoryDBModel,
    CharacterDBModel,
    EvidenceDBModel,
    LocationDBModel,
    ScriptDBModel,
    User,
)
from src.db.models.game_event import GameEventDBModel
from src.db.models.game_session import GameSession
from src.db.models.script_model import ScriptStatus
from src.fusion.agents import FusionAgentOrchestrator
from src.fusion.budget import UsageAmount, usage_metadata_is_valid
from src.fusion.provider_smoke import (
    SelectedSmokeConfig,
    SmokeConfigurationError,
    load_selected_config,
    parse_confirmed_cost,
    write_sanitized_receipt,
)
from src.fusion.providers import PLAYER_PROVIDER_PROFILES
from src.fusion.service import FusionGameService
from src.services.llm_service import BaseLLMService, LLMMessage, LLMResponse, OpenAILLMService


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_RECEIPT_DIR = REPOSITORY_ROOT / ".runtime" / "live-vertical-smoke"
LIVE_FIXTURE_ID = "synthetic-fusion-vertical-v1"
EPHEMERAL_SCHEMA_PREFIX = "fusion_live_it_"
# The real provider needed more than 96 tokens even for the short strict-JSON
# fixture.  256 avoids a false truncation while keeping the local worst-case
# reservation far below the CLI's one-cent ceiling.
LIVE_MAX_OUTPUT_TOKENS = 256
LIVE_TIMEOUT_SECONDS = 30

# All content below is invented for this connectivity test.  The canaries make
# an accidental widening of RoleKnowledgeView fail before the paid request.
AUTHORIZED_ROLE_MARKER = "SYNTHETIC_ALLOWED_ROLE_BLUE_BRIDGE"
AUTHORIZED_PUBLIC_MARKER = "SYNTHETIC_ALLOWED_PUBLIC_COPPER_17"
FORBIDDEN_SYSTEM_MARKER = "SYNTHETIC_FORBIDDEN_SYSTEM_TRUTH"
FORBIDDEN_OTHER_ROLE_MARKER = "SYNTHETIC_FORBIDDEN_OTHER_ROLE_SECRET"
_QUESTION = "只根据你已知的虚构信息，简短说明蓝桥边的纸鹤和公开铜牌线索。"
_FIXTURE_HASH = "sha256:" + sha256(json.dumps({
    "fixture_id": LIVE_FIXTURE_ID,
    "role_marker": AUTHORIZED_ROLE_MARKER,
    "public_marker": AUTHORIZED_PUBLIC_MARKER,
    "system_canary": FORBIDDEN_SYSTEM_MARKER,
    "other_role_canary": FORBIDDEN_OTHER_ROLE_MARKER,
    "question": _QUESTION,
}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class VerticalSmokeSafetyError(RuntimeError):
    """A local safety invariant failed; its message is never emitted."""


@dataclass
class EphemeralSchemaState:
    fingerprint: str | None = None
    created: bool = False
    cleanup_completed: bool = False


@dataclass(frozen=True)
class SeededVerticalGame:
    session_id: str
    user_id: int
    target_character_id: int
    question_key: str


class VerticalSmokeFusionService(FusionGameService):
    """Expose only the two protected operations needed by this fixed harness."""

    def prepare_synthetic_canaries(
        self,
        session_id: str,
        user_id: int,
        other_character_id: int,
    ) -> None:
        game_session = self._owned_session(session_id, user_id, for_update=True)
        cast(Any, game_session).current_phase = "INVESTIGATION"
        self._append_event(
            game_session,
            "SYNTHETIC_PUBLIC_FACT",
            {"content": AUTHORIZED_PUBLIC_MARKER},
            visibility="PUBLIC",
        )
        self._append_event(
            game_session,
            "SYNTHETIC_SYSTEM_CANARY",
            {"content": FORBIDDEN_SYSTEM_MARKER},
            visibility="SYSTEM_TRUTH",
        )
        self._append_event(
            game_session,
            "SYNTHETIC_OTHER_ROLE_CANARY",
            {"content": FORBIDDEN_OTHER_ROLE_MARKER},
            visibility="CHARACTER_PRIVATE",
            recipient=other_character_id,
        )
        self.db.commit()

    async def reply_to_synthetic_question(
        self,
        seeded: SeededVerticalGame,
        runtime: FusionAgentOrchestrator,
    ) -> dict | None:
        return await self._reply_to_action(
            seeded.session_id,
            seeded.user_id,
            seeded.question_key,
            seeded.target_character_id,
            "QUESTION_ASKED",
            runtime,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="单次真实 Fusion 纵向验收：只使用内置虚构数据和隔离 PostgreSQL schema。",
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=("aliyun_bailian", "volcengine_ark"),
        help="必须显式选择一家供应商，不会自动回退。",
    )
    parser.add_argument(
        "--max-cost-cny",
        required=True,
        help="本次本地估算确认线，必须大于 0 且不超过 0.01 元。",
    )
    parser.add_argument(
        "--confirm-one-paid-call",
        required=True,
        action="store_true",
        help="确认最多发出一次可能扣费的虚构数据请求。",
    )
    return parser


def _fingerprint(value: str | None) -> str | None:
    if not value:
        return None
    return "sha256:" + sha256(value.encode()).hexdigest()[:16]


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


@contextmanager
def selected_runtime_environment(
    config: SelectedSmokeConfig,
    confirmed_cost_cny: Decimal,
) -> Iterator[None]:
    """Temporarily expose only the selected, already-validated runtime profile."""
    confirmed_cost_cny = parse_confirmed_cost(str(confirmed_cost_cny))
    profile = config.profile
    managed_names = {
        "PYTHON_DOTENV_DISABLED",
        "ENABLE_PAID_MODEL_CALLS",
        "FUSION_PLAYER_PROVIDER",
        "GAME_TOKEN_BUDGET",
        "GAME_COST_BUDGET_CNY",
        "LLM_MAX_RETRIES",
        "LLM_MAX_TOKENS",
        "LLM_TIMEOUT_SECONDS",
        "LLM_TEMPERATURE",
        "FUSION_PLAYER_MAX_INPUT_BYTES",
        "REDIS_URL",
    }
    for known in ("volcengine_ark", "aliyun_bailian"):
        known_profile = PLAYER_PROVIDER_PROFILES[known]
        managed_names.update({
            known_profile.api_key_env,
            known_profile.base_url_env,
            known_profile.model_env,
            f"{known_profile.pricing_env_prefix}_INPUT_COST_PER_MILLION",
            f"{known_profile.pricing_env_prefix}_CACHED_INPUT_COST_PER_MILLION",
            f"{known_profile.pricing_env_prefix}_OUTPUT_COST_PER_MILLION",
            f"{known_profile.pricing_env_prefix}_PRICING_VERSION",
        })
    previous = {name: os.environ.get(name) for name in managed_names}
    try:
        for name in managed_names:
            os.environ.pop(name, None)
        prefix = profile.pricing_env_prefix
        os.environ.update({
            "PYTHON_DOTENV_DISABLED": "1",
            "ENABLE_PAID_MODEL_CALLS": "true",
            "FUSION_PLAYER_PROVIDER": profile.name,
            # Cost is the authoritative guard.  This token ceiling is generous
            # enough for the fixed small prompt while remaining finite.
            "GAME_TOKEN_BUDGET": "100000",
            "GAME_COST_BUDGET_CNY": _decimal_text(confirmed_cost_cny),
            "LLM_MAX_RETRIES": "0",
            "LLM_MAX_TOKENS": str(LIVE_MAX_OUTPUT_TOKENS),
            "LLM_TIMEOUT_SECONDS": str(LIVE_TIMEOUT_SECONDS),
            "LLM_TEMPERATURE": "0",
            "FUSION_PLAYER_MAX_INPUT_BYTES": "24000",
            profile.base_url_env: config.base_url,
            profile.model_env: config.model,
            f"{prefix}_INPUT_COST_PER_MILLION": _decimal_text(config.pricing.input_rate_cny),
            f"{prefix}_CACHED_INPUT_COST_PER_MILLION": _decimal_text(
                config.pricing.cached_input_rate_cny,
            ),
            f"{prefix}_OUTPUT_COST_PER_MILLION": _decimal_text(config.pricing.output_rate_cny),
            f"{prefix}_PRICING_VERSION": config.pricing.pricing_version,
        })
        yield
    finally:
        for name in managed_names:
            old_value = previous[name]
            if old_value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = old_value


class OneShotProjectionClient:
    """Fail before network on widened knowledge or a second provider attempt."""

    def __init__(self, inner: BaseLLMService):
        self.inner = inner
        self.network_call_count = 0
        self.projection_checked = False
        self.reasoning_detected = False

    async def chat_completion(self, messages: list[LLMMessage], **kwargs: Any) -> LLMResponse:
        if self.network_call_count != 0:
            raise VerticalSmokeSafetyError("second provider call refused")
        serialized = "\n".join(message.content for message in messages)
        if (
            FORBIDDEN_SYSTEM_MARKER in serialized
            or FORBIDDEN_OTHER_ROLE_MARKER in serialized
        ):
            raise VerticalSmokeSafetyError("unauthorized projection refused")
        if (
            AUTHORIZED_ROLE_MARKER not in serialized
            or AUTHORIZED_PUBLIC_MARKER not in serialized
        ):
            raise VerticalSmokeSafetyError("required authorized projection missing")
        self.projection_checked = True
        self.network_call_count = 1
        response = await self.inner.chat_completion(messages, **kwargs)
        self.reasoning_detected = bool(response.reasoning_content)
        return response


@contextmanager
def isolated_postgres_schema(
    target: ValidatedTestDatabase,
    state: EphemeralSchemaState,
) -> Iterator[Engine]:
    """Create every table in one random schema, then remove it unconditionally."""
    schema = f"{EPHEMERAL_SCHEMA_PREFIX}{uuid4().hex}"
    state.fingerprint = _fingerprint(schema)
    bootstrap = create_engine(
        target.url,
        pool_pre_ping=True,
        hide_parameters=True,
        connect_args={
            "application_name": "fusion-live-vertical-bootstrap",
            "connect_timeout": 5,
        },
    )
    engine: Engine | None = None
    created = False
    quoted_schema = bootstrap.dialect.identifier_preparer.quote(schema)
    try:
        with bootstrap.begin() as connection:
            database, user, read_only, address, port = connection.execute(text(
                "SELECT current_database(), current_user, "
                "current_setting('transaction_read_only'), "
                # Casting PostgreSQL's inet value to text retains its /32 mask;
                # host() returns the literal address expected by the strict
                # loopback-only target guard.
                "host(inet_server_addr()), inet_server_port()",
            )).one()
            if (
                str(database).lower() != target.database
                or str(user).lower() != "fusion_it"
                or read_only != "off"
                or str(address) != target.host
                or int(port) != target.port
            ):
                raise VerticalSmokeSafetyError("connected database identity mismatch")
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
        created = True
        state.created = True
        engine = create_engine(
            target.url,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=0,
            hide_parameters=True,
            connect_args={
                "application_name": "fusion-live-vertical",
                "connect_timeout": 5,
                "options": (
                    f"-c search_path={schema},public "
                    "-c statement_timeout=45000 -c lock_timeout=10000"
                ),
            },
        )
        SQLAlchemyBase.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            if not schema.startswith(EPHEMERAL_SCHEMA_PREFIX):
                raise VerticalSmokeSafetyError("unsafe schema cleanup target")
            with bootstrap.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {quoted_schema} CASCADE"))
            state.cleanup_completed = True
        bootstrap.dispose()


def seed_synthetic_vertical_game(database: Session) -> SeededVerticalGame:
    """Insert only the fixed fictional fixture and one accepted question."""
    user = User(
        username=f"live_vertical_{uuid4().hex[:10]}",
        email=f"live-vertical-{uuid4().hex[:10]}@example.invalid",
        hashed_password="synthetic-not-a-password-hash",
    )
    script = ScriptDBModel(
        title="虚构蓝桥纵向测试",
        description="仅用于单次真实模型与 PostgreSQL 验收",
        player_count=4,
        duration_minutes=20,
        status=ScriptStatus.PUBLISHED,
        is_public=True,
    )
    database.add_all([user, script])
    database.flush()
    roles = [
        CharacterDBModel(
            script_id=script.id,
            name="许岚",
            profession="虚构图书角志愿者",
            background=f"{AUTHORIZED_ROLE_MARKER}：19:10 在蓝桥边看到无署名纸鹤。",
            secret="她把纸鹤放回原处。",
            objective="只根据已知虚构信息回答。",
            is_murderer=False,
            is_victim=False,
        ),
        CharacterDBModel(
            script_id=script.id,
            name="周河",
            profession="虚构摄影师",
            background="虚构玩家角色背景。",
            secret="他只知道自己丢了相机带。",
            objective="完成测试提问。",
            is_murderer=False,
            is_victim=False,
        ),
        CharacterDBModel(
            script_id=script.id,
            name="叶青",
            profession="虚构钟表师",
            background="另一名虚构角色的背景。",
            secret=FORBIDDEN_OTHER_ROLE_MARKER,
            objective="保管自己的虚构秘密。",
            is_murderer=True,
            is_victim=False,
        ),
        CharacterDBModel(
            script_id=script.id,
            name="陈珂",
            profession="虚构维修员",
            background="第四名虚构角色的背景。",
            secret="只知道桥灯曾经闪烁。",
            objective="区分事实与猜测。",
            is_murderer=False,
            is_victim=False,
        ),
    ]
    database.add_all(roles)
    database.add(LocationDBModel(
        script_id=script.id,
        name="虚构蓝桥",
        description="不对应任何商业剧本的测试地点。",
    ))
    database.add(EvidenceDBModel(
        script_id=script.id,
        name="虚构铜牌",
        location="虚构蓝桥",
        description="铜牌上写着 17。",
    ))
    database.add(BackgroundStoryDBModel(
        script_id=script.id,
        title="虚构纸鹤事件",
        setting_description="一个不存在的测试社区。",
        incident_description="桥边出现一只无署名纸鹤。",
        murder_method="无；这是接口测试",
        murder_location="虚构蓝桥",
    ))
    database.commit()

    service = VerticalSmokeFusionService(database)
    script_id = cast(int, script.id)
    user_id = cast(int, user.id)
    role_ids = [cast(int, role.id) for role in roles]
    session_id = service.create_session(script_id, user_id)["session"]["session_id"]
    service.select_character(session_id, user_id, role_ids[1], "live-select-synthetic-0001")
    service.prepare_synthetic_canaries(session_id, user_id, role_ids[2])

    question_key = "live-question-synthetic-0001"
    service.perform_action(session_id, user_id, "ask_question", {
        "target_character_id": role_ids[0],
        "content": _QUESTION,
    }, question_key)
    return SeededVerticalGame(session_id, user_id, role_ids[0], question_key)


def build_base_receipt(
    config: SelectedSmokeConfig,
    target: ValidatedTestDatabase,
    confirmed_cost_cny: Decimal,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture_id": LIVE_FIXTURE_ID,
        "fixture_hash": _FIXTURE_HASH,
        "data_class": "synthetic_noncommercial",
        "provider": config.profile.name,
        "requested_model": config.model,
        "endpoint_fingerprint": _fingerprint(config.base_url),
        "pricing_version": config.pricing.pricing_version,
        "database_target": target.safe_label,
        "request_count": 0,
        "sdk_retries": 0,
        "max_output_tokens": LIVE_MAX_OUTPUT_TOKENS,
        # This protects the local decision only; it is not a cloud-account cap.
        "confirmed_local_estimate_limit_cny": _decimal_text(confirmed_cost_cny),
        "cost_basis": "configured_requested_model_rates",
    }


async def execute_vertical_smoke(
    config: SelectedSmokeConfig,
    target: ValidatedTestDatabase,
    confirmed_cost_cny: Decimal,
    client_factory: Callable[..., BaseLLMService] = OpenAILLMService,
) -> tuple[int, dict[str, object]]:
    """Run the isolated vertical check with at most one provider request."""
    confirmed_cost_cny = parse_confirmed_cost(str(confirmed_cost_cny))
    receipt = build_base_receipt(config, target, confirmed_cost_cny)
    schema_state = EphemeralSchemaState()
    guarded: OneShotProjectionClient | None = None
    reservation: UsageAmount | None = None
    started = monotonic()
    observation: dict[str, Any] | None = None
    try:
        with isolated_postgres_schema(target, schema_state) as engine:
            factory = sessionmaker(bind=engine, expire_on_commit=False)
            database = factory()
            try:
                # Session creation and the accepted synthetic question pass
                # through Fusion's normal action lock.  Clear any legacy
                # REDIS_URL loaded by older application modules so this
                # PostgreSQL-only harness cannot reach an unrelated Redis.
                with selected_runtime_environment(config, confirmed_cost_cny):
                    seeded = seed_synthetic_vertical_game(database)
                with selected_runtime_environment(config, confirmed_cost_cny):
                    inner = client_factory(
                        api_key=config.api_key,
                        base_url=config.base_url,
                        model=config.model,
                        client_max_retries=0,
                    )
                    guarded = OneShotProjectionClient(inner)
                    runtime = FusionAgentOrchestrator(
                        client=cast(BaseLLMService, guarded),
                    )
                    if runtime.settings.retries != 0 or runtime.settings.max_output_tokens != LIVE_MAX_OUTPUT_TOKENS:
                        raise VerticalSmokeSafetyError("runtime retry/output bound mismatch")

                    service = VerticalSmokeFusionService(database)
                    knowledge = service.role_knowledge(
                        seeded.session_id,
                        seeded.user_id,
                        seeded.target_character_id,
                    )
                    role_context, visible_events = knowledge.model_inputs()
                    reserved_tokens = runtime.reservation_tokens(
                        role_context,
                        visible_events,
                        _QUESTION,
                    )
                    reservation = config.pricing.amount(
                        reserved_tokens.prompt_tokens,
                        reserved_tokens.completion_tokens,
                    )
                    if reservation.cost_cny > confirmed_cost_cny:
                        raise SmokeConfigurationError("RESERVATION_EXCEEDS_CONFIRMED_COST")

                    result = await service.reply_to_synthetic_question(seeded, runtime)

                game_session = database.query(GameSession).filter_by(
                    session_id=seeded.session_id,
                ).one()
                receipt_event = database.query(GameEventDBModel).filter_by(
                    session_id=seeded.session_id,
                    event_type="AI_TURN_RECEIPT",
                ).one()
                ai_event = database.query(GameEventDBModel).filter_by(
                    session_id=seeded.session_id,
                    event_type="AI_MESSAGE",
                ).one_or_none()
                metadata = receipt_event.event_metadata
                generations = metadata.get("generations") if isinstance(metadata, dict) else None
                generation = generations[0] if isinstance(generations, list) and len(generations) == 1 else None
                accounting = generation.get("accounting") if isinstance(generation, dict) else None
                accounting_valid = usage_metadata_is_valid(accounting)
                accounting_amount = UsageAmount.from_metadata(accounting) if accounting_valid else reservation
                ai_metadata = ai_event.event_metadata if ai_event and isinstance(ai_event.event_metadata, dict) else {}
                raw_finish_reason = generation.get("finish_reason") if isinstance(generation, dict) else None
                raw_request_id = generation.get("provider_request_id") if isinstance(generation, dict) else None
                response_model = generation.get("response_model") if isinstance(generation, dict) else None
                player_visible_events = VerticalSmokeFusionService(database).get_events(
                    seeded.session_id,
                    seeded.user_id,
                )
                observation = {
                    "result_present": result is not None and ai_event is not None,
                    "result_event_id": ai_event.event_sequence if ai_event else None,
                    "receipt_event_id": receipt_event.event_sequence,
                    "message_chars": len(str(ai_metadata.get("content") or "")),
                    "degraded": ai_metadata.get("degraded") is True,
                    "generation_status": generation.get("status") if isinstance(generation, dict) else None,
                    "model_attempted": generation.get("model_attempted") is True if isinstance(generation, dict) else False,
                    "attempt_count": generation.get("attempt_count") if isinstance(generation, dict) else None,
                    "unknown_attempts": generation.get("unknown_attempts") if isinstance(generation, dict) else None,
                    "accounting_status": generation.get("accounting_status") if isinstance(generation, dict) else None,
                    "accounting_valid": accounting_valid,
                    "accounting": accounting_amount,
                    "response_model_matches": response_model in config.profile.allowed_models,
                    "finish_reason": "stop" if raw_finish_reason == "stop" else (
                        "other" if raw_finish_reason else None
                    ),
                    "provider_request_fingerprint": _fingerprint(
                        raw_request_id if isinstance(raw_request_id, str) else None,
                    ),
                    "receipt_hidden_from_player_events": all(
                        event.get("type") != "AI_TURN_RECEIPT"
                        for event in player_visible_events
                    ),
                    "player_visible_event_count": len(player_visible_events),
                    "session_prompt_tokens": cast(int, game_session.prompt_tokens),
                    "session_completion_tokens": cast(int, game_session.completion_tokens),
                    "session_estimated_cost_cny": str(game_session.estimated_cost),
                }
            finally:
                database.rollback()
                database.close()
    except SmokeConfigurationError:
        raise
    except Exception:
        call_count = guarded.network_call_count if guarded else 0
        receipt.update({
            "status": "safe_failure",
            "result_code": (
                "EPHEMERAL_SCHEMA_CLEANUP_FAILED"
                if schema_state.created and not schema_state.cleanup_completed
                else "POST_CALL_VERTICAL_EXECUTION_FAILED_USAGE_UNKNOWN"
                if call_count
                else "LOCAL_VERTICAL_EXECUTION_FAILED_BEFORE_NETWORK"
            ),
            "request_count": call_count,
            "usage_source": "conservative_reservation" if reservation else None,
            "estimated_cost_cny": _decimal_text(reservation.cost_cny) if reservation else None,
            "schema_fingerprint": schema_state.fingerprint,
            "schema_created": schema_state.created,
            "schema_cleanup_completed": schema_state.cleanup_completed,
            "duration_ms": max(0, int((monotonic() - started) * 1000)),
        })
        return 3, receipt

    if observation is None or guarded is None or reservation is None:
        raise VerticalSmokeSafetyError("missing vertical observation")
    accounting = cast(UsageAmount, observation.pop("accounting"))
    projection_passed = guarded.projection_checked and guarded.network_call_count == 1
    within_confirmed_cost = accounting.cost_cny <= confirmed_cost_cny
    passed = bool(
        schema_state.cleanup_completed
        and projection_passed
        and not guarded.reasoning_detected
        and observation["result_present"]
        and observation["message_chars"]
        and not observation["degraded"]
        and observation["generation_status"] == "COMPLETED"
        and observation["model_attempted"]
        and observation["attempt_count"] == 1
        and observation["unknown_attempts"] == 0
        and observation["accounting_status"] == "PROVIDER_REPORTED"
        and observation["accounting_valid"]
        and observation["response_model_matches"]
        and observation["finish_reason"] == "stop"
        and observation["receipt_hidden_from_player_events"]
        and within_confirmed_cost
    )
    if guarded.network_call_count == 0:
        result_code = "PROJECTION_GUARD_REFUSED_BEFORE_NETWORK"
    elif guarded.reasoning_detected:
        result_code = "REASONING_DETECTED"
    elif not observation["receipt_hidden_from_player_events"]:
        result_code = "PLAYER_VISIBILITY_LEAK"
    elif not observation["model_attempted"]:
        result_code = "MODEL_NOT_ATTEMPTED"
    elif observation["attempt_count"] != 1:
        result_code = "MODEL_ATTEMPT_COUNT_INVALID"
    elif not observation["accounting_valid"] or observation["accounting_status"] != "PROVIDER_REPORTED":
        result_code = "PROVIDER_USAGE_MISSING_OR_UNCERTAIN"
    elif not observation["response_model_matches"]:
        result_code = "RESPONSE_MODEL_MISMATCH"
    elif observation["finish_reason"] != "stop":
        result_code = "NON_NORMAL_FINISH"
    elif not within_confirmed_cost:
        result_code = "CONFIRMED_COST_OVERRUN"
    elif not observation["result_present"] or observation["degraded"]:
        result_code = "OUTPUT_CONTRACT_OR_PROVIDER_FAILED"
    elif not schema_state.cleanup_completed:
        result_code = "EPHEMERAL_SCHEMA_CLEANUP_FAILED"
    else:
        result_code = "PASSED"

    receipt.update({
        "status": "passed" if passed else "safe_failure",
        "result_code": result_code,
        "request_count": guarded.network_call_count,
        "reservation": {
            "prompt_tokens": reservation.prompt_tokens,
            "completion_tokens": reservation.completion_tokens,
            "estimated_cost_cny": _decimal_text(reservation.cost_cny),
        },
        "usage_source": (
            "provider_reported"
            if observation["accounting_status"] == "PROVIDER_REPORTED"
            else "conservative_or_invalid"
        ),
        "usage": {
            "prompt_tokens": accounting.prompt_tokens,
            "completion_tokens": accounting.completion_tokens,
            "cached_prompt_tokens": accounting.cached_prompt_tokens,
            "reasoning_tokens": accounting.reasoning_tokens,
        },
        "estimated_cost_cny": _decimal_text(accounting.cost_cny),
        "knowledge_projection_checked": guarded.projection_checked,
        "forbidden_data_absent_before_network": guarded.projection_checked,
        "reasoning_detected": guarded.reasoning_detected,
        "schema_fingerprint": schema_state.fingerprint,
        "schema_created": schema_state.created,
        "schema_cleanup_completed": schema_state.cleanup_completed,
        **observation,
        "duration_ms": max(0, int((monotonic() - started) * 1000)),
    })
    return (0 if passed else 3), receipt


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        confirmed_cost = parse_confirmed_cost(args.max_cost_cny)
        target = validate_test_database_environment()
        config = load_selected_config(args.provider)
    except UnsafeTestDatabase:
        print(json.dumps({
            "status": "refused_before_network",
            "result_code": "UNSAFE_DATABASE_TARGET",
            "request_count": 0,
        }, ensure_ascii=False, sort_keys=True))
        return 2
    except SmokeConfigurationError as error:
        print(json.dumps({
            "status": "refused_before_network",
            "result_code": error.code,
            "request_count": 0,
        }, ensure_ascii=False, sort_keys=True))
        return 2

    try:
        code, receipt = asyncio.run(execute_vertical_smoke(config, target, confirmed_cost))
    except SmokeConfigurationError as error:
        print(json.dumps({
            "status": "refused_before_network",
            "result_code": error.code,
            "request_count": 0,
        }, ensure_ascii=False, sort_keys=True))
        return 2

    try:
        path = write_sanitized_receipt(receipt, DEFAULT_RECEIPT_DIR)
    except OSError:
        print(json.dumps({**receipt, "receipt_path": None, "receipt_persisted": False},
                         ensure_ascii=False, sort_keys=True))
        return 4
    print(json.dumps({**receipt, "receipt_path": str(path)}, ensure_ascii=False, sort_keys=True))
    return code
