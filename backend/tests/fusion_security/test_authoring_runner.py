"""Persistent workflow and HTTP tests with invented sources and stubbed SDK."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from src.api.routes import authoring_routes
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.db.models import User, ScriptAuditRecord, ScriptPackageVersion, ScriptImportJob, AuthoringJob, AuthoringAttempt
from src.fusion import authoring_jobs
from src.fusion.authoring_jobs import AuthoringJobStore, AuthoringJobError
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError
from src.fusion.authoring_runner import AuthoringRunner, submit_authoring_job
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.source_bundles import SourceBundleStore
from tests.fusion_security.test_authoring_model import (
    fixture_config, fixture_report, smoke_module, smoke_expected_package, stub_sdk, good_response, draft_for,
)


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    module = smoke_module()
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    sources, request = module.make_synthetic_bundle(root)
    context = prepare_authoring_sources(sources, request)
    package = smoke_expected_package(context, module)
    engine = create_engine("sqlite:///" + str(root / "jobs.sqlite"), connect_args={"autocommit": False})
    for model_type in (User, ScriptPackageVersion, ScriptImportJob, AuthoringJob, AuthoringAttempt, ScriptAuditRecord):
        model_type.__table__.create(engine)
    factory = sessionmaker(engine, autoflush=False)
    with factory.begin() as db:
        db.add(User(id=1, username="fixture", email="fixture@example.invalid", hashed_password="NOT_A_PASSWORD", is_admin=True))
    jobs = AuthoringJobStore(factory)
    model = AuthoringModel(fixture_config())
    _, sdk = stub_sdk(monkeypatch)
    sdk.chat_completion.side_effect = [good_response(draft_for(package)),
                                       good_response(fixture_report(package))]
    job = asyncio.run(submit_authoring_job(jobs, sources, model, request, 1))
    yield SimpleNamespace(jobs=jobs, sources=sources, model=model, request=request, context=context,
                          package=package, sdk=sdk, job=job, factory=factory, root=root)
    engine.dispose()


def run(workflow):
    return asyncio.run(AuthoringRunner(workflow.jobs, workflow.sources, workflow.model).run(workflow.job["id"], allow_paid=True))


def test_authoring_queue_no_network_and_complete_real_adapter_chain(workflow):
    assert workflow.job["state"] == "QUEUED" and workflow.sdk.chat_completion.await_count == 0
    result = run(workflow)
    assert result["state"] == "COMPLETED", result["error_code"]
    assert result["step"] == "DONE" and result["publication_ready"] is False
    assert [item["status"] for item in result["attempts"]] == ["SUCCEEDED", "SUCCEEDED"]
    assert workflow.sdk.chat_completion.await_count == 2
    restored = AuthoringJobStore(workflow.factory).get(result["id"])
    assert restored == result
    with workflow.factory() as db:
        assert len(db.scalars(select(ScriptPackageVersion)).all()) == 1
        assert len(db.scalars(select(ScriptImportJob)).all()) == 1
        assert len(db.scalars(select(ScriptAuditRecord)).all()) == 0
    assert workflow.sources.get_report(result["source_report_hash"])["valid"]


def test_authoring_worker_requires_paid_opt_in_before_claim(workflow):
    with pytest.raises(AuthoringModelError, match="AUTHORING_PAID_EXECUTION_DISABLED"):
        asyncio.run(AuthoringRunner(workflow.jobs, workflow.sources, workflow.model).run(workflow.job["id"]))
    assert workflow.jobs.get(workflow.job["id"])["state"] == "QUEUED"
    assert workflow.sdk.chat_completion.await_count == 0


def test_authoring_configuration_drift_stops_without_call(workflow):
    workflow.model.config = replace(workflow.model.config, pricing=replace(workflow.model.config.pricing, pricing_version="changed"))
    result = run(workflow)
    assert result["state"] == "BLOCKED" and result["error_code"] == "AUTHORING_CONFIGURATION_CHANGED"
    assert workflow.sdk.chat_completion.await_count == 0


def test_authoring_source_changed_before_call_blocks(workflow):
    source = workflow.sources.manifest(workflow.request.bundle_hash).sources[0]
    (workflow.sources.root / workflow.request.bundle_hash / "files" / source.relative_path).write_text("corrupt")
    result = run(workflow)
    assert result["state"] == "BLOCKED" and workflow.sdk.chat_completion.await_count == 0


def test_authoring_model_blocker_is_durable_without_candidate_or_audit(workflow):
    workflow.sdk.chat_completion.side_effect = [good_response({"schema_version": "compiler-draft/1.0", "status": "BLOCKED", "content": None, "blockers": [
        {"code": "SOURCE_GAP", "message": "Synthetic blocker", "sources": workflow.package["introduction"]["sources"]}]})]
    result = run(workflow)
    assert result["state"] == "BLOCKED" and result["error_code"] == "COMPILER_REPORTED_BLOCKERS"
    assert result["candidate_version_id"] is None and len(result["attempts"]) == 1
    assert workflow.sdk.chat_completion.await_count == 1


def test_authoring_unknown_usage_preserves_reservation_and_cannot_retry(workflow):
    workflow.sdk.chat_completion.side_effect = RuntimeError("PRIVATE_PROVIDER_ERROR_SENTINEL")
    result = run(workflow)
    assert result["state"] == "NEEDS_RECONCILIATION" and result["attempts"][0]["status"] == "UNKNOWN"
    assert result["charged_cost_cny"] == result["attempts"][0]["prepared"]["reservation"]["cost_cny"]
    assert "PRIVATE_PROVIDER_ERROR_SENTINEL" not in str(result)
    with pytest.raises(AuthoringJobError):
        workflow.jobs.recover(result["id"], result["revision"])
    assert workflow.sdk.chat_completion.await_count == 1


def test_authoring_malformed_audit_retains_candidate_and_known_charge(workflow):
    workflow.sdk.chat_completion.side_effect = [good_response(draft_for(workflow.package)),
                                              good_response({"arbitrary": "PRIVATE_OUTPUT_SENTINEL"})]
    result = run(workflow)
    assert result["state"] == "BLOCKED" and result["error_code"] == "AUDIT_OUTPUT_INVALID"
    assert result["candidate_version_id"] and result["attempts"][1]["status"] == "FAILED"
    assert result["attempts"][1]["receipt"]["usage_known"] is True
    assert "PRIVATE_OUTPUT_SENTINEL" not in str(result)


def test_authoring_cancel_during_call_keeps_late_receipt_without_candidate(workflow):
    async def answer(*args, **kwargs):
        current = workflow.jobs.get(workflow.job["id"])
        workflow.jobs.cancel(current["id"], current["revision"])
        return good_response(draft_for(workflow.package))
    workflow.sdk.chat_completion.side_effect = answer
    result = run(workflow)
    assert result["state"] == "CANCELLED" and result["candidate_version_id"] is None
    assert result["attempts"][0]["status"] == "SUCCEEDED" and workflow.sdk.chat_completion.await_count == 1


def test_authoring_file_changes_during_compile_block_candidate(workflow, monkeypatch):
    real_verify = workflow.sources.verify
    def verify(*args, **kwargs):
        report = real_verify(*args, **kwargs)
        return report | {"valid": False}
    monkeypatch.setattr(workflow.sources, "verify", verify)
    result = run(workflow)
    assert result["state"] == "BLOCKED" and result["candidate_version_id"] is None
    assert workflow.sdk.chat_completion.await_count == 1


def test_authoring_crash_after_output_recovers_without_repeat(workflow, monkeypatch):
    runner = AuthoringRunner(workflow.jobs, workflow.sources, workflow.model)
    token = workflow.jobs.claim(workflow.job["id"])
    asyncio.run(runner._invoke(workflow.job["id"], token, "COMPILE", workflow.context))
    assert workflow.sdk.chat_completion.await_count == 1
    later = authoring_jobs._now() + timedelta(seconds=301)
    monkeypatch.setattr(authoring_jobs, "_now", lambda: later)
    result = run(workflow)
    assert result["state"] == "COMPLETED", result["error_code"]
    assert workflow.sdk.chat_completion.await_count == 2
    with workflow.factory() as db:
        assert len(db.scalars(select(ScriptPackageVersion)).all()) == 1


def test_authoring_network_wait_has_no_open_database_transaction(workflow):
    async def answer(*args, **kwargs):
        # A second connection obtains a SQLite write lock while SDK awaits.
        with workflow.factory.begin() as db:
            db.add(User(username="parallel", email="parallel@example.invalid", hashed_password="fixture"))
        return good_response({"schema_version": "compiler-draft/1.0", "status": "BLOCKED", "content": None, "blockers": [
            {"code": "SOURCE_GAP", "message": "fixture", "sources": workflow.package["introduction"]["sources"]}]})
    workflow.sdk.chat_completion.side_effect = answer
    assert run(workflow)["error_code"] == "COMPILER_REPORTED_BLOCKERS"


@pytest.fixture
def api(workflow):
    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return {"admin": SimpleNamespace(id=1, is_active=True, is_admin=True),
                    "player": SimpleNamespace(id=1, is_active=True, is_admin=False),
                    "disabled": SimpleNamespace(id=1, is_active=False, is_admin=True)}.get(token)
    app = FastAPI()
    app.include_router(authoring_routes.router)
    app.add_middleware(Auth)
    app.dependency_overrides[authoring_routes.authoring_store] = lambda: workflow.jobs
    app.dependency_overrides[authoring_routes.sources_factory] = lambda: lambda: workflow.sources
    app.dependency_overrides[authoring_routes.model_factory] = lambda: lambda: workflow.model
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("token,status", [(None, 401), ("player", 403), ("disabled", 403)])
def test_authoring_http_active_admin_required(api, token, status):
    for method, path in [("GET", ""), ("POST", ""), ("GET", "/1"), ("POST", "/1/cancel"), ("POST", "/1/recover")]:
        response = api.request(method, "/api/admin/fusion/authoring-jobs" + path,
                               headers={"Authorization": f"Bearer {token}"} if token else {}, json={})
        assert response.status_code == status


def test_authoring_http_queue_replay_read_cancel_without_paid_call(api, workflow):
    base = "/api/admin/fusion/authoring-jobs"
    headers = {"Authorization": "Bearer admin"}
    queued = api.post(base, headers=headers, json=workflow.request.model_dump())
    assert queued.status_code == 202 and queued.json()["data"] == workflow.job
    assert queued.headers["cache-control"] == "no-store"
    assert api.get(base, headers=headers).json()["data"] == [workflow.job]
    assert api.get(base + "/1", headers=headers).json()["data"] == workflow.job
    response = api.post(base + "/1/cancel", headers=headers, json={"expected_revision": 0})
    assert response.status_code == 200 and response.json()["data"]["state"] == "CANCELLED"
    assert api.post(base + "/1/recover", headers=headers, json={"expected_revision": 0}).status_code == 409
    assert workflow.sdk.chat_completion.await_count == 0


@pytest.mark.parametrize("raw", ['{"PRIVATE_JSON_SENTINEL":', '{"source_ids":[],"source_ids":[]}', '{"expected_revision":true}'])
def test_authoring_http_invalid_bodies_are_sanitized(api, raw):
    response = api.post("/api/admin/fusion/authoring-jobs", headers={"Authorization": "Bearer admin"}, content=raw)
    assert response.status_code == 422 and "PRIVATE_JSON_SENTINEL" not in response.text


def test_authoring_http_bounds_ids_and_missing(api):
    headers = {"Authorization": "Bearer admin"}
    base = "/api/admin/fusion/authoring-jobs"
    assert api.post(base, headers=headers, content=b"x" * 16385).status_code == 413
    assert api.get(base + "/9999", headers=headers).status_code == 404
    for identifier in ("0", "abc", "999999999999999999999"):
        assert api.get(base + "/" + identifier, headers=headers).status_code == 422


def test_authoring_replay_does_not_reread_changed_source_or_configuration(workflow, monkeypatch):
    monkeypatch.setattr(workflow.sources, "manifest", Mock(side_effect=AssertionError("Do not reopen")))
    monkeypatch.setattr(workflow.model, "prepare", Mock(side_effect=AssertionError("Do not reprepare")))
    assert asyncio.run(submit_authoring_job(workflow.jobs, workflow.sources, workflow.model, workflow.request, 1)) == workflow.job
    assert workflow.sdk.chat_completion.await_count == 0


def test_authoring_http_replay_does_not_construct_model_or_source(api, workflow):
    unavailable = Mock(side_effect=AssertionError("Do not construct dependency"))
    api.app.dependency_overrides[authoring_routes.sources_factory] = lambda: unavailable
    api.app.dependency_overrides[authoring_routes.model_factory] = lambda: unavailable
    result = api.post("/api/admin/fusion/authoring-jobs", headers={"Authorization": "Bearer admin"}, json=workflow.request.model_dump())
    assert result.status_code == 202 and result.json()["data"] == workflow.job
    unavailable.assert_not_called()


def test_authoring_assembled_candidate_persists_exact_metadata_and_audit_receives_it(workflow):
    result = run(workflow)
    assert result["state"] == "COMPLETED", result["error_code"]
    candidate = result["attempts"][0]["output"]["package"]
    for key in ("script_key", "title", "content_version", "player_count", "sources"):
        assert candidate[key] == workflow.context[key]
    assert result["model_snapshot"]["schema_version"] == "authoring-model/1.1"
    import json
    requests = workflow.sdk.chat_completion.call_args_list
    compiler_request = json.loads(requests[0].args[0][1].content)
    audit_request = json.loads(requests[1].args[0][1].content)
    assert set(compiler_request["input"]) == {"title", "player_count", "materials", "notes"}
    assert "sources" not in compiler_request["input"]
    assert audit_request["candidate"] == candidate
    assert audit_request["input"]["sources"] == workflow.context["sources"]
    assert result["attempts"][1]["receipt"]["request_contract"] == "bailian-authoring-json/1.1"


def test_authoring_rejects_legacy_compiler_wire_in_new_task_without_audit(workflow):
    workflow.sdk.chat_completion.side_effect = [good_response({"status": "CANDIDATE", "package": workflow.package, "blockers": []})]
    result = run(workflow)
    assert result["state"] == "BLOCKED" and result["candidate_version_id"] is None
    assert result["error_code"] == "COMPILER_DRAFT_INVALID"
    assert result["attempts"][0]["receipt"]["usage_known"] is True
    assert workflow.sdk.chat_completion.await_count == 1


def test_authoring_draft_cannot_override_fixed_metadata_even_with_valid_content(workflow):
    draft = draft_for(workflow.package)
    draft["content"]["sources"] = deepcopy(workflow.context["sources"])
    workflow.sdk.chat_completion.side_effect = [good_response(draft)]
    result = run(workflow)
    assert result["state"] == "BLOCKED" and result["candidate_version_id"] is None
    assert result["error_code"] == "COMPILER_DRAFT_INVALID"
    assert workflow.sdk.chat_completion.await_count == 1


def test_authoring_smoke_preserves_job_evidence_when_semantic_acceptance_fails(tmp_path, monkeypatch):
    import json
    module = smoke_module()
    root = tmp_path / "quality-failure"
    root.mkdir(mode=0o700)
    _, sdk = stub_sdk(monkeypatch)

    async def fixture_reply(messages, **kwargs):
        user = json.loads(messages[1].content)
        if user["task"] == "COMPILE":
            # Metadata is irrelevant to the draft fixture; only selected IDs
            # enter refs. The service assembles authoritative metadata itself.
            projected = user["input"]
            context = {"script_key": module.FIXTURE_ID, "title": projected["title"],
                       "content_version": "synthetic-v1", "player_count": projected["player_count"],
                       "sources": [], "source_ids": [item["source_id"] for item in projected["materials"]]}
            package = smoke_expected_package(context, module)
            package["evidence"][0]["release"]["phase_id"] = "ending"
            output = draft_for(package)
        else:
            output = fixture_report(user["candidate"])
        return good_response(output)

    sdk.chat_completion.side_effect = fixture_reply
    model = module.SmokeAuthoringModel(fixture_config())
    code, receipt = asyncio.run(module.execute_smoke(model, root))
    assert code == 3 and receipt["result_code"] == "SMOKE_EVIDENCE_MAPPING_FAILED"
    assert receipt["job_state"] == "COMPLETED" and receipt["job_reload_verified"] is True
    assert receipt["candidate_version_id"] is not None
    assert receipt["quality"] is None and receipt["usage_known"] is True
    assert sdk.chat_completion.await_count == 2
