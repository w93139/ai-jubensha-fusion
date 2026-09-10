"""Real PostgreSQL locks/migrations; fictional packages and a non-network SDK.

Run only through scripts/test_package_play_postgres.py. SyntheticPublisher
models the production candidate fence and a changing source/current gate; it
does not claim to test human approval semantics or commercial source quality.
"""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal
import importlib.util
import json
from pathlib import Path
import threading
import time
from uuid import uuid4

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from scripts.test_package_play_postgres import isolated_schema, validate_environment
from src.db.base import SQLAlchemyBase
from src.db.models import User
from src.db.models.package_play import ScriptPackagePlay, ScriptPackagePlayEvent
from src.db.models.script_package import ScriptPackageVersion
from src.db.models.script_publication import ScriptPackageRelease, ScriptPublicationApproval
from src.fusion.agents import PlayerModelSettings
from src.fusion.budget import BudgetPolicy
from src.fusion.package_import import PackageImportService
from src.fusion.package_play import PackagePlayError, PackagePlayService
from src.fusion.package_role_model import PackageRoleModel
from src.fusion.package_runtime import PackageRuntimeService
from src.fusion.package_validation import canonical_json, content_hash
from src.fusion.publication_lock import lock_candidate_version
from src.services.llm_service import LLMResponse
from tests.fusion_security.test_package_runtime import opening_package
from tests.fusion_security.test_script_packages import fictional_package_v11


MIGRATIONS = (
    "j0d1e2f3a4b5_add_script_package_intake", "k1d2e3f4a5b6_add_script_review_records",
    "l2e3f4a5b6c7_add_authoring_jobs", "m3f4a5b6c7d8_add_publication_and_package_binding",
    "n4a5b6c7d8e9_add_package_flow", "o5b6c7d8e9f0_add_package_play",
)
MIGRATED_TABLES = {
    "script_package_versions", "script_import_jobs", "script_audit_records", "script_finding_dispositions",
    "authoring_jobs", "authoring_attempts", "script_publication_approvals", "script_package_releases",
    "script_package_play_sessions", "script_package_flows", "script_package_flow_actions",
    "script_package_plays", "script_package_play_events",
}
MODEL = "doubao-seed-character-260628"


def migration_modules():
    directory = Path(__file__).resolve().parents[2] / "src/db/migrations/versions"
    previous = "i9c0d1e2f3a4"
    result = []
    for name in MIGRATIONS:
        spec = importlib.util.spec_from_file_location(name, directory / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.down_revision == previous
        previous = module.revision
        result.append(module)
    return result


def migrate(sandbox, connection, *, downgrade=False):
    sandbox.assert_connection(connection)
    modules = migration_modules()
    with Operations.context(MigrationContext.configure(connection)):
        for module in reversed(modules) if downgrade else modules:
            (module.downgrade if downgrade else module.upgrade)()


def prepare_schema(sandbox):
    with sandbox.engine.begin() as connection:
        sandbox.assert_connection(connection)
        # Only users is a pre-j0 dependency. Do not create current package
        # metadata: j0 through o5 must really build those tables themselves.
        User.__table__.create(connection)
        migrate(sandbox, connection)
        connection.execute(text("CREATE TABLE package_play_it_gate (version_id INTEGER PRIMARY KEY "
                                "REFERENCES script_package_versions(id), current BOOLEAN NOT NULL)"))
    factory = sessionmaker(sandbox.engine, autoflush=False)
    with factory.begin() as db:
        db.add(User(id=1, username="synthetic-pg-player", email="synthetic-pg@example.invalid",
                    hashed_password="NOT_A_LOGIN", is_active=True, is_admin=False))
    return factory


@pytest.fixture(scope="module")
def pg_factory():
    validate_environment()  # Also guard an accidental alternative entry point.
    with isolated_schema() as sandbox:
        yield prepare_schema(sandbox)


class SyntheticPublisher:
    def __init__(self, db, record, source_hook=None):
        self.db, self.record, self.source_hook = db, record, source_hook

    def list_releases(self):
        return [self.get_release(self.record["id"])]

    def get_release(self, release_id, *, require_current=True):
        if release_id != self.record["id"]:
            raise ValueError("SYNTHETIC_RELEASE_UNAVAILABLE")
        if require_current:
            current = self.db.execute(text("SELECT current FROM package_play_it_gate WHERE version_id=:id"),
                                      {"id": self.record["version_id"]}).scalar_one()
            if not current:
                raise ValueError("SYNTHETIC_SOURCE_CHANGED")
            if self.source_hook:
                self.source_hook()  # Simulates source I/O before any row fence.
            lock_candidate_version(self.db, self.record["version_id"])
            refreshed = self.db.execute(text("SELECT current FROM package_play_it_gate WHERE version_id=:id"),
                                        {"id": self.record["version_id"]}).scalar_one()
            if not refreshed:
                raise ValueError("SYNTHETIC_SOURCE_CHANGED")
        return deepcopy(self.record)


class FakeSDK:
    def __init__(self):
        self.started, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.guard = threading.Lock()
        self.local = threading.local()
        self.calls = 0
        self.transaction_flags = []

    async def chat_completion(self, messages, **params):
        del messages, params
        with self.guard:
            self.calls += 1
            self.transaction_flags.append(self.local.db.in_transaction())
        self.started.set()
        deadline = time.monotonic() + 12
        while not self.release.is_set():
            if time.monotonic() >= deadline:
                raise AssertionError("synthetic SDK release timed out")
            await asyncio.sleep(0.01)
        return LLMResponse(content='{"refs":[{"collection":"knowledge","id":"memory-b"}]}',
                           usage={"prompt_tokens": 100, "completion_tokens": 10}, model=MODEL, finish_reason="stop")


@dataclass
class PlayCase:
    factory: object
    release: dict
    opening: dict
    initial: dict
    sdk: FakeSDK
    clock: list
    policy: BudgetPolicy

    def service(self, db, source_hook=None):
        self.sdk.local.db = db
        settings = PlayerModelSettings(provider="volcengine_ark", model=MODEL, timeout_seconds=10,
            retries=0, max_output_tokens=64, max_input_bytes=24000, thinking_mode="disabled",
            temperature=0, paid_calls_enabled=True)
        model = PackageRoleModel(self.sdk, settings)
        assert model.available
        return PackagePlayService(db, SyntheticPublisher(db, self.release, source_hook), model,
                                  self.policy, lambda: self.clock[0])


def make_case(pg_factory, package=None):
    token = uuid4().hex
    package = deepcopy(package) if package is not None else opening_package()
    package["content_version"] = "pg-" + token
    with pg_factory.begin() as db:
        imported = PackageImportService(db).submit(package, submitted_by=1, idempotency_key="import-" + token)
        common = {"version_id": imported["version_id"], "submitted_by": 1, "input_hash": "1" * 64,
                  "package_hash": content_hash(package), "bundle_hash": "2" * 64,
                  "basis_hash": "3" * 64, "source_report_hash": "4" * 64}
        # Synthetic FK records only. The publisher seam, not these dummy
        # receipts, supplies authorization in this isolated concurrency test.
        approval = ScriptPublicationApproval(**common, idempotency_key="approval-" + token,
                    approval_hash="5" * 64, approval_json='{"synthetic_test_reference":true}')
        db.add(approval)
        db.flush()
        release_row = ScriptPackageRelease(**common, approval_id=approval.id, idempotency_key="release-" + token,
                    release_hash="6" * 64, release_json='{"synthetic_test_reference":true}')
        db.add(release_row)
        db.flush()
        release = {"id": release_row.id, "version_id": imported["version_id"],
                   "package_hash": content_hash(package), "release_hash": "6" * 64}
        db.execute(text("INSERT INTO package_play_it_gate (version_id,current) VALUES (:id,true)"),
                   {"id": imported["version_id"]})
        opening = PackageRuntimeService(db, SyntheticPublisher(db, release)).create(
            {"release_id": release_row.id, "character_id": "a", "idempotency_key": "opening-" + token}, 1)
    policy = BudgetPolicy(1_000_000, Decimal("10"), Decimal("1"), Decimal("0.1"), Decimal("2"), True, "synthetic-pg/1")
    result = PlayCase(pg_factory, release, opening, {}, FakeSDK(), [1000], policy)
    with pg_factory.begin() as db:
        result.initial = result.service(db).create({"opening_session_id": opening["session_id"],
                                                  "idempotency_key": "play-" + token}, 1)
    return result


@pytest.fixture
def case(pg_factory):
    result = make_case(pg_factory)
    try:
        yield result
    finally:
        result.sdk.release.set()


@pytest.fixture
def investigation_case(pg_factory):
    package = opening_package(factory=fictional_package_v11)
    package["schema_version"] = "script-package/1.2"
    sources = deepcopy(package["introduction"]["sources"])
    package["mechanics"] = {
        "phase_budgets": [
            {"phase_id": "opening", "points": 1, "advance_policy": "REQUIRE_EXHAUSTED",
             "origin": "SOURCE_EXPLICIT", "sources": deepcopy(sources)},
            {"phase_id": "ending", "points": 0, "advance_policy": "ALLOW_REMAINING",
             "origin": "SOURCE_EXPLICIT", "sources": deepcopy(sources)},
        ],
        "actions": [
            {"id": name, "label": "虚构调查 " + name, "cost": 1, "phase_ids": ["opening"],
             "allowed_character_ids": ["a"], "origin": "SOURCE_EXPLICIT", "sources": deepcopy(sources)}
            for name in ("left", "right")
        ],
    }
    for name in ("left", "right"):
        package["evidence"].append({
            "id": "reward-" + name, "text": "SYNTHETIC_PG_REWARD_" + name,
            "visibility": "PUBLIC", "character_id": None, "disclosure": "PUBLIC",
            "release": {"phase_id": "opening", "required_action_ids": [name]}, "sources": deepcopy(sources),
        })
    result = make_case(pg_factory, package)
    try:
        yield result
    finally:
        result.sdk.release.set()


def action(key="advance", revision=0):
    return {"action": "ADVANCE_PHASE", "expected_revision": revision, "idempotency_key": key}


def question(key="question", revision=0):
    return {"character_id": "b", "question": "请说明虚构记录。", "expected_revision": revision, "idempotency_key": key}


def worker(case, body, *, ask=False, barrier=None, application=None, source_hook=None):
    with case.factory() as db:
        if application:
            db.execute(text("SELECT set_config('application_name', :name, false)"), {"name": application})
        pid = db.execute(text("SELECT pg_backend_pid()")).scalar_one()
        if barrier:
            barrier.wait(timeout=5)
        try:
            service = case.service(db, source_hook)
            result = asyncio.run(service.ask(case.initial["play_id"], body, 1)) if ask else service.act(case.initial["play_id"], body, 1)
            db.commit()
            return {"pid": pid, "view": result}
        except PackagePlayError as exc:
            db.rollback()
            return {"pid": pid, "error": exc.code}


def wait_until(predicate, message, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError(message)


def waiting_count(db, prefix):
    db.execute(text("SELECT pg_stat_clear_snapshot()"))
    return db.execute(text("SELECT count(*) FROM pg_stat_activity WHERE application_name LIKE :prefix "
                           "AND wait_event_type='Lock'"), {"prefix": prefix + "%"}).scalar_one()


def read_events(case, db):
    return db.query(ScriptPackagePlayEvent).filter_by(play_id=case.initial["play_id"]).order_by(
        ScriptPackagePlayEvent.revision).all()


def test_package_play_postgres_j0_through_o5_real_migration_roundtrip():
    with isolated_schema() as sandbox:
        with sandbox.engine.begin() as connection:
            User.__table__.create(connection)
            migrate(sandbox, connection)
            assert MIGRATED_TABLES <= set(inspect(connection).get_table_names())
            context = MigrationContext.configure(connection, opts={
                "include_object": lambda obj, name, kind, reflected, compare: kind != "table" or name in MIGRATED_TABLES})
            assert compare_metadata(context, SQLAlchemyBase.metadata) == []
            checks = {item["name"] for item in inspect(connection).get_check_constraints("script_package_play_events")}
            assert checks == {"ck_package_play_event_revision_positive", "ck_package_play_event_kind"}
            migrate(sandbox, connection, downgrade=True)
            assert set(inspect(connection).get_table_names()) == {"users"}
            migrate(sandbox, connection)
            assert MIGRATED_TABLES <= set(inspect(connection).get_table_names())


@pytest.mark.parametrize("same_key", [False, True])
def test_package_play_postgres_two_connections_same_revision_cannot_advance_twice(case, same_key):
    barrier = threading.Barrier(3)
    prefix = "play-act-" + uuid4().hex[:12]
    with case.factory() as locker, case.factory() as monitor, ThreadPoolExecutor(max_workers=2) as executor:
        locker.execute(select(ScriptPackagePlay.id).where(ScriptPackagePlay.play_id == case.initial["play_id"]).with_for_update()).scalar_one()
        futures = [executor.submit(worker, case, action("first" if same_key else f"action-{i}"),
                                   barrier=barrier, application=prefix + str(i)) for i in range(2)]
        try:
            barrier.wait(timeout=5)
            wait_until(lambda: waiting_count(monitor, prefix) == 2, "both independent connections must wait on real row fences")
            locker.commit()
            results = [future.result(timeout=8) for future in futures]
        finally:
            locker.rollback()
    assert len({result["pid"] for result in results}) == 2
    if same_key:
        assert [result["view"]["revision"] for result in results] == [1, 1]
    else:
        assert sum("view" in result for result in results) == 1
        assert next(result["error"] for result in results if "error" in result) == "PACKAGE_PLAY_REVISION_CONFLICT"
    with case.factory() as db:
        assert len(read_events(case, db)) == 1
        assert case.service(db).get(case.initial["play_id"], 1)["revision"] == 1


@pytest.mark.parametrize("same_key", [False, True])
def test_package_play_postgres_investigation_last_point_is_atomic_and_idempotent(investigation_case, same_key):
    case = investigation_case
    assert case.initial["mechanics"]["remaining_points"] == 1
    barrier = threading.Barrier(3)
    prefix = "play-search-" + uuid4().hex[:12]
    bodies = [{"action": "PERFORM_ACTION", "target": {"action_id": "left" if same_key or index == 0 else "right"},
               "expected_revision": 0, "idempotency_key": "same-search" if same_key else "search-" + str(index)}
              for index in range(2)]
    with case.factory() as locker, case.factory() as monitor, ThreadPoolExecutor(max_workers=2) as executor:
        locker.execute(select(ScriptPackagePlay.id).where(
            ScriptPackagePlay.play_id == case.initial["play_id"]).with_for_update()).scalar_one()
        futures = [executor.submit(worker, case, body, barrier=barrier, application=prefix + str(index))
                   for index, body in enumerate(bodies)]
        try:
            barrier.wait(timeout=5)
            wait_until(lambda: waiting_count(monitor, prefix) == 2,
                       "both v1.2 requests must wait on independent PostgreSQL connections")
            locker.commit()
            results = [future.result(timeout=8) for future in futures]
        finally:
            locker.rollback()
    assert len({result["pid"] for result in results}) == 2
    if same_key:
        assert results[0]["view"] == results[1]["view"]
    else:
        assert sum("view" in result for result in results) == 1
        assert next(result["error"] for result in results if "error" in result) == "PACKAGE_PLAY_REVISION_CONFLICT"
    with case.factory() as db:
        saved = read_events(case, db)
        assert len(saved) == 1 and saved[0].kind == "ACTION"
        winner = json.loads(saved[0].request_json)["target"]["action_id"]
        view = case.service(db).get(case.initial["play_id"], 1)
        assert view["revision"] == 1 and view["mechanics"] == {
            "initial_points": 1, "remaining_points": 0, "spent_points": 1,
            "can_finish_phase": True, "available_actions": [],
        }
        rewards = {item["id"] for item in view["public_evidence"] if item["id"].startswith("reward-")}
        assert rewards == {"reward-" + winner}
        # The successful request may be replayed, but can never charge again.
        assert case.service(db).act(case.initial["play_id"], json.loads(saved[0].request_json), 1) == view
        assert len(read_events(case, db)) == 1 and case.sdk.calls == 0


def test_package_play_postgres_same_question_reserves_once_and_waits_without_locks(case):
    case.sdk.release.clear()
    barrier = threading.Barrier(3)
    prefix = "play-ask-" + uuid4().hex[:12]
    with case.factory() as locker, case.factory() as monitor, ThreadPoolExecutor(max_workers=2) as executor:
        locker.execute(select(ScriptPackagePlay.id).where(ScriptPackagePlay.play_id == case.initial["play_id"]).with_for_update()).scalar_one()
        futures = [executor.submit(worker, case, question(), ask=True, barrier=barrier, application=prefix + str(i)) for i in range(2)]
        try:
            barrier.wait(timeout=5)
            wait_until(lambda: waiting_count(monitor, prefix) == 2, "both question requests must reach PostgreSQL locks")
            locker.commit()
            wait_until(lambda: case.sdk.started.is_set() and sum(future.done() for future in futures) == 1,
                       "one repeated request should finish while one SDK call waits")
            assert case.sdk.calls == 1 and case.sdk.transaction_flags == [False]
            with case.factory.begin() as observer:
                rows = read_events(case, observer)
                assert [row.kind for row in rows] == ["AI_REQUEST"]
                # Independent NOWAIT writes prove neither authorization fence
                # remains held during the synthetic model await.
                observer.execute(select(ScriptPackageVersion.id).where(ScriptPackageVersion.id == case.release["version_id"]).with_for_update(nowait=True)).scalar_one()
                observer.execute(select(ScriptPackagePlay.id).where(ScriptPackagePlay.play_id == case.initial["play_id"]).with_for_update(nowait=True)).scalar_one()
            case.sdk.release.set()
            results = [future.result(timeout=8) for future in futures]
        finally:
            case.sdk.release.set()
            locker.rollback()
    assert len({result["pid"] for result in results}) == 2
    assert all("view" in result for result in results)
    with case.factory() as db:
        assert [row.kind for row in read_events(case, db)] == ["AI_REQUEST", "AI_RESULT"]
        result = asyncio.run(case.service(db).ask(case.initial["play_id"], question(), 1))
        assert result["revision"] == 2 and result["budget"]["used_tokens"] == 110
    assert case.sdk.calls == 1


@pytest.mark.parametrize("change", ["advance", "publication"])
def test_package_play_postgres_changes_during_await_only_account_stale_reply(case, change):
    case.sdk.release.clear()
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker, case, question(), ask=True)
        try:
            assert case.sdk.started.wait(timeout=5)
            with case.factory.begin() as db:
                assert [row.kind for row in read_events(case, db)] == ["AI_REQUEST"]
                if change == "advance":
                    case.service(db).act(case.initial["play_id"], action(revision=1), 1)
                else:
                    lock_candidate_version(db, case.release["version_id"])
                    db.execute(text("UPDATE package_play_it_gate SET current=false WHERE version_id=:id"), {"id": case.release["version_id"]})
            case.sdk.release.set()
            result = future.result(timeout=8)["view"]
        finally:
            case.sdk.release.set()
    assert result["last_ai_status"] == "STALE" and result["dialogue"] == []
    assert result["budget"]["used_tokens"] == 110 and result["budget"]["reserved_tokens"] == 0
    assert not any(item.get("shared_by_character_id") == "b" for item in result["public_knowledge"])
    with case.factory() as db:
        restored = asyncio.run(case.service(db).ask(case.initial["play_id"], question(), 1))
        assert restored == result
    assert case.sdk.calls == 1


def test_package_play_postgres_source_change_before_candidate_lock_is_rechecked(case):
    observed, resume = threading.Event(), threading.Event()
    def source_io():
        observed.set()
        assert resume.wait(timeout=5)
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(worker, case, action(), source_hook=source_io)
        try:
            assert observed.wait(timeout=5)
            with case.factory.begin() as db:
                lock_candidate_version(db, case.release["version_id"])
                db.execute(text("UPDATE package_play_it_gate SET current=false WHERE version_id=:id"), {"id": case.release["version_id"]})
            resume.set()
            result = future.result(timeout=8)
        finally:
            resume.set()
    assert result["error"] == "PACKAGE_PLAY_RELEASE_UNAVAILABLE"
    with case.factory() as db:
        assert read_events(case, db) == []
        assert case.service(db).get(case.initial["play_id"], 1)["revision"] == 0


def test_package_play_postgres_rollback_retry_and_real_constraints(case):
    class InjectedFailure(Exception):
        pass
    with pytest.raises(InjectedFailure), case.factory.begin() as db:
        case.service(db).act(case.initial["play_id"], action(), 1)
        raise InjectedFailure()
    with case.factory() as db:
        assert read_events(case, db) == []
        assert case.service(db).get(case.initial["play_id"], 1)["revision"] == 0
        restored = case.service(db).act(case.initial["play_id"], action(), 1)
        db.commit()
        assert restored["revision"] == 1
        row = read_events(case, db)[0]
        values = {column.name: getattr(row, column.name) for column in row.__table__.columns
                  if column.name not in {"id", "created_at", "updated_at"}}
        for patch in ({"idempotency_key": "different"}, {"revision": 2},
                      {"revision": 2, "idempotency_key": "different", "kind": "INVALID_KIND"},
                      {"revision": 0, "idempotency_key": "different"},
                      {"play_id": "play-" + "f" * 32, "idempotency_key": "different"}):
            with pytest.raises(IntegrityError), db.begin_nested():
                db.execute(ScriptPackagePlayEvent.__table__.insert().values(values | patch))
        assert len(read_events(case, db)) == 1


def test_package_play_postgres_committed_reservation_survives_crash_and_expiry(case):
    with case.factory() as db:
        repeated, prepared = case.service(db)._begin(case.initial["play_id"], question(), 1)
        assert repeated is None and prepared and not db.in_transaction()
        db.rollback()  # Simulated process loss after commit, before dispatch.
    with case.factory() as db:
        pending = asyncio.run(case.service(db).ask(case.initial["play_id"], question(), 1))
        assert pending["pending_ai"] and pending["budget"]["reserved_tokens"] > 0
    case.clock[0] += 301
    with case.factory() as db:
        expired = asyncio.run(case.service(db).ask(case.initial["play_id"], question(), 1))
        assert expired["last_ai_status"] == "EXPIRED"
        assert expired["budget"]["reserved_tokens"] == 0
        assert Decimal(expired["budget"]["used_cost_cny"]) == Decimal(pending["budget"]["reserved_cost_cny"])
        db.rollback()
    with case.factory() as db:
        assert case.service(db).get(case.initial["play_id"], 1) == expired
        assert [row.kind for row in read_events(case, db)] == ["AI_REQUEST", "AI_RESULT"]
    assert case.sdk.calls == 0
