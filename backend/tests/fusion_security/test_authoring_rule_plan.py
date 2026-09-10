"""Frozen rule input, adversarial text output and durable workflow; zero network."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import select
from unittest.mock import Mock

from src.api.routes import authoring_routes
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.db.models import ScriptPackageVersion, ScriptAuditRecord
from src.fusion.authoring_jobs import AuthoringJobError
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError, _context, parse_compiler_draft, parse_compiler_output
from src.fusion.authoring_rule_plan import locked_projection, text_entities
from src.fusion.authoring_runner import AuthoringRunner, submit_authoring_job
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.package_validation import content_hash
from src.schemas.authoring import AuthoringRequestV13, CompilerTextDraft, parse_authoring_request
from tests.fusion_security.test_authoring_jobs import setup
from tests.fusion_security.test_authoring_model import fixture_config, good_response, stub_sdk
from tests.fusion_security.test_authoring_v12 import synthetic_v12_context_and_package, report_v12_for
from tests.fusion_security.test_package_investigation_publication import make_investigation_sources
from tests.fusion_security.test_package_investigation_smoke import expected_package
from src.fusion import investigation_authoring_smoke as fixed_smoke


def text_draft(package):
    return {"schema_version": "compiler-text-draft/1.0", "status": "CANDIDATE", "blockers": [],
            "slots": [{"collection": collection, "id": identifier, "text": entity["text"]}
                      for (collection, identifier), entity in text_entities(package)]}


def synthetic_plan():
    context, package = synthetic_v12_context_and_package()
    context["rule_plan"] = deepcopy(package)
    return context, package


def rule_model():
    return AuthoringModel(fixture_config(), "script-package/1.2", rule_plan_enabled=True)


@pytest.fixture
def workflow(setup, tmp_path, monkeypatch):
    source = make_investigation_sources(tmp_path / "new-rule-plan")
    request = AuthoringRequestV13.model_validate(source.request.model_dump() | {"rule_plan": source.package})
    context = prepare_authoring_sources(source.sources, request)
    model = rule_model()
    _, sdk = stub_sdk(monkeypatch)
    sdk.chat_completion.side_effect = [good_response(text_draft(source.package)), good_response(report_v12_for(source.package))]
    job = asyncio.run(submit_authoring_job(setup.store, source.sources, model, request, 1))
    return SimpleNamespace(jobs=setup.store, factory=setup.sessions, source=source, request=request,
                           context=context, model=model, sdk=sdk, job=job)


def test_frozen_rule_plan_preserves_exact_fields_and_omitted_defaults():
    context, package = synthetic_plan()
    before = deepcopy(context)
    draft = text_draft(package)
    draft["slots"].reverse()  # Model ordering never reorders the frozen package.
    result = parse_compiler_draft(draft, context)
    assert result["package"] == package
    assert context == before
    assert locked_projection(result["package"]) == locked_projection(package)


@pytest.mark.parametrize("mutation", ["extra-rule", "extra-source", "missing", "duplicate", "unknown-id", "singleton-id",
                                     "wrong-collection", "full-package", "wrong-version", "null-slots", "mixed-branch"])
def test_frozen_rule_plan_rejects_model_rule_changes_or_slot_drift(mutation):
    context, package = synthetic_plan()
    draft = text_draft(package)
    if mutation == "extra-rule": draft["slots"][0]["release"] = {"required_public_evidence_ids": ["ev_key"]}
    elif mutation == "extra-source": draft["slots"][0]["sources"] = package["introduction"]["sources"]
    elif mutation == "missing": draft["slots"].pop()
    elif mutation == "duplicate": draft["slots"].append(deepcopy(draft["slots"][0]))
    elif mutation == "unknown-id": draft["slots"][1]["id"] = "unknown"
    elif mutation == "singleton-id": draft["slots"][0]["id"] = "introduction"
    elif mutation == "wrong-collection": draft["slots"][0]["collection"] = "mechanics.actions"
    elif mutation == "full-package": draft["package"] = package
    elif mutation == "wrong-version": draft["schema_version"] = "compiler-draft/1.1"
    elif mutation == "null-slots": draft["slots"] = None
    elif mutation == "mixed-branch": draft["status"] = "BLOCKED"
    with pytest.raises(AuthoringModelError, match="COMPILER_TEXT_DRAFT_INVALID"):
        parse_compiler_draft(draft, context)


def test_frozen_rule_plan_still_checks_exact_source_quotes():
    context, package = synthetic_plan()
    draft = text_draft(package)
    draft["slots"][0]["text"] = "Invented unsupported replacement."
    with pytest.raises(AuthoringModelError, match="COMPILER_TEXT_NOT_EXTRACTIVE"):
        parse_compiler_draft(draft, context)


@pytest.mark.parametrize("field", ["cost", "allowed_character_ids", "required_public_evidence_ids", "disclosure"])
def test_frozen_rule_plan_domain_reparse_rejects_tampered_candidate(field):
    context, package = synthetic_plan()
    candidate = deepcopy(package)
    if field == "cost": candidate["mechanics"]["actions"][0][field] += 1
    elif field == "allowed_character_ids": candidate["mechanics"]["actions"][0][field].reverse()
    elif field == "required_public_evidence_ids":
        item = next(item for item in candidate["evidence"] if item["release"].get("required_action_ids"))
        item["release"][field] = [candidate["evidence"][0]["id"]]
    else:
        item = next(item for item in candidate["knowledge"] if item["visibility"] == "CHARACTER_PRIVATE")
        item[field] = "MAY_SHARE" if item[field] != "MAY_SHARE" else "KEEP_PRIVATE"
    with pytest.raises(AuthoringModelError):
        parse_compiler_output({"status": "CANDIDATE", "package": candidate, "blockers": []}, context)


@pytest.mark.parametrize("field", ["title", "content_version", "player_count", "sources", "quote", "mechanics", "approval"])
def test_frozen_rule_plan_invalid_input_fails_before_dispatch(field, monkeypatch):
    context, package = synthetic_plan()
    plan = context["rule_plan"]
    if field == "title": plan[field] = "Different title"
    elif field == "content_version": plan[field] = "different-version"
    elif field == "player_count": plan[field] = 8
    elif field == "sources": plan[field][0]["sha256"] = "f" * 64
    elif field == "quote": plan["introduction"]["text"] = "UNSUPPORTED_PLAN_TEXT"
    elif field == "mechanics": plan[field]["phase_budgets"][0]["points"] = -1
    else: plan["approved"] = True
    factory, _ = stub_sdk(monkeypatch)
    with pytest.raises(AuthoringModelError): rule_model().prepare("COMPILE", context)
    factory.assert_not_called()


def test_frozen_rule_plan_version_is_explicit_and_model_only_sees_text_slots():
    context, package = synthetic_plan()
    model = rule_model()
    prepared = model.prepare("COMPILE", context)
    payload = json.loads(prepared.messages[1].content)
    assert "rule_plan" not in payload["input"] and "mechanics" not in payload
    assert all(set(slot) == {"collection", "id", "sources"} for slot in payload["text_slots"])
    assert len(payload["text_slots"]) == len(text_draft(package)["slots"])
    assert prepared.request_contract["version"] == "bailian-authoring-json/1.3"
    assert model.snapshot()["schema_version"] == "authoring-model/1.3"
    assert prepared.input_tokens == sum(len(message.content.encode()) for message in prepared.messages) + 512
    with pytest.raises(AuthoringModelError, match="AUTHORING_CONTRACT_MISMATCH"):
        AuthoringModel(fixture_config(), "script-package/1.2").prepare("COMPILE", context)
    with pytest.raises(AuthoringModelError, match="AUTHORING_CONTRACT_MISMATCH"):
        model.prepare("COMPILE", {key: value for key, value in context.items() if key != "rule_plan"})
    with pytest.raises(AuthoringModelError): AuthoringModel(fixture_config(), rule_plan_enabled=True)


def test_frozen_rule_plan_request_roundtrip_and_invalid_null():
    context, _ = synthetic_plan()
    body = {key: value for key, value in context.items()
            if key in ("bundle_hash", "source_ids", "title", "content_version", "player_count", "package_contract", "rule_plan")}
    body["idempotency_key"] = "frozen-plan"
    assert parse_authoring_request(body).model_dump() == body
    with pytest.raises(ValidationError): parse_authoring_request(body | {"rule_plan": None})
    assert _context(context) == context


def test_frozen_rule_plan_blockers_use_existing_authorized_source_gate():
    context, package = synthetic_plan()
    draft = {"schema_version": "compiler-text-draft/1.0", "status": "BLOCKED", "slots": None,
             "blockers": [{"code": "CONTRADICTION", "message": "Fictional review needed.",
                           "sources": deepcopy(package["introduction"]["sources"])}]}
    assert parse_compiler_draft(draft, context)["status"] == "BLOCKED"
    draft["blockers"][0]["sources"][0]["source_id"] = "unselected"
    with pytest.raises(AuthoringModelError, match="COMPILER_REFERENCE_UNAUTHORIZED"):
        parse_compiler_draft(draft, context)


def test_frozen_rule_plan_same_fixed_smoke_distinguishes_action_and_material_prerequisites(tmp_path):
    # A new explicit test input, never a repair of an existing model candidate.
    _, _, context = fixed_smoke.make_fixture(tmp_path)
    plan = expected_package(context)
    context["rule_plan"] = deepcopy(plan)
    result = parse_compiler_draft(text_draft(plan), context)["package"]
    quality = fixed_smoke.validate_candidate(context, result)
    assert quality["both_human_roles_completed"] and quality["exact_fixture_mapping"]
    assert result["mechanics"]["actions"][1]["required_public_evidence_ids"] == ["key"]
    assert "required_public_evidence_ids" not in result["evidence"][1]["release"]
    altered = deepcopy(result)
    altered["evidence"][1]["release"]["required_public_evidence_ids"] = ["key"]
    with pytest.raises(AuthoringModelError, match="COMPILER_RULE_PLAN_MISMATCH"):
        parse_compiler_output({"status": "CANDIDATE", "package": altered, "blockers": []}, context)


def test_frozen_rule_plan_text_cannot_be_moved_across_narrow_sources(tmp_path):
    _, _, context = fixed_smoke.make_fixture(tmp_path)
    plan = expected_package(context)
    context["rule_plan"] = plan
    draft = text_draft(plan)
    draft["slots"][0]["text"] = fixed_smoke.TRUTH_TEXT
    with pytest.raises(AuthoringModelError, match="COMPILER_TEXT_NOT_EXTRACTIVE"):
        parse_compiler_draft(draft, context)


def test_frozen_rule_plan_durable_compile_audit_and_replay_without_approval(workflow):
    w = workflow
    result = asyncio.run(AuthoringRunner(w.jobs, w.source.sources, w.model).run(w.job["id"], allow_paid=True))
    assert result["state"] == "COMPLETED", result
    assert result["publication_ready"] is False
    assert len(result["attempts"]) == 2 and w.sdk.chat_completion.await_count == 2
    assert result["attempts"][0]["output"]["package"] == w.source.package
    assert all(item["receipt"]["schema_version"] == "authoring-model/1.3" for item in result["attempts"])
    inputs = w.jobs.inputs(result["id"])
    assert inputs["context"]["rule_plan"] == w.source.package
    assert asyncio.run(submit_authoring_job(w.jobs, w.source.sources, w.model, w.request, 1)) == result
    assert w.sdk.chat_completion.await_count == 2
    with w.factory() as db:
        reports = db.scalars(select(ScriptAuditRecord)).all()
        assert reports == []  # Model Audit lives in its attempt, never a human report.


def test_frozen_rule_plan_changed_input_cannot_reuse_idempotency_key(workflow):
    w = workflow
    changed = deepcopy(w.request.model_dump())
    changed["rule_plan"]["mechanics"]["actions"][0]["cost"] += 1
    with pytest.raises(AuthoringJobError):
        asyncio.run(submit_authoring_job(w.jobs, w.source.sources, w.model, parse_authoring_request(changed), 1))
    assert w.sdk.chat_completion.await_count == 0


def test_frozen_rule_plan_old_worker_cannot_dispatch_new_job(workflow):
    w = workflow
    result = asyncio.run(AuthoringRunner(w.jobs, w.source.sources,
        AuthoringModel(fixture_config(), "script-package/1.2")).run(w.job["id"], allow_paid=True))
    assert result["state"] == "BLOCKED" and result["error_code"] == "AUTHORING_CONFIGURATION_CHANGED"
    assert w.sdk.chat_completion.await_count == 0


def test_frozen_rule_plan_bad_output_stops_before_audit_or_candidate(workflow):
    w = workflow
    draft = text_draft(w.source.package)
    draft["slots"][0]["required_public_evidence_ids"] = ["ev_key"]
    w.sdk.chat_completion.side_effect = [good_response(draft)]
    result = asyncio.run(AuthoringRunner(w.jobs, w.source.sources, w.model).run(w.job["id"], allow_paid=True))
    assert result["state"] == "BLOCKED" and result["error_code"] == "COMPILER_TEXT_DRAFT_INVALID"
    assert result["candidate_version_id"] is None and w.sdk.chat_completion.await_count == 1
    assert result["attempts"][0]["receipt"]["response_fingerprint"]["content_sha256"]


def test_frozen_rule_plan_unknown_call_is_not_resent(workflow):
    w = workflow
    w.sdk.chat_completion.side_effect = TimeoutError()
    result = asyncio.run(AuthoringRunner(w.jobs, w.source.sources, w.model).run(w.job["id"], allow_paid=True))
    assert result["state"] == "NEEDS_RECONCILIATION"
    with pytest.raises(AuthoringJobError): w.jobs.recover(result["id"], result["revision"])
    assert asyncio.run(submit_authoring_job(w.jobs, w.source.sources, w.model, w.request, 1)) == result
    assert w.sdk.chat_completion.await_count == 1


def test_frozen_rule_plan_static_schemas_match():
    root = Path(__file__).resolve().parents[3] / "docs/contracts"
    for name, model in [("authoring-request.v1.3.schema.json", AuthoringRequestV13),
                        ("compiler-text-draft.v1.0.schema.json", CompilerTextDraft)]:
        assert json.loads((root / name).read_text()) == model.model_json_schema()


@pytest.mark.parametrize("confirm_text", [False, True, "indexed", "bounded", "direct", "strict", "portable", "typed", "runtime", "citation"])
def test_frozen_rule_plan_http_binding_replay_and_permissions(workflow, confirm_text):
    w = workflow

    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return {"admin": SimpleNamespace(id=1, is_active=True, is_admin=True),
                    "player": SimpleNamespace(id=1, is_active=True, is_admin=False)}.get(token)

    app = FastAPI()
    app.include_router(authoring_routes.router)
    app.add_middleware(Auth)
    app.dependency_overrides[authoring_routes.authoring_store] = lambda: w.jobs
    app.dependency_overrides[authoring_routes.sources_factory] = lambda: lambda: w.source.sources
    selected = Mock(return_value=AuthoringModel(fixture_config(), "script-package/1.2",
                    rule_plan_enabled=True, confirm_frozen_text=bool(confirm_text), indexed_audit=confirm_text in {"indexed", "bounded", "direct", "strict", "portable", "typed", "runtime", "citation"}, bounded_audit=confirm_text in {"bounded", "direct", "strict", "portable", "typed", "runtime", "citation"}, direct_audit_schema=confirm_text in {"direct", "strict", "portable", "typed", "runtime", "citation"}, strict_audit_schema=confirm_text in {"strict", "portable", "typed", "runtime", "citation"}, portable_audit_patterns=confirm_text in {"portable", "typed", "runtime", "citation"}, typed_audit_schema=confirm_text in {"typed", "runtime", "citation"}, runtime_audit_context=confirm_text in {"runtime", "citation"}, citation_audit=confirm_text == "citation"))
    app.dependency_overrides[authoring_routes.model_factory] = lambda: selected
    body = w.request.model_dump() | {"idempotency_key": "http-frozen-rule-plan"}
    if confirm_text:
        body["compiler_mode"] = "CONFIRM_FROZEN_TEXT"
    if confirm_text == "indexed":
        body["audit_mode"] = "TARGET_SOURCE_INDEXES"
    if confirm_text == "bounded":
        body["audit_mode"] = "BOUNDED_TARGET_SOURCE_INDEXES"
    if confirm_text == "direct":
        body["audit_mode"] = "DIRECT_BOUNDED_SOURCE_INDEXES"
    if confirm_text == "strict":
        body["audit_mode"] = "STRICT_BOUNDED_SOURCE_INDEXES"
    if confirm_text == "portable":
        body["audit_mode"] = "PORTABLE_STRICT_SOURCE_INDEXES"
    if confirm_text == "typed":
        body["audit_mode"] = "TYPED_STRICT_SOURCE_INDEXES"
    if confirm_text == "runtime":
        body["audit_mode"] = "RUNTIME_CONTEXT_SOURCE_INDEXES"
    if confirm_text == "citation":
        body["audit_mode"] = "CITATION_CATALOG"
    path = "/api/admin/fusion/authoring-jobs/with-rule-plan"
    headers = {"Authorization": "Bearer admin"}
    with TestClient(app) as client:
        assert client.post(path, json=body).status_code == 401
        assert client.post(path, json=body, headers={"Authorization": "Bearer player"}).status_code == 403
        queued = client.post(path, json=body, headers=headers)
        assert queued.status_code == 202 and queued.headers["cache-control"] == "no-store"
        assert queued.json()["data"]["model_snapshot"]["schema_version"] == ("authoring-model/1.12" if confirm_text == "citation" else "authoring-model/1.11" if confirm_text == "runtime" else "authoring-model/1.10" if confirm_text == "typed" else "authoring-model/1.9" if confirm_text == "portable" else "authoring-model/1.8" if confirm_text == "strict" else "authoring-model/1.7" if confirm_text == "direct" else "authoring-model/1.6" if confirm_text == "bounded" else "authoring-model/1.5" if confirm_text == "indexed" else "authoring-model/1.4" if confirm_text else "authoring-model/1.3")
        selected.assert_called_once_with("script-package/1.2", rule_plan_enabled=True,
                                          **({"confirm_frozen_text": True} if confirm_text else {}),
                                          **({"indexed_audit": True} if confirm_text in {"indexed", "bounded", "direct", "strict", "portable", "typed", "runtime", "citation"} else {}),
                                          **({"bounded_audit": True} if confirm_text in {"bounded", "direct", "strict", "portable", "typed", "runtime", "citation"} else {}),
                                          **({"direct_audit_schema": True} if confirm_text in {"direct", "strict", "portable", "typed", "runtime", "citation"} else {}),
                                          **({"strict_audit_schema": True} if confirm_text in {"strict", "portable", "typed", "runtime", "citation"} else {}),
                                          **({"portable_audit_patterns": True} if confirm_text in {"portable", "typed", "runtime", "citation"} else {}),
                                          **({"typed_audit_schema": True} if confirm_text in {"typed", "runtime", "citation"} else {}),
                                          **({"runtime_audit_context": True} if confirm_text in {"runtime", "citation"} else {}),
                                          **({"citation_audit": True} if confirm_text == "citation" else {}))
        unavailable = Mock(side_effect=AssertionError("Replay must not reload configuration or source"))
        app.dependency_overrides[authoring_routes.model_factory] = lambda: unavailable
        app.dependency_overrides[authoring_routes.sources_factory] = lambda: unavailable
        assert client.post(path, json=body, headers=headers).json() == queued.json()
        changed = deepcopy(body)
        changed["rule_plan"]["mechanics"]["actions"][0]["cost"] += 1
        assert client.post(path, json=changed, headers=headers).status_code == 409
        invalid = client.post(path, json=body | {"rule_plan": {"secret": "PRIVATE_SENTINEL"}}, headers=headers)
        assert invalid.status_code == 422 and "PRIVATE_SENTINEL" not in invalid.text
        oversized = client.post(path, content=b" " * (1024 * 1024 + 1), headers=headers)
        assert oversized.status_code == 413
        unavailable.assert_not_called()
    assert w.sdk.chat_completion.await_count == 0
