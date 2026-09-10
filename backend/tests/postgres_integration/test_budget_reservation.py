"""Real PostgreSQL row-lock coverage for Fusion's per-game budget reservation.

The dedicated runner validates the target before this module is collected.  A
second validation here protects any future isolated runner too; the supported
entry remains ``backend/scripts/test_fusion_postgres_budget.py`` because the
repository's legacy top-level pytest configuration is not integration-safe.
Only fictional rows are written, all inside one random schema that is removed at
the end of the run.  No model provider client is constructed or called.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
import threading
import time
from types import SimpleNamespace
from typing import Any, Iterator, cast
from uuid import uuid4

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from scripts.test_fusion_postgres_budget import (
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
from src.fusion.agents import AgentTurn, FusionAgentOrchestrator
from src.fusion.budget import UsageAmount
from src.fusion.service import FusionGameService


EPHEMERAL_SCHEMA_PREFIX = "fusion_budget_it_"


@contextmanager
def isolated_postgres_schema() -> Iterator[Engine]:
    target = validate_test_database_environment()
    bootstrap = create_engine(
        target.url,
        pool_pre_ping=True,
        connect_args={
            "application_name": "fusion-budget-it-bootstrap",
            "connect_timeout": 5,
        },
    )
    schema = f"{EPHEMERAL_SCHEMA_PREFIX}{uuid4().hex}"
    created = False
    engine: Engine | None = None
    quoted_schema = bootstrap.dialect.identifier_preparer.quote(schema)
    try:
        with bootstrap.begin() as connection:
            current_database, read_only = connection.execute(text(
                "SELECT current_database(), current_setting('transaction_read_only')",
            )).one()
            if str(current_database).lower() != target.database:
                raise AssertionError("connected database identity differs from the validated target")
            if read_only != "off":
                raise AssertionError("the disposable integration database is read-only")
            connection.execute(text(f"CREATE SCHEMA {quoted_schema}"))
        created = True

        # The generated schema name contains only a fixed prefix plus hex.  It is
        # safe to use in libpq's search_path option and cannot redirect the test.
        engine = create_engine(
            target.url,
            pool_pre_ping=True,
            pool_size=8,
            max_overflow=0,
            connect_args={
                "application_name": "fusion-budget-it",
                "connect_timeout": 5,
                "options": (
                    f"-c search_path={schema},public "
                    "-c statement_timeout=20000 -c lock_timeout=10000"
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
                raise AssertionError("refusing cleanup outside the integration schema prefix")
            with bootstrap.begin() as connection:
                connection.execute(text(f"DROP SCHEMA {quoted_schema} CASCADE"))
        bootstrap.dispose()


@pytest.fixture(scope="module")
def postgres_engine() -> Iterator[Engine]:
    with isolated_postgres_schema() as engine:
        yield engine


@dataclass(frozen=True)
class SeededGame:
    session_id: str
    user_id: int
    first_ai_id: int
    second_ai_id: int
    first_question_key: str
    second_question_key: str


def seed_fictional_game(session_factory: sessionmaker[Session]) -> SeededGame:
    database = session_factory()
    try:
        user = User(
            username=f"budget_test_{uuid4().hex[:10]}",
            email=f"budget-{uuid4().hex[:10]}@example.invalid",
            hashed_password="not-a-real-password-hash",
        )
        script = ScriptDBModel(
            title="虚构钟楼测试案",
            description="仅用于 PostgreSQL 并发预算测试",
            player_count=4,
            duration_minutes=30,
            status=ScriptStatus.PUBLISHED,
            is_public=True,
        )
        database.add_all([user, script])
        database.flush()
        roles = [
            CharacterDBModel(
                script_id=script.id,
                name=f"虚构角色{index}",
                profession="测试调查员",
                background=f"虚构背景{index}",
                secret=f"虚构秘密{index}",
                objective="验证预算锁",
                is_murderer=index == 0,
                is_victim=False,
            )
            for index in range(4)
        ]
        database.add_all(roles)
        database.add(LocationDBModel(script_id=script.id, name="虚构书房", description="测试地点"))
        database.add_all([
            EvidenceDBModel(
                script_id=script.id,
                name=f"虚构线索{index}",
                location="虚构书房",
                description=f"虚构证据{index}",
            )
            for index in range(4)
        ])
        database.add(BackgroundStoryDBModel(
            script_id=script.id,
            title="虚构事件",
            setting_description="测试世界",
            incident_description="没有真实商业正文",
            murder_method="虚构",
            murder_location="虚构书房",
        ))
        database.commit()

        service = FusionGameService(database)
        script_id = cast(int, script.id)
        user_id = cast(int, user.id)
        role_ids = [cast(int, role.id) for role in roles]
        session_id = service.create_session(script_id, user_id)["session"]["session_id"]
        service.select_character(session_id, user_id, role_ids[1], "pg-select-test-0001")
        game_session = service._owned_session(session_id, user_id)
        setattr(game_session, "current_phase", "INVESTIGATION")
        database.commit()

        first_key = "pg-question-test-0001"
        second_key = "pg-question-test-0002"
        service.perform_action(session_id, user_id, "ask_question", {
            "target_character_id": role_ids[0],
            "content": "请解释这条虚构线索。",
        }, first_key)
        service.perform_action(session_id, user_id, "ask_question", {
            "target_character_id": role_ids[2],
            "content": "请说明你的虚构判断。",
        }, second_key)
        return SeededGame(
            session_id=session_id,
            user_id=user_id,
            first_ai_id=role_ids[0],
            second_ai_id=role_ids[2],
            first_question_key=first_key,
            second_question_key=second_key,
        )
    finally:
        database.close()


class BlockingFakeRuntime:
    """A deterministic fake that proves how many calls passed reservation."""

    def __init__(self) -> None:
        self.settings = SimpleNamespace(provider="volcengine_ark", retries=0)
        self.client = object()
        self.model_started = threading.Event()
        self.release_model = threading.Event()
        self._counter_lock = threading.Lock()
        self.call_count = 0

    def model_metadata(self) -> dict[str, str | int]:
        return {
            "config_version": "postgres-budget-integration-v1",
            "provider": "volcengine_ark",
            "model": "fake-no-network-model",
            "max_attempts": 1,
        }

    def reservation_tokens(self, role_context: dict, visible_events: list[dict], question: str) -> UsageAmount:
        del role_context, visible_events, question
        return UsageAmount(prompt_tokens=10, completion_tokens=10)

    async def player_reply(self, role_context: dict, visible_events: list[dict], question: str) -> AgentTurn:
        del role_context, visible_events, question
        with self._counter_lock:
            self.call_count += 1
        self.model_started.set()
        deadline = time.monotonic() + 10
        while not self.release_model.is_set():
            if time.monotonic() >= deadline:
                raise AssertionError("fake model release timed out")
            await asyncio.sleep(0.01)
        return AgentTurn(
            "虚构模型回复",
            usage={"prompt_tokens": 10, "completion_tokens": 10},
            model_attempted=True,
            attempt_count=1,
            response_model="fake-no-network-model",
            finish_reason="stop",
        )


def wait_for_two_postgres_lock_waiters(
    monitor: Session,
    application_prefix: str,
    timeout_seconds: float = 5,
) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        waiting = monitor.execute(text(
            "SELECT count(*) FROM pg_stat_activity "
            "WHERE application_name LIKE :prefix AND wait_event_type = 'Lock'",
        ), {"prefix": f"{application_prefix}%"}).scalar_one()
        if waiting >= 2:
            return True
        time.sleep(0.02)
    return False


def run_reply_worker(
    session_factory: sessionmaker[Session],
    seeded: SeededGame,
    runtime: BlockingFakeRuntime,
    start_barrier: threading.Barrier,
    application_name: str,
    question_key: str,
    character_id: int,
) -> dict | None:
    database = session_factory()
    try:
        database.execute(
            text("SELECT set_config('application_name', :name, true)"),
            {"name": application_name},
        )
        start_barrier.wait(timeout=5)
        return asyncio.run(FusionGameService(database)._reply_to_action(
            seeded.session_id,
            seeded.user_id,
            question_key,
            character_id,
            "QUESTION_ASKED",
            cast(FusionAgentOrchestrator, runtime),
        ))
    finally:
        database.rollback()
        database.close()


@pytest.mark.integration
def test_two_connections_cannot_overbook_one_game_budget(postgres_engine: Engine) -> None:
    """Two real connections queue on one row; only one receives model budget."""
    session_factory = sessionmaker(bind=postgres_engine, expire_on_commit=False)
    seeded = seed_fictional_game(session_factory)
    runtime = BlockingFakeRuntime()
    application_prefix = f"fusion_budget_it_{uuid4().hex[:8]}"
    start_barrier = threading.Barrier(3)
    locker = session_factory()
    monitor = session_factory()
    futures: list[Future[dict | None]] = []
    results: list[dict | None] = []
    observed_real_lock_wait = False
    try:
        locker.execute(
            select(GameSession).where(GameSession.session_id == seeded.session_id).with_for_update(),
        ).scalar_one()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="fusion-pg-budget") as executor:
            try:
                futures = [
                    executor.submit(
                        run_reply_worker,
                        session_factory,
                        seeded,
                        runtime,
                        start_barrier,
                        f"{application_prefix}_w1",
                        seeded.first_question_key,
                        seeded.first_ai_id,
                    ),
                    executor.submit(
                        run_reply_worker,
                        session_factory,
                        seeded,
                        runtime,
                        start_barrier,
                        f"{application_prefix}_w2",
                        seeded.second_question_key,
                        seeded.second_ai_id,
                    ),
                ]
                start_barrier.wait(timeout=5)
                observed_real_lock_wait = wait_for_two_postgres_lock_waiters(
                    monitor,
                    application_prefix,
                )
                locker.commit()
                if not observed_real_lock_wait:
                    pytest.fail("both worker connections did not reach a PostgreSQL row-lock wait")

                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    if runtime.call_count > 1:
                        break
                    if runtime.model_started.is_set() and sum(future.done() for future in futures) == 1:
                        break
                    time.sleep(0.02)
                assert runtime.model_started.is_set(), "no reservation reached the fake model"
                assert runtime.call_count == 1, "concurrent connections overbooked the game budget"
                assert sum(future.done() for future in futures) == 1
                runtime.release_model.set()
                results = [future.result(timeout=10) for future in futures]
            finally:
                # Never leave fake calls or row-lock waiters blocked while the
                # executor waits for its threads during an assertion failure.
                runtime.release_model.set()
                if locker.in_transaction():
                    locker.rollback()
    finally:
        runtime.release_model.set()
        if locker.in_transaction():
            locker.rollback()
        locker.close()
        monitor.close()

    assert observed_real_lock_wait is True
    assert all(result is not None for result in results)
    assert sorted(result["payload"]["degraded"] for result in results if result is not None) == [False, True]

    verification = session_factory()
    try:
        game_session = verification.execute(
            select(GameSession).where(GameSession.session_id == seeded.session_id),
        ).scalar_one()
        receipts = verification.query(GameEventDBModel).filter_by(
            session_id=seeded.session_id,
            event_type="AI_TURN_RECEIPT",
        ).all()
        generations: list[dict[str, Any]] = [
            cast(dict[str, Any], receipt.event_metadata)["generations"][0]
            for receipt in receipts
        ]
        assert len(generations) == 2
        assert sorted(generation.get("budget_reason") or "ALLOWED" for generation in generations) == [
            "ALLOWED",
            "TOKEN_BUDGET",
        ]
        assert sorted(generation["status"] for generation in generations) == [
            "BUDGET_BLOCKED",
            "COMPLETED",
        ]
        assert cast(int, game_session.prompt_tokens) == 10
        assert cast(int, game_session.completion_tokens) == 10
    finally:
        verification.close()
