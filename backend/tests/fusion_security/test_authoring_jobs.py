"""File-backed SQLite, independent connections, synthetic model receipts only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta
from decimal import Decimal
import importlib.util
import io
from pathlib import Path
from threading import Barrier
from types import SimpleNamespace

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.orm import sessionmaker

from src.db.base import SQLAlchemyBase
from src.db.models import ScriptImportJob, ScriptPackageVersion, User
from src.db.models.authoring_job import AuthoringAttempt, AuthoringJob
from src.fusion import authoring_jobs
from src.fusion.authoring_jobs import AuthoringJobError, AuthoringJobStore
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError
from src.fusion.package_validation import canonical_json, content_hash
from src.services.llm_service import LLMResponse
from tests.fusion_security.test_authoring_model import draft_for, fixture_config, fixture_report, good_response, stub_sdk, synthetic_context_and_package


def prepared_metadata(model, step, context, package=None):
    prepared = model.prepare(step, context, package)
    return {"step": step, "prompt_hash": prepared.prompt_hash, "contract_hash": prepared.contract_hash,
            "input_tokens": prepared.input_tokens, "max_completion_tokens": prepared.max_completion_tokens,
            "reservation": prepared.reservation.to_metadata(), "request_contract": prepared.request_contract}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'authoring.sqlite3'}", connect_args={"check_same_thread": False, "timeout": 1})

    @event.listens_for(engine, "connect")
    def explicit_transactions(connection, _record):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    SQLAlchemyBase.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions.begin() as db:
        db.add_all([User(id=index, username=f"authoring-{index}", email=f"authoring-{index}@example.invalid",
                         hashed_password="synthetic", is_admin=True) for index in (1, 2)])
    context, package = synthetic_context_and_package()
    request = {key: context[key] for key in ("bundle_hash", "source_ids", "title", "content_version", "player_count")}
    request["idempotency_key"] = "authoring-fixture"
    model = AuthoringModel(fixture_config())
    clock = SimpleNamespace(now=datetime(2026, 9, 5, 1, 0))
    monkeypatch.setattr(authoring_jobs, "_now", lambda: clock.now)
    value = SimpleNamespace(store=AuthoringJobStore(sessions), sessions=sessions, engine=engine, context=context,
                            package=package, request=request, model=model, clock=clock)
    value.job = value.store.create(request, context, model.snapshot(), 1)
    value.prepared = prepared_metadata(model, "COMPILE", context)
    yield value
    engine.dispose()


def receipt_for(setup, prepared=None, *, known=True, code="PASSED", tokens=(100, 20), snapshot=None):
    prepared = prepared or setup.prepared
    snapshot = snapshot or setup.model.snapshot()
    receipt = {key: snapshot[key] for key in ("schema_version", "provider", "model", "pricing_version", "request_contract")}
    receipt.update({key: prepared[key] for key in ("step", "prompt_hash", "contract_hash", "reservation")})
    amount = setup.model.config.pricing.amount(*tokens).to_metadata() if known else None
    cost = amount["cost_cny"] if known else prepared["reservation"]["cost_cny"]
    return receipt | {"usage_known": known, "usage": amount, "charged_cost_cny": cost, "estimated_cost_cny": cost,
                      "latency_ms": 15, "result_code": code}


def dispatched(setup):
    token = setup.store.claim(setup.job["id"])
    attempt = setup.store.reserve(setup.job["id"], token, "COMPILE", setup.prepared)
    setup.store.dispatch(setup.job["id"], token, attempt["id"])
    return token, attempt


def compiled(setup):
    token, attempt = dispatched(setup)
    output = {"status": "CANDIDATE", "package": setup.package, "blockers": []}
    setup.store.finish_attempt(attempt["id"], output, receipt_for(setup))
    return token, attempt


def count(setup, model):
    with setup.sessions() as db:
        return db.query(model).count()


def legacy_metadata(setup):
    """Synthetic archived hashes, independent of the currently shipped prompts."""
    snapshot = setup.model.snapshot() | {"schema_version": "authoring-model/1.0", "request_contract": "bailian-authoring-json/1.0"}
    for hashes in ("prompt_hashes", "schema_hashes"):
        snapshot[hashes] = {step: content_hash({"fixture": "archived-v1", "kind": hashes, "step": step})
                            for step in ("COMPILE", "AUDIT")}
    prepared = {}
    for step in ("COMPILE", "AUDIT"):
        item = prepared_metadata(setup.model, step, setup.context, setup.package if step == "AUDIT" else None)
        item["prompt_hash"] = snapshot["prompt_hashes"][step]
        item["request_contract"]["version"] = snapshot["request_contract"]
        item["request_contract"]["schema_hash"] = snapshot["schema_hashes"][step]
        item["contract_hash"] = content_hash({"request": item["request_contract"], "configuration": snapshot})
        prepared[step] = item
    return snapshot, prepared


@pytest.mark.parametrize("model_version,request_version", [
    ("authoring-model/1.0", "bailian-authoring-json/1.1"),
    ("authoring-model/1.1", "bailian-authoring-json/1.0"),
    ("authoring-model/2.0", "bailian-authoring-json/2.0"),
])
def test_authoring_jobs_contract_versions_must_be_a_supported_pair(setup, model_version, request_version):
    snapshot = setup.model.snapshot() | {"schema_version": model_version, "request_contract": request_version}
    with pytest.raises(AuthoringJobError, match="^AUTHORING_INPUT_INVALID$"):
        setup.store.create(setup.request | {"idempotency_key": "invalid-pair"}, setup.context, snapshot, 1)
    assert count(setup, AuthoringJob) == 1


def test_authoring_jobs_archived_v1_completed_results_keep_hashes_and_billing(setup):
    snapshot, metadata = legacy_metadata(setup)
    request = setup.request | {"idempotency_key": "archived-completed"}
    job = setup.store.create(request, setup.context, snapshot, 1)
    token = setup.store.claim(job["id"])
    compile_output = {"status": "CANDIDATE", "package": setup.package, "blockers": []}
    expected_receipts = []
    for step, output in [("COMPILE", compile_output), ("AUDIT", fixture_report(setup.package))]:
        prepared = metadata[step]
        attempt = setup.store.reserve(job["id"], token, step, prepared)
        setup.store.dispatch(job["id"], token, attempt["id"])
        receipt = receipt_for(setup, prepared, snapshot=snapshot)
        assert "output_diagnostics" not in receipt
        expected_receipts.append(receipt)
        setup.store.finish_attempt(attempt["id"], output, receipt)
        if step == "COMPILE":
            setup.store.materialize(job["id"], token, setup.package, "2" * 64)
    completed = setup.store.checkpoint(job["id"], token, step="DONE", state="COMPLETED")
    with setup.sessions() as db:
        row = db.get(AuthoringJob, job["id"])
        persisted_before = (row.request_json, row.request_hash, row.context_json, row.context_hash,
                            row.model_snapshot_json, row.model_snapshot_hash, row.input_hash)
        assert row.model_snapshot_json == canonical_json(snapshot)
        assert row.input_hash == content_hash({"request": request, "context": setup.context,
                                                "model_snapshot": snapshot, "submitted_by": 1})
        attempts = db.query(AuthoringAttempt).filter_by(job_id=job["id"]).order_by(AuthoringAttempt.id).all()
        for attempt, receipt in zip(attempts, expected_receipts):
            assert attempt.prepared_hash == content_hash(metadata[attempt.step])
            assert attempt.receipt_json == canonical_json(receipt) and attempt.receipt_hash == content_hash(receipt)
    restarted = AuthoringJobStore(setup.sessions)
    assert restarted.get(job["id"]) == completed
    assert restarted.existing_request(request, 1) == completed
    assert completed["model_snapshot"]["schema_version"] == "authoring-model/1.0"
    assert Decimal(completed["charged_cost_cny"]) == sum(Decimal(item["charged_cost_cny"]) for item in expected_receipts)
    with setup.sessions() as db:
        row = db.get(AuthoringJob, job["id"])
        assert persisted_before == (row.request_json, row.request_hash, row.context_json, row.context_hash,
                                     row.model_snapshot_json, row.model_snapshot_hash, row.input_hash)


def test_authoring_jobs_new_prepared_and_receipt_cannot_replace_archived_dispatch(setup):
    snapshot, metadata = legacy_metadata(setup)
    job = setup.store.create(setup.request | {"idempotency_key": "archived-dispatch"}, setup.context, snapshot, 1)
    token = setup.store.claim(job["id"])
    attempt = setup.store.reserve(job["id"], token, "COMPILE", metadata["COMPILE"])
    with pytest.raises(AuthoringJobError, match="^AUTHORING_PREPARATION_INVALID$"):
        setup.store.reserve(job["id"], token, "COMPILE", setup.prepared)
    setup.store.dispatch(job["id"], token, attempt["id"])
    output = {"status": "CANDIDATE", "package": setup.package, "blockers": []}
    with pytest.raises(AuthoringJobError, match="^AUTHORING_RECEIPT_INVALID$"):
        setup.store.finish_attempt(attempt["id"], output, receipt_for(setup))
    code = "AUTHORING_TIMEOUT_USAGE_UNKNOWN"
    receipt = receipt_for(setup, metadata["COMPILE"], snapshot=snapshot, known=False, code=code)
    setup.store.finish_attempt(attempt["id"], None, receipt, code)
    setup.clock.now += timedelta(seconds=301)
    with pytest.raises(AuthoringJobError, match="^AUTHORING_USAGE_RECONCILIATION_REQUIRED$"):
        setup.store.claim(job["id"])
    restored = setup.store.get(job["id"])
    assert restored["state"] == "NEEDS_RECONCILIATION" and restored["attempts"][0]["receipt"] == receipt
    assert restored["charged_cost_cny"] == metadata["COMPILE"]["reservation"]["cost_cny"]
    with pytest.raises(AuthoringJobError, match="^AUTHORING_RECOVERY_UNSAFE$"):
        setup.store.recover(job["id"], restored["revision"])
    assert len(restored["attempts"]) == 1


def test_authoring_jobs_successful_two_call_workflow_survives_store_restart(setup):
    token, compile_attempt = compiled(setup)
    before = setup.store.get(setup.job["id"])
    assert before["step"] == "COMPILE" and before["state"] == "RUNNING"
    job = setup.store.materialize(setup.job["id"], token, setup.package, "2" * 64)
    assert job["step"] == "AUDIT" and job["candidate_version_id"] and job["source_report_hash"] == "2" * 64
    prepared = prepared_metadata(setup.model, "AUDIT", setup.context, setup.package)
    audit = setup.store.reserve(job["id"], token, "AUDIT", prepared)
    setup.store.dispatch(job["id"], token, audit["id"])
    output = fixture_report(setup.package)
    setup.store.finish_attempt(audit["id"], output, receipt_for(setup, prepared))
    result = setup.store.checkpoint(job["id"], token, step="DONE", state="COMPLETED")
    restarted = AuthoringJobStore(setup.sessions)
    assert restarted.get(job["id"]) == result == restarted.list()[0]
    assert result["state"] == "COMPLETED" and not result["publication_ready"]
    assert result["model_snapshot"]["schema_version"] == "authoring-model/1.1"
    assert result["model_snapshot"]["request_contract"] == "bailian-authoring-json/1.1"
    assert [item["status"] for item in result["attempts"]] == ["SUCCEEDED", "SUCCEEDED"]
    assert result["attempts"][1]["output_hash"] == content_hash(output)
    assert Decimal(result["charged_cost_cny"]) == Decimal(receipt_for(setup)["charged_cost_cny"]) * 2
    assert "SYNTHETIC_KEY_SENTINEL" not in str(result)
    assert "materials" not in result and "context" not in result
    assert count(setup, ScriptImportJob) == count(setup, ScriptPackageVersion) == 1


def test_authoring_jobs_actor_key_idempotency_freezes_first_context_and_model(setup):
    changed_context = deepcopy(setup.context)
    changed_context["notes"] = ["Later source note"]
    changed_model = setup.model.snapshot() | {"pricing_version": "different-price"}
    assert setup.store.create(setup.request, changed_context, changed_model, 1) == setup.job
    assert setup.store.inputs(setup.job["id"])["context"] == setup.context
    with pytest.raises(AuthoringJobError, match="^AUTHORING_IDEMPOTENCY_CONFLICT$"):
        setup.store.create(setup.request | {"title": "New title"}, setup.context | {"title": "New title"}, setup.model.snapshot(), 1)
    other = setup.store.create(setup.request, setup.context, setup.model.snapshot(), 2)
    assert other["id"] != setup.job["id"] and count(setup, AuthoringJob) == 2


def test_authoring_jobs_existing_request_retries_need_no_source_or_model_reload(setup):
    assert setup.store.existing_request(setup.request, 1) == setup.job
    assert setup.store.existing_request(setup.request | {"idempotency_key": "not-created"}, 1) is None
    assert setup.store.existing_request(setup.request, 2) is None
    with pytest.raises(AuthoringJobError, match="^AUTHORING_IDEMPOTENCY_CONFLICT$"):
        setup.store.existing_request(setup.request | {"content_version": "changed"}, 1)


@pytest.mark.parametrize("case", ["bad-actor", "binding", "secret-snapshot"])
def test_authoring_jobs_invalid_input_has_no_persistent_side_effects(setup, case):
    context, snapshot, actor = deepcopy(setup.context), setup.model.snapshot(), 1
    if case == "bad-actor":
        actor = True
    elif case == "binding":
        context["title"] = "PRIVATE_UNBOUND_TITLE"
    else:
        snapshot["api_key"] = "PRIVATE_KEY_SENTINEL"
    with pytest.raises(AuthoringJobError) as caught:
        setup.store.create(setup.request | {"idempotency_key": "invalid"}, context, snapshot, actor)
    assert "PRIVATE_" not in str(caught.value)
    assert count(setup, AuthoringJob) == 1


def test_authoring_jobs_pre_dispatch_crash_is_recoverable_without_second_reservation(setup):
    old = setup.store.claim(setup.job["id"])
    first = setup.store.reserve(setup.job["id"], old, "COMPILE", setup.prepared)
    setup.clock.now += timedelta(seconds=301)
    new = AuthoringJobStore(setup.sessions).claim(setup.job["id"])
    assert old != new
    same = setup.store.reserve(setup.job["id"], new, "COMPILE", setup.prepared)
    assert same == first and count(setup, AuthoringAttempt) == 1
    with pytest.raises(AuthoringJobError, match="^AUTHORING_LEASE_LOST$"):
        setup.store.dispatch(setup.job["id"], old, first["id"])
    assert setup.store.dispatch(setup.job["id"], new, first["id"])["status"] == "IN_FLIGHT"


def test_authoring_jobs_dispatched_crash_requires_reconciliation_and_late_success_can_resume(setup):
    token, attempt = dispatched(setup)
    setup.clock.now += timedelta(seconds=301)
    with pytest.raises(AuthoringJobError, match="^AUTHORING_USAGE_RECONCILIATION_REQUIRED$"):
        setup.store.claim(setup.job["id"])
    job = setup.store.get(setup.job["id"])
    assert job["state"] == "NEEDS_RECONCILIATION" and job["attempts"][0]["status"] == "IN_FLIGHT"
    with pytest.raises(AuthoringJobError, match="^AUTHORING_RECOVERY_UNSAFE$"):
        setup.store.recover(job["id"], job["revision"])
    setup.store.finish_attempt(attempt["id"], {"status": "CANDIDATE", "package": setup.package, "blockers": []}, receipt_for(setup))
    assert setup.store.get(job["id"])["state"] == "NEEDS_RECONCILIATION"
    assert setup.store.recover(job["id"], job["revision"])["state"] == "QUEUED"
    fresh = setup.store.claim(job["id"])
    with pytest.raises(AuthoringJobError, match="^AUTHORING_ATTEMPT_ALREADY_DISPATCHED$"):
        setup.store.dispatch(job["id"], fresh, attempt["id"])
    assert setup.store.materialize(job["id"], fresh, setup.package, "2" * 64)["step"] == "AUDIT"


def test_authoring_jobs_unknown_usage_keeps_full_reservation_and_never_retries(setup):
    token, attempt = dispatched(setup)
    code = "AUTHORING_TIMEOUT_USAGE_UNKNOWN"
    saved = setup.store.finish_attempt(attempt["id"], None, receipt_for(setup, known=False, code=code), code)
    assert saved["status"] == "UNKNOWN"
    job = setup.store.checkpoint(setup.job["id"], token, step="COMPILE", state="NEEDS_RECONCILIATION", error_code=code)
    assert job["charged_cost_cny"] == setup.prepared["reservation"]["cost_cny"]
    with pytest.raises(AuthoringJobError):
        setup.store.recover(job["id"], job["revision"])
    with pytest.raises(AuthoringJobError):
        setup.store.claim(job["id"])
    with pytest.raises(AuthoringJobError, match="^AUTHORING_RESULT_IMMUTABLE$"):
        setup.store.finish_attempt(attempt["id"], {"status": "CANDIDATE", "package": setup.package, "blockers": []}, receipt_for(setup))


def test_authoring_jobs_known_failure_keeps_actual_charge_without_automatic_retry(setup):
    token, attempt = dispatched(setup)
    code = "COMPILER_OUTPUT_INVALID"
    receipt = receipt_for(setup, code=code)
    failed = setup.store.finish_attempt(attempt["id"], None, receipt, code)
    assert failed["status"] == "FAILED"
    assert setup.store.reserve(setup.job["id"], token, "COMPILE", setup.prepared) == failed
    with pytest.raises(AuthoringJobError, match="^AUTHORING_ATTEMPT_ALREADY_DISPATCHED$"):
        setup.store.dispatch(setup.job["id"], token, attempt["id"])
    job = setup.store.checkpoint(setup.job["id"], token, step="COMPILE", state="BLOCKED", error_code=code)
    assert job["charged_cost_cny"] == receipt["charged_cost_cny"] and job["state"] == "BLOCKED"


def test_authoring_jobs_zero_usage_content_filter_keeps_adapter_known_failure(setup, monkeypatch):
    token, attempt = dispatched(setup)
    response = LLMResponse(content="", model=fixture_config().model, finish_reason="content_filter",
                           usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0})
    _factory, client = stub_sdk(monkeypatch, response)
    with pytest.raises(AuthoringModelError, match="^AUTHORING_FINISH_INVALID$") as caught:
        asyncio.run(setup.model.call("COMPILE", setup.context))
    error = caught.value
    assert error.receipt["usage_known"] is True and Decimal(error.receipt["charged_cost_cny"]) == 0
    saved = setup.store.finish_attempt(attempt["id"], None, error.receipt, error.code)
    assert saved["status"] == "FAILED" and saved["receipt"] == error.receipt
    job = setup.store.checkpoint(setup.job["id"], token, step="COMPILE", state="BLOCKED", error_code=error.code)
    assert Decimal(job["charged_cost_cny"]) == 0
    assert Decimal(saved["prepared"]["reservation"]["cost_cny"]) > 0
    assert AuthoringJobStore(setup.sessions).get(job["id"]) == job
    client.chat_completion.assert_awaited_once()


def test_authoring_jobs_safe_output_diagnostics_survive_adapter_and_persistence(setup, monkeypatch):
    _token, attempt = dispatched(setup)
    package = deepcopy(setup.package)
    package["knowledge"][0]["text"] = "PRIVATE_DIAGNOSTIC_BODY_SENTINEL"
    _factory, client = stub_sdk(monkeypatch, good_response(draft_for(package)))
    with pytest.raises(AuthoringModelError, match="^COMPILER_TEXT_NOT_EXTRACTIVE$") as caught:
        asyncio.run(setup.model.call("COMPILE", setup.context))
    error = caught.value
    expected = [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/knowledge/0/text"}]
    assert error.receipt["output_diagnostics"] == expected
    saved = setup.store.finish_attempt(attempt["id"], None, error.receipt, error.code)
    assert saved["status"] == "FAILED" and saved["receipt"]["output_diagnostics"] == expected
    assert "PRIVATE_DIAGNOSTIC_BODY_SENTINEL" not in str(saved)
    assert AuthoringJobStore(setup.sessions).get(setup.job["id"])["attempts"] == [saved]
    client.chat_completion.assert_awaited_once()


def test_authoring_jobs_package_rule_diagnostics_keep_known_fee_and_receipt_hash(setup, monkeypatch):
    token, attempt = dispatched(setup)
    package = deepcopy(setup.package)
    package["knowledge"][0]["release"]["phase_id"] = "PRIVATE_INVALID_PHASE_SENTINEL"
    _factory, client = stub_sdk(monkeypatch, good_response(draft_for(package)))
    with pytest.raises(AuthoringModelError, match="^COMPILER_PACKAGE_INVALID$") as caught:
        asyncio.run(setup.model.call("COMPILE", setup.context))
    error = caught.value
    expected = [
        {"code": "PACKAGE_PHASE_NOT_FOUND", "entity_path": "/knowledge/0/release/phase_id"},
        {"code": "PACKAGE_INITIAL_KNOWLEDGE_MISSING", "entity_path": "/characters/0"},
    ]
    assert error.receipt["output_diagnostics"] == expected and error.receipt["usage_known"] is True
    saved = setup.store.finish_attempt(attempt["id"], None, error.receipt, error.code)
    assert saved["status"] == "FAILED" and saved["output"] is None
    assert setup.store.finish_attempt(attempt["id"], None, error.receipt, error.code) == saved
    with pytest.raises(AuthoringJobError, match="^AUTHORING_ATTEMPT_ALREADY_DISPATCHED$"):
        setup.store.dispatch(setup.job["id"], token, attempt["id"])
    blocked = setup.store.checkpoint(setup.job["id"], token, step="COMPILE", state="BLOCKED", error_code=error.code)
    restored = AuthoringJobStore(setup.sessions).get(blocked["id"])
    assert restored == blocked and restored["attempts"][0]["receipt"] == error.receipt
    assert Decimal(restored["charged_cost_cny"]) == Decimal(error.receipt["charged_cost_cny"]) > 0
    with setup.sessions() as db:
        row = db.get(AuthoringAttempt, attempt["id"])
        assert row.receipt_hash == content_hash(error.receipt) and row.receipt_json == canonical_json(error.receipt)
        assert row.output_json is None and "PRIVATE_INVALID_PHASE_SENTINEL" not in row.receipt_json
    assert count(setup, AuthoringAttempt) == 1 and count(setup, ScriptPackageVersion) == 0
    client.chat_completion.assert_awaited_once()


def test_authoring_jobs_failed_audit_diagnostics_preserve_candidate_and_existing_charge(setup, monkeypatch):
    token, compiler_attempt = compiled(setup)
    materialized = setup.store.materialize(setup.job["id"], token, setup.package, "2" * 64)
    before = materialized["attempts"][0]
    with setup.sessions() as db:
        compiler_hash = db.get(AuthoringAttempt, compiler_attempt["id"]).receipt_hash
    prepared = prepared_metadata(setup.model, "AUDIT", setup.context, setup.package)
    attempt = setup.store.reserve(setup.job["id"], token, "AUDIT", prepared)
    setup.store.dispatch(setup.job["id"], token, attempt["id"])
    report = fixture_report(setup.package)
    report["summary"] = "PRIVATE_AUDIT_BODY_SENTINEL"
    report["findings"][0].pop("sources")
    _factory, client = stub_sdk(monkeypatch, good_response(report))
    with pytest.raises(AuthoringModelError, match="^AUDIT_OUTPUT_INVALID$") as caught:
        asyncio.run(setup.model.call("AUDIT", setup.context, setup.package))
    error = caught.value
    assert error.receipt["usage_known"] is True
    assert error.receipt["output_diagnostics"] == [{"code": "AUDIT_SCHEMA_INVALID", "entity_path": "/findings/0/sources"}]
    failed = setup.store.finish_attempt(attempt["id"], None, error.receipt, error.code)
    assert failed["status"] == "FAILED" and failed["output"] is None
    assert "PRIVATE_AUDIT_BODY_SENTINEL" not in str(failed)
    with pytest.raises(AuthoringJobError, match="^AUTHORING_ATTEMPT_ALREADY_DISPATCHED$"):
        setup.store.dispatch(setup.job["id"], token, attempt["id"])
    stopped = setup.store.checkpoint(setup.job["id"], token, step="AUDIT", state="BLOCKED", error_code=error.code)
    restored = AuthoringJobStore(setup.sessions).get(stopped["id"])
    assert restored == stopped and restored["attempts"] == [before, failed]
    assert restored["candidate_version_id"] == materialized["candidate_version_id"]
    assert Decimal(restored["charged_cost_cny"]) == (
        Decimal(before["receipt"]["charged_cost_cny"]) + Decimal(error.receipt["charged_cost_cny"]))
    with setup.sessions() as db:
        assert db.get(AuthoringAttempt, compiler_attempt["id"]).receipt_hash == compiler_hash
        row = db.get(AuthoringAttempt, attempt["id"])
        assert row.receipt_hash == content_hash(error.receipt) and row.output_json is None
        assert "PRIVATE_AUDIT_BODY_SENTINEL" not in row.receipt_json
    client.chat_completion.assert_awaited_once()


def test_authoring_jobs_package_rule_diagnostics_reject_private_fields_and_preserve_absent_legacy_field(setup, caplog):
    _token, attempt = dispatched(setup)
    code = "COMPILER_PACKAGE_INVALID"
    legacy = receipt_for(setup, code=code)
    for diagnostic in (
        {"code": "PRIVATE_UNKNOWN_RULE_SENTINEL", "entity_path": "/knowledge/0/release/phase_id"},
        {"code": "PACKAGE_PHASE_NOT_FOUND", "entity_path": "/knowledge/0/release/PRIVATE_PATH_SENTINEL"},
        {"code": "PACKAGE_PHASE_NOT_FOUND", "entity_path": "/knowledge/0/release/phase_id", "text": "PRIVATE_BODY_SENTINEL"},
    ):
        receipt = deepcopy(legacy) | {"output_diagnostics": [diagnostic]}
        with pytest.raises(AuthoringJobError, match="^AUTHORING_RECEIPT_INVALID$") as caught:
            setup.store.finish_attempt(attempt["id"], None, receipt, code)
        assert "SENTINEL" not in str(caught.value) + caplog.text
        current = setup.store.get(setup.job["id"])["attempts"][0]
        assert current["status"] == "IN_FLIGHT" and current["receipt"] is None and current["output"] is None
    saved = setup.store.finish_attempt(attempt["id"], None, legacy, code)
    assert saved["status"] == "FAILED" and saved["receipt"] == legacy and "output_diagnostics" not in legacy
    restored = AuthoringJobStore(setup.sessions).get(setup.job["id"])
    assert restored["attempts"] == [saved] and restored["charged_cost_cny"] == legacy["charged_cost_cny"]
    with setup.sessions() as db:
        assert db.get(AuthoringAttempt, attempt["id"]).receipt_hash == content_hash(legacy)


def test_authoring_jobs_diagnostic_extra_body_is_rejected_without_echo(setup, caplog):
    _token, attempt = dispatched(setup)
    code = "COMPILER_TEXT_NOT_EXTRACTIVE"
    receipt = receipt_for(setup, code=code)
    receipt["output_diagnostics"] = [{"code": "TEXT_NOT_IN_MATERIALS", "entity_path": "/knowledge/0/text",
                                      "text": "PRIVATE_DIAGNOSTIC_BODY_SENTINEL"}]
    with pytest.raises(AuthoringJobError, match="^AUTHORING_RECEIPT_INVALID$") as caught:
        setup.store.finish_attempt(attempt["id"], None, receipt, code)
    assert "PRIVATE_DIAGNOSTIC_BODY_SENTINEL" not in str(caught.value) + caplog.text
    current = setup.store.get(setup.job["id"])["attempts"][0]
    assert current["status"] == "IN_FLIGHT" and current["receipt"] is None


def test_authoring_jobs_over_reservation_failure_preserves_actual_billing(setup):
    token, attempt = dispatched(setup)
    code = "AUTHORING_USAGE_EXCEEDS_RESERVATION"
    receipt = receipt_for(setup, code=code, tokens=(1000000, 100000))
    assert Decimal(receipt["charged_cost_cny"]) > Decimal("0.10")
    result = setup.store.finish_attempt(attempt["id"], None, receipt, code)
    assert result["status"] == "FAILED"
    assert setup.store.get(setup.job["id"])["charged_cost_cny"] == receipt["charged_cost_cny"]
    prepared = prepared_metadata(setup.model, "AUDIT", setup.context, setup.package)
    with pytest.raises(AuthoringJobError):
        setup.store.reserve(setup.job["id"], token, "AUDIT", prepared)
    assert count(setup, AuthoringAttempt) == 1


def test_authoring_jobs_cancel_fences_late_results_and_materialization(setup):
    token, attempt = dispatched(setup)
    before = setup.store.get(setup.job["id"])
    cancelled = setup.store.cancel(before["id"], before["revision"])
    setup.store.finish_attempt(attempt["id"], {"status": "CANDIDATE", "package": setup.package, "blockers": []}, receipt_for(setup))
    late = setup.store.get(before["id"])
    assert late["state"] == "CANCELLED" and late["revision"] == cancelled["revision"]
    assert late["attempts"][0]["status"] == "SUCCEEDED"
    for action in (lambda: setup.store.checkpoint(before["id"], token, step="CANDIDATE"),
                   lambda: setup.store.materialize(before["id"], token, setup.package, "2" * 64),
                   lambda: setup.store.recover(before["id"], late["revision"])):
        with pytest.raises(AuthoringJobError):
            action()
    assert count(setup, ScriptPackageVersion) == 0


def test_authoring_jobs_cancelling_reserved_call_prevents_dispatch_and_preserves_reservation(setup):
    token = setup.store.claim(setup.job["id"])
    attempt = setup.store.reserve(setup.job["id"], token, "COMPILE", setup.prepared)
    current = setup.store.get(setup.job["id"])
    cancelled = setup.store.cancel(current["id"], current["revision"])
    with pytest.raises(AuthoringJobError, match="^AUTHORING_LEASE_LOST$"):
        setup.store.dispatch(current["id"], token, attempt["id"])
    assert cancelled["attempts"][0]["status"] == "RESERVED"
    assert Decimal(cancelled["charged_cost_cny"]) == Decimal(setup.prepared["reservation"]["cost_cny"])


def test_authoring_jobs_lease_expiry_and_stale_revision_are_enforced(setup):
    token = setup.store.claim(setup.job["id"])
    with pytest.raises(AuthoringJobError, match="^AUTHORING_LEASE_BUSY$"):
        setup.store.claim(setup.job["id"])
    with pytest.raises(AuthoringJobError, match="^AUTHORING_STATE_CONFLICT$"):
        setup.store.cancel(setup.job["id"], 0)
    current = setup.store.get(setup.job["id"])
    with pytest.raises(AuthoringJobError, match="^AUTHORING_RECOVERY_UNSAFE$"):
        setup.store.recover(current["id"], current["revision"])
    setup.clock.now += timedelta(seconds=300)
    with pytest.raises(AuthoringJobError, match="^AUTHORING_LEASE_LOST$"):
        setup.store.checkpoint(current["id"], token, step="CANDIDATE")
    assert setup.store.recover(current["id"], current["revision"])["state"] == "QUEUED"


def test_authoring_jobs_prepared_calls_are_bound_and_budgeted_before_dispatch(setup, monkeypatch):
    token = setup.store.claim(setup.job["id"])
    corrupt = deepcopy(setup.prepared)
    corrupt["reservation"]["cost_cny"] = "0.000001"
    with pytest.raises(AuthoringJobError, match="^AUTHORING_PREPARATION_INVALID$"):
        setup.store.reserve(setup.job["id"], token, "COMPILE", corrupt)
    monkeypatch.setattr(authoring_jobs, "MAX_JOB_COST_CNY", Decimal("0.00001"))
    with pytest.raises(AuthoringJobError, match="^AUTHORING_JOB_BUDGET_EXCEEDED$"):
        setup.store.reserve(setup.job["id"], token, "COMPILE", setup.prepared)
    assert count(setup, AuthoringAttempt) == 0


def test_authoring_jobs_existing_reservation_requires_exact_prepared_input(setup):
    token = setup.store.claim(setup.job["id"])
    setup.store.reserve(setup.job["id"], token, "COMPILE", setup.prepared)
    changed = deepcopy(setup.prepared)
    changed["input_tokens"] += 1
    changed["reservation"] = setup.model.config.pricing.amount(changed["input_tokens"], changed["max_completion_tokens"] + 16).to_metadata()
    with pytest.raises(AuthoringJobError, match="^AUTHORING_PREPARATION_CHANGED$"):
        setup.store.reserve(setup.job["id"], token, "COMPILE", changed)


@pytest.mark.parametrize("case", ["step", "prompt", "contract", "unknown-success", "negative", "nan", "free", "unpriced", "private-extra"])
def test_authoring_jobs_receipt_binding_and_cost_tampering_fail_closed(setup, case):
    token, attempt = dispatched(setup)
    receipt = receipt_for(setup)
    if case in {"step", "prompt", "contract"}:
        receipt[{"step": "step", "prompt": "prompt_hash", "contract": "contract_hash"}[case]] = "AUDIT" if case == "step" else "0" * 64
    elif case == "unknown-success":
        receipt = receipt_for(setup, known=False)
    elif case == "private-extra":
        receipt["api_key"] = "PRIVATE_RECEIPT_SENTINEL"
    else:
        receipt["charged_cost_cny"] = {"negative": "-1", "nan": "NaN", "free": "0", "unpriced": "0.000001"}[case]
        receipt["estimated_cost_cny"] = receipt["charged_cost_cny"]
    with pytest.raises(AuthoringJobError, match="^AUTHORING_RECEIPT_INVALID$"):
        setup.store.finish_attempt(attempt["id"], {"status": "CANDIDATE", "package": setup.package, "blockers": []}, receipt)
    assert setup.store.get(setup.job["id"])["attempts"][0]["status"] == "IN_FLIGHT"


def test_authoring_jobs_finish_is_idempotent_but_completed_payload_is_immutable(setup):
    token, attempt = dispatched(setup)
    output = {"status": "CANDIDATE", "package": setup.package, "blockers": []}
    receipt = receipt_for(setup)
    first = setup.store.finish_attempt(attempt["id"], output, receipt)
    assert "output_diagnostics" not in receipt and first["receipt"] == receipt
    with setup.sessions() as db:
        assert db.get(AuthoringAttempt, attempt["id"]).receipt_hash == content_hash(receipt)
    assert setup.store.finish_attempt(attempt["id"], output, receipt) == first
    with pytest.raises(AuthoringJobError, match="^AUTHORING_RESULT_IMMUTABLE$"):
        setup.store.finish_attempt(attempt["id"], output | {"extra": "PRIVATE_CHANGED_OUTPUT"}, receipt)
    assert setup.store.get(setup.job["id"])["state"] == "RUNNING"


def test_authoring_jobs_cannot_finish_before_dispatch_or_skip_to_audit(setup):
    token = setup.store.claim(setup.job["id"])
    attempt = setup.store.reserve(setup.job["id"], token, "COMPILE", setup.prepared)
    with pytest.raises(AuthoringJobError, match="^AUTHORING_ATTEMPT_NOT_DISPATCHED$"):
        setup.store.finish_attempt(attempt["id"], {}, receipt_for(setup))
    prepared = prepared_metadata(setup.model, "AUDIT", setup.context, setup.package)
    with pytest.raises(AuthoringJobError, match="^AUTHORING_STEP_INVALID$"):
        setup.store.reserve(setup.job["id"], token, "AUDIT", prepared)
    with pytest.raises(AuthoringJobError):
        setup.store.checkpoint(setup.job["id"], token, step="DONE", state="COMPLETED")


def test_authoring_jobs_materialization_is_idempotent_and_rejects_other_documents(setup):
    token, _attempt = compiled(setup)
    changed = deepcopy(setup.package) | {"title": "PRIVATE_OTHER_CANDIDATE"}
    with pytest.raises(AuthoringJobError, match="^AUTHORING_CANDIDATE_BINDING_INVALID$"):
        setup.store.materialize(setup.job["id"], token, changed, "2" * 64)
    first = setup.store.materialize(setup.job["id"], token, setup.package, "2" * 64)
    assert setup.store.materialize(setup.job["id"], token, setup.package, "2" * 64) == first
    with pytest.raises(AuthoringJobError, match="^AUTHORING_CANDIDATE_BINDING_INVALID$"):
        setup.store.materialize(setup.job["id"], token, setup.package, "3" * 64)
    assert count(setup, ScriptPackageVersion) == count(setup, ScriptImportJob) == 1


def test_authoring_jobs_materialization_insert_failure_rolls_back_fence_and_candidate(setup):
    token, _attempt = compiled(setup)
    before = setup.store.get(setup.job["id"])

    def fail(_mapper, _connection, _row):
        raise RuntimeError("Synthetic insert failure")

    event.listen(ScriptImportJob, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="Synthetic"):
            setup.store.materialize(setup.job["id"], token, setup.package, "2" * 64)
    finally:
        event.remove(ScriptImportJob, "before_insert", fail)
    assert setup.store.get(setup.job["id"]) == before
    assert count(setup, ScriptPackageVersion) == count(setup, ScriptImportJob) == 0


def race_after_job_read(setup, monkeypatch, functions):
    barrier = Barrier(2)
    read = setup.store._job

    def synchronized(*args):
        result = read(*args)
        barrier.wait(timeout=5)
        return result

    def safe_call(function):
        try:
            return function()
        except AuthoringJobError as error:
            return error.code

    with monkeypatch.context() as isolated:
        isolated.setattr(setup.store, "_job", synchronized)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(safe_call, function) for function in functions]
            return [future.result(timeout=8) for future in futures]


def test_authoring_jobs_independent_connection_claim_race_has_one_lease(setup, monkeypatch):
    results = race_after_job_read(setup, monkeypatch, [lambda: setup.store.claim(setup.job["id"])] * 2)
    winners = [value for value in results if len(value) == 32]
    assert len(winners) == 1 and results.count("AUTHORING_STATE_CONFLICT") == 1
    assert setup.store.get(setup.job["id"])["revision"] == 1


def test_authoring_jobs_independent_connection_dispatch_race_sends_at_most_once(setup, monkeypatch):
    token = setup.store.claim(setup.job["id"])
    attempt = setup.store.reserve(setup.job["id"], token, "COMPILE", setup.prepared)
    results = race_after_job_read(setup, monkeypatch, [lambda: setup.store.dispatch(setup.job["id"], token, attempt["id"])] * 2)
    assert sum(isinstance(value, dict) for value in results) == 1
    assert results.count("AUTHORING_STATE_CONFLICT") == 1
    assert setup.store.get(setup.job["id"])["attempts"][0]["status"] == "IN_FLIGHT"


def test_authoring_jobs_cancel_materialize_race_cannot_commit_candidate_after_cancel(setup, monkeypatch):
    token, _attempt = compiled(setup)
    before = setup.store.get(setup.job["id"])
    results = race_after_job_read(setup, monkeypatch, [
        lambda: setup.store.cancel(before["id"], before["revision"]),
        lambda: setup.store.materialize(before["id"], token, setup.package, "2" * 64)])
    assert sum(isinstance(value, dict) for value in results) == 1
    assert results.count("AUTHORING_STATE_CONFLICT") == 1
    current = setup.store.get(before["id"])
    assert count(setup, ScriptPackageVersion) == (0 if current["state"] == "CANCELLED" else 1)
    assert current["state"] == "CANCELLED" or current["step"] == "AUDIT"


@pytest.mark.parametrize("model,field", [(AuthoringJob, "request_json"), (AuthoringAttempt, "prepared_json")])
def test_authoring_jobs_orm_cannot_change_inputs_or_rearm_attempt(setup, model, field):
    dispatched(setup)
    with setup.sessions() as db:
        row = db.query(model).first()
        setattr(row, field, "{}")
        with pytest.raises(ValueError, match="不可修改"):
            db.flush()
        db.rollback()
        db.delete(row)
        with pytest.raises(ValueError, match="不可修改"):
            db.flush()
        db.rollback()


@pytest.mark.parametrize("model,field,value", [
    (AuthoringJob, "context_json", "{}"), (AuthoringJob, "request_hash", "0" * 64), (AuthoringJob, "state", "CANCELLED"),
    (AuthoringAttempt, "prepared_json", "{}"), (AuthoringAttempt, "output_json", "{}"),
    (AuthoringAttempt, "receipt_json", "{}"), (AuthoringAttempt, "status", "RESERVED"),
])
def test_authoring_jobs_raw_sql_tampering_is_detected_on_read(setup, model, field, value):
    compiled(setup)
    with setup.sessions.begin() as db:
        db.execute(update(model).values({field: value}))
    with pytest.raises(AuthoringJobError, match="INTEGRITY_FAILED"):
        setup.store.get(setup.job["id"])


def load_migration():
    path = Path(__file__).resolve().parents[2] / "src/db/migrations/versions/l2e3f4a5b6c7_add_authoring_jobs.py"
    spec = importlib.util.spec_from_file_location("authoring_migration_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_authoring_jobs_migration_matches_orm_and_downgrades_preserving_existing_tables():
    engine = create_engine("sqlite://")
    tables = {"authoring_jobs", "authoring_attempts"}
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE script_package_versions (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO script_package_versions VALUES (123)"))
        context = MigrationContext.configure(connection, opts={
            "include_object": lambda obj, name, kind, reflected, compare: kind != "table" or name in tables})
        with Operations.context(context):
            load_migration().upgrade()
            assert compare_metadata(context, SQLAlchemyBase.metadata) == []
            load_migration().downgrade()
        assert set(connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()) == {"users", "script_package_versions"}
        assert connection.execute(text("SELECT id FROM script_package_versions")).scalar() == 123
    engine.dispose()
    assert load_migration().down_revision == "k1d2e3f4a5b6"


def test_authoring_jobs_postgres_migration_compiles_offline():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        load_migration().upgrade()
        load_migration().downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE authoring_jobs" in sql and "CREATE TABLE authoring_attempts" in sql
    assert "uq_authoring_job_actor_key" in sql and "uq_authoring_attempt_job_step" in sql
    assert "REFERENCES users (id)" in sql and "REFERENCES script_package_versions (id)" in sql
    assert "DROP TABLE authoring_attempts" in sql and "DROP TABLE authoring_jobs" in sql
