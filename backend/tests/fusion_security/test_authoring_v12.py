"""Version-isolated authoring with wholly invented material and a stubbed SDK."""
import asyncio
from copy import deepcopy
from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from unittest.mock import Mock

from src.api.routes import authoring_routes
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.db.models import User, ScriptAuditRecord, ScriptPackageVersion, ScriptImportJob, AuthoringJob, AuthoringAttempt
from src.fusion import authoring_model
from src.fusion.authoring_jobs import AuthoringJobError, AuthoringJobStore
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError, _context, parse_compiler_draft, validate_model_audit
from src.fusion.authoring_runner import AuthoringRunner, submit_authoring_job
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from src.fusion.source_bundles import SourceBundleStore
from src.schemas.authoring import (
    AuthoringRequest, AuthoringRequestV12, CompilerContentV12, CompilerDraftOutput, CompilerDraftOutputV12,
    parse_authoring_request,
)
from src.schemas.script_review import (
    ManualAuditReport, ManualAuditReportV12, SubmitAuditRequest, SubmitAuditRequestV12, finding_entity,
    parse_audit_report, parse_submit_audit_request,
)
from tests.fusion_security.test_authoring_model import (
    fixture_config, fixture_report, good_response, stub_sdk, synthetic_context_and_package,
)
from tests.fusion_security.test_package_investigation import investigation_package
from tests.fusion_security.test_authoring_jobs import setup


PACKAGE_V12 = "script-package/1.2"


def synthetic_v12_context_and_package():
    context, base = synthetic_context_and_package()
    package = investigation_package(base)
    for action in package["mechanics"]["actions"]:
        action["allowed_character_ids"] = [item["id"] for item in package["characters"]]
    fragments = [package["introduction"]["text"], *(item["name"] for item in package["characters"]),
                 *(item["title"] for item in package["phases"]),
                 *(item["text"] for collection in ("knowledge", "evidence", "truth") for item in package[collection]),
                 package["settlement"]["instructions"]["text"], *(item["label"] for item in package["mechanics"]["actions"]),
                 "Wholly invented: opening has 3 shared points and requires zero remaining; ending has 1 and may finish early.",
                 "Both fictional seats can execute all declared actions. Costs are 1, 2, 1 and 0; each action runs once.",
                 "Open-case requires find-key and public key; next-search requires open-case. This fixture is never approval."]
    text = "# fixture-opening\n" + "\n".join(fragments) + "\n"
    package["sources"][0]["sha256"] = sha256(text.encode()).hexdigest()
    context["sources"] = deepcopy(package["sources"])
    context["materials"][0]["text"] = text
    context["package_contract"] = PACKAGE_V12
    assert validate_package(package)["valid"]
    return context, package


def draft_v12_for(package):
    return {"schema_version": "compiler-draft/1.1", "status": "CANDIDATE",
            "content": {key: deepcopy(package[key]) for key in CompilerContentV12.model_fields}, "blockers": []}


def report_v12_for(package):
    report = fixture_report(package)
    report["schema_version"] = "script-audit/1.1"
    report["findings"] = [
        {"id": "synthetic-action-review", "category": "PLAYABILITY", "severity": "WARNING",
         "target": {"collection": "mechanics.actions", "id": package["mechanics"]["actions"][0]["id"]},
         "message": "Synthetic mechanics need a separate human content decision.",
         "sources": deepcopy(package["mechanics"]["actions"][0]["sources"])},
        {"id": "synthetic-budget-review", "category": "PROVENANCE", "severity": "INFO",
         "target": {"collection": "mechanics.phase_budgets", "id": package["mechanics"]["phase_budgets"][0]["phase_id"]},
         "message": "Only invented source and stubbed model behavior were checked.",
         "sources": deepcopy(package["mechanics"]["phase_budgets"][0]["sources"])},
    ]
    return report


@pytest.fixture
def workflow_v12(tmp_path, monkeypatch):
    synthetic, package = synthetic_v12_context_and_package()
    root = tmp_path / "v12-private"
    material_root = root / "materials"
    material_root.mkdir(parents=True, mode=0o700)
    (material_root / "fixture.md").write_text(synthetic["materials"][0]["text"])
    sources = SourceBundleStore(root / "bundles")
    frozen = sources.freeze(material_root, {"schema_version": "source-plan/1.0", "script_key": package["script_key"],
        "edition": "invented-v12", "notes": ["Wholly fictional; no human approval."],
        "sources": [{"relative_path": "fixture.md", "kind": "original", "material_type": "host"}]})
    request = AuthoringRequestV12(idempotency_key="fixture-v12", bundle_hash=frozen["bundle_hash"],
        source_ids=[frozen["sources"][0]["id"]], title=package["title"], content_version=package["content_version"],
        player_count=package["player_count"], package_contract=PACKAGE_V12)
    context = prepare_authoring_sources(sources, request)
    for key in ("script_key", "title", "content_version", "player_count", "sources"):
        package[key] = deepcopy(context[key])
    refs = [{"source_id": request.source_ids[0], "anchor": "fixture-opening"}]
    package["introduction"]["sources"] = deepcopy(refs)
    package["settlement"]["instructions"]["sources"] = deepcopy(refs)
    for collection in ("characters", "phases", "knowledge", "evidence", "truth"):
        for item in package[collection]:item["sources"] = deepcopy(refs)
    for collection in ("phase_budgets", "actions"):
        for item in package["mechanics"][collection]:item["sources"] = deepcopy(refs)
    engine = create_engine("sqlite:///" + str(root / "jobs.sqlite"), connect_args={"autocommit": False})
    for model_type in (User, ScriptPackageVersion, ScriptImportJob, AuthoringJob, AuthoringAttempt, ScriptAuditRecord):
        model_type.__table__.create(engine)
    factory = sessionmaker(engine, autoflush=False)
    with factory.begin() as db:
        db.add(User(id=1, username="v12-fixture", email="v12@example.invalid", hashed_password="FICTIONAL", is_admin=True))
    jobs = AuthoringJobStore(factory)
    model = AuthoringModel(fixture_config(), package_contract=PACKAGE_V12)
    _, sdk = stub_sdk(monkeypatch)
    sdk.chat_completion.side_effect = [good_response(draft_v12_for(package)), good_response(report_v12_for(package))]
    job = asyncio.run(submit_authoring_job(jobs, sources, model, request, 1))
    yield SimpleNamespace(jobs=jobs, sources=sources, model=model, request=request, context=context,
                          package=package, sdk=sdk, job=job, factory=factory, root=root)
    engine.dispose()


def test_v12_preserves_frozen_legacy_schemas_prompts_and_request_dump():
    expected = {AuthoringRequest: "798d5462abacef5a9215b49649a04c7a976a0da1835f5665f73e6e1b98e7871e",
                CompilerDraftOutput: "dd42e05457a98e49322c0fa7158335117685e5f6e8d0f884671da572e6bcb0cf",
                ManualAuditReport: "297c51b06eb6fe6f6dbdfcd7807f42da274cab4cfeca4aec4fad2881780ff937",
                SubmitAuditRequest: "4253bcd75b15aa5084ed2cd8659391f592f6bbcadefc14d0ae0fa6c93852780a"}
    for model, digest in expected.items():assert content_hash(model.model_json_schema()) == digest
    assert sha256(authoring_model._prompt("COMPILE").encode()).hexdigest() == "bf495bf658a2db5ce84751128d61ac7d9a1d17c6cf71b4b224847b7190812142"
    assert sha256(authoring_model._prompt("AUDIT").encode()).hexdigest() == "ee8d98550f30491b554ad40f21d253d9698af530b511cf06d0531252a0423aec"
    context, _ = synthetic_context_and_package()
    body = {key: context[key] for key in ("bundle_hash", "source_ids", "title", "content_version", "player_count")}
    body["idempotency_key"] = "legacy"
    assert parse_authoring_request(body).model_dump() == body
    assert _context(context) == context and "package_contract" not in _context(context)


@pytest.mark.parametrize("contract", [None, "script-package/1.1", "script-package/9.0", {}, False])
def test_v12_request_requires_explicit_exact_contract(contract):
    context, _ = synthetic_v12_context_and_package()
    body = {key: context[key] for key in ("bundle_hash", "source_ids", "title", "content_version", "player_count")}
    with pytest.raises(ValidationError):parse_authoring_request(body | {"idempotency_key": "x", "package_contract": contract})


def test_v12_actual_prompt_schema_origin_catalog_and_budget_are_bound():
    context, package = synthetic_v12_context_and_package()
    model = AuthoringModel(fixture_config(), package_contract=PACKAGE_V12)
    prepared = model.prepare("COMPILE", context)
    user = json.loads(prepared.messages[1].content)
    assert user["available_source_origins"] == [{"source_id": "fixture-source", "kind": "original"}]
    assert set(user["input"]) == {"title", "player_count", "materials", "notes"}
    assert all(key not in user["input"] for key in ("sources", "package_contract", "content_version", "script_key"))
    assert "compiler-draft/1.1" in prepared.messages[0].content
    assert "只有玩家所选择的真人角色能执行调查动作" in prepared.messages[0].content
    assert "normalized只表示" in prepared.messages[0].content
    assert prepared.input_tokens == sum(len(m.content.encode()) for m in prepared.messages) + 512
    assert prepared.reservation.cost_cny <= Decimal("0.05")
    audit = model.prepare("AUDIT", context, package)
    assert "AI角色只能按已授权材料回答，不能自行执行调查" in audit.messages[0].content
    assert "script-audit/1.1" in audit.messages[0].content
    assert model.snapshot()["schema_version"] == "authoring-model/1.2"
    assert prepared.request_contract["version"] == "bailian-authoring-json/1.2"
    assert parse_compiler_draft(draft_v12_for(package), context)["package"] == package


@pytest.mark.parametrize("wrong", ["legacy-wire", "legacy-context", "fixed-metadata", "unknown-action", "unselected-source", "wrong-label"])
def test_v12_compiler_does_not_accept_cross_version_injection_or_unbound_mechanics(wrong):
    context, package = synthetic_v12_context_and_package()
    draft = draft_v12_for(package)
    if wrong == "legacy-wire":draft["schema_version"] = "compiler-draft/1.0"
    elif wrong == "legacy-context":context.pop("package_contract")
    elif wrong == "fixed-metadata":draft["content"]["title"] = "PRIVATE_SENTINEL"
    elif wrong == "unknown-action":draft["content"]["evidence"][0]["release"]["required_action_ids"] = ["PRIVATE_SENTINEL"]
    elif wrong == "unselected-source":draft["content"]["mechanics"]["actions"][0]["sources"] = [{"source_id": "PRIVATE_SENTINEL", "anchor": "L1"}]
    else:draft["content"]["mechanics"]["actions"][0]["label"] = "PRIVATE_SENTINEL"
    with pytest.raises(AuthoringModelError) as error:parse_compiler_draft(draft, context)
    assert "PRIVATE_SENTINEL" not in str(error.value)
    assert "PRIVATE_SENTINEL" not in canonical_json(error.value.details)


def test_v12_audit_resolves_action_and_budget_and_keeps_report_versions_separate():
    _, package = synthetic_v12_context_and_package()
    report = report_v12_for(package)
    actual = validate_model_audit(report, package)
    assert actual["schema_version"] == "script-audit/1.1"
    for finding in report["findings"]:
        entity = finding_entity(package, finding["target"])
        assert entity["sources"] == finding["sources"]
    request = {"idempotency_key": "manual-v12", "expected_package_hash": content_hash(package), "bundle_hash": "a"*64, "report": report}
    assert parse_submit_audit_request(request).report.schema_version == "script-audit/1.1"
    with pytest.raises(ValueError):parse_audit_report(report, "script-package/1.1")
    with pytest.raises(AuthoringModelError):validate_model_audit(fixture_report(package), package)
    assert finding_entity(package, {"collection": "mechanics.phase_budgets", "id": "find-key"}) is None
    assert finding_entity(package, {"collection": "mechanics.actions", "id": None}) is None


@pytest.mark.parametrize("mutation", ["absent", "foreign-reference", "approval", "duplicate", "null-budget"])
def test_v12_audit_rejects_unobserved_target_or_authority(mutation):
    _, package = synthetic_v12_context_and_package();report = report_v12_for(package)
    if mutation == "absent":report["findings"][0]["target"]["id"] = "PRIVATE_SENTINEL"
    elif mutation == "foreign-reference":report["findings"][0]["sources"][0]["anchor"] = "PRIVATE_SENTINEL"
    elif mutation == "approval":report["publication_ready"] = True
    elif mutation == "duplicate":report["findings"][1]["id"] = report["findings"][0]["id"]
    else:report["findings"][1]["target"]["id"] = None
    with pytest.raises(AuthoringModelError) as error:validate_model_audit(report, package)
    assert error.value.code == "AUDIT_OUTPUT_INVALID"
    assert "PRIVATE_SENTINEL" not in canonical_json(error.value.details)


def test_v12_runner_uses_real_adapter_source_candidate_and_audit_without_approval(workflow_v12):
    w=workflow_v12
    assert w.job["state"] == "QUEUED" and w.sdk.chat_completion.await_count == 0
    result=asyncio.run(AuthoringRunner(w.jobs,w.sources,w.model).run(w.job["id"],allow_paid=True))
    assert result["state"] == "COMPLETED", result["error_code"]
    assert result["publication_ready"] is False
    assert [a["status"] for a in result["attempts"]] == ["SUCCEEDED","SUCCEEDED"]
    assert w.sdk.chat_completion.await_count == 2
    assert result["attempts"][0]["output"]["package"] == w.package
    assert result["attempts"][1]["output"]["schema_version"] == "script-audit/1.1"
    assert w.sources.get_report(result["source_report_hash"])["verifier_version"] == "source-verifier/1.2"
    assert AuthoringJobStore(w.factory).get(result["id"]) == result
    assert asyncio.run(submit_authoring_job(w.jobs,w.sources,w.model,w.request,1)) == result
    assert w.sdk.chat_completion.await_count == 2
    with w.factory() as db:
        assert len(db.scalars(select(ScriptPackageVersion)).all()) == 1
        assert len(db.scalars(select(ScriptAuditRecord)).all()) == 0
        package=db.scalars(select(ScriptPackageVersion)).one()
        assert package.contract_version == PACKAGE_V12


def test_v12_cannot_replay_old_ledger_or_inject_default_contract(setup):
    legacy=setup.store.get(setup.job["id"]);before=setup.store.inputs(legacy["id"])
    new=AuthoringModel(fixture_config(),package_contract=PACKAGE_V12)
    with pytest.raises(AuthoringModelError,match="AUTHORING_CONTRACT_MISMATCH"):
        new.prepare("COMPILE",setup.context)
    request=setup.request|{"idempotency_key":"new-version","package_contract":PACKAGE_V12}
    with pytest.raises(AuthoringJobError):setup.store.create(request,setup.context,new.snapshot(),1)
    with pytest.raises(AuthoringJobError):setup.store.create(request,setup.context|{"package_contract":PACKAGE_V12},setup.model.snapshot(),1)
    assert setup.store.get(legacy["id"]) == legacy
    assert setup.store.inputs(legacy["id"]) == before
    assert setup.store.existing_request(setup.request,1) == legacy


def test_v12_editorial_source_cannot_claim_original_rule_origin():
    context, package = synthetic_v12_context_and_package()
    source = package["sources"][0]
    source.update(kind="supplement", provenance_note="Explicit fictional editorial supplement; no approval.")
    context["sources"] = deepcopy(package["sources"])
    with pytest.raises(AuthoringModelError, match="COMPILER_PACKAGE_INVALID") as error:
        parse_compiler_draft(draft_v12_for(package), context)
    assert error.value.details == [{"code": "PACKAGE_SCHEMA_INVALID", "entity_path": "/"}]
    for collection in ("phase_budgets", "actions"):
        for rule in package["mechanics"][collection]:rule["origin"] = "EDITORIAL"
    assert parse_compiler_draft(draft_v12_for(package), context)["package"] == package
    prepared = AuthoringModel(fixture_config(), package_contract=PACKAGE_V12).prepare("COMPILE", context)
    assert json.loads(prepared.messages[1].content)["available_source_origins"] == [
        {"source_id": "fixture-source", "kind": "supplement"}]


def test_v12_http_explicit_contract_queues_and_replay_never_reloads_dependencies(workflow_v12):
    w=workflow_v12

    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return {"admin": SimpleNamespace(id=1,is_active=True,is_admin=True),
                    "player": SimpleNamespace(id=1,is_active=True,is_admin=False)}.get(token)

    app=FastAPI();app.include_router(authoring_routes.router);app.add_middleware(Auth)
    app.dependency_overrides[authoring_routes.authoring_store]=lambda:w.jobs
    app.dependency_overrides[authoring_routes.sources_factory]=lambda:lambda:w.sources
    selected=Mock(return_value=w.model)
    app.dependency_overrides[authoring_routes.model_factory]=lambda:selected
    body=w.request.model_dump()|{"idempotency_key":"http-v12"}
    path="/api/admin/fusion/authoring-jobs";headers={"Authorization":"Bearer admin"}
    with TestClient(app) as client:
        assert client.post(path,json=body).status_code==401
        assert client.post(path,json=body,headers={"Authorization":"Bearer player"}).status_code==403
        queued=client.post(path,json=body,headers=headers)
        assert queued.status_code==202 and queued.headers["cache-control"]=="no-store"
        assert queued.json()["data"]["model_snapshot"]["schema_version"]=="authoring-model/1.2"
        selected.assert_called_once_with(PACKAGE_V12)
        unavailable=Mock(side_effect=AssertionError("Replay must not reload sources/configuration"))
        app.dependency_overrides[authoring_routes.model_factory]=lambda:unavailable
        app.dependency_overrides[authoring_routes.sources_factory]=lambda:unavailable
        replay=client.post(path,json=body,headers=headers)
        assert replay.status_code==202 and replay.json()==queued.json()
        old_body={key:value for key,value in body.items() if key!="package_contract"}
        assert client.post(path,json=old_body,headers=headers).status_code==409
        invalid=client.post(path,json=body|{"package_contract":"PRIVATE_CONTRACT_SENTINEL"},headers=headers)
        assert invalid.status_code==422 and "PRIVATE_CONTRACT_SENTINEL" not in invalid.text
        unavailable.assert_not_called()
    assert w.sdk.chat_completion.await_count==0


def test_v12_old_wire_rejection_keeps_known_charge_and_never_runs_audit(workflow_v12):
    w=workflow_v12;draft=draft_v12_for(w.package);draft["schema_version"]="compiler-draft/1.0"
    w.sdk.chat_completion.side_effect=[good_response(draft)]
    result=asyncio.run(AuthoringRunner(w.jobs,w.sources,w.model).run(w.job["id"],allow_paid=True))
    assert result["state"]=="BLOCKED" and result["candidate_version_id"] is None
    assert result["error_code"]=="COMPILER_DRAFT_INVALID" and len(result["attempts"])==1
    assert result["attempts"][0]["receipt"]["usage_known"] is True
    assert result["attempts"][0]["receipt"]["schema_version"]=="authoring-model/1.2"
    assert Decimal(result["charged_cost_cny"])>0 and w.sdk.chat_completion.await_count==1
    assert AuthoringJobStore(w.factory).get(result["id"])==result


def test_v12_unknown_dispatch_stays_reserved_and_cannot_recover_or_recall(workflow_v12):
    w=workflow_v12;w.sdk.chat_completion.side_effect=TimeoutError()
    result=asyncio.run(AuthoringRunner(w.jobs,w.sources,w.model).run(w.job["id"],allow_paid=True))
    assert result["state"]=="NEEDS_RECONCILIATION"
    attempt=result["attempts"][0]
    assert attempt["status"]=="UNKNOWN" and attempt["receipt"]["usage_known"] is False
    assert result["charged_cost_cny"]==attempt["prepared"]["reservation"]["cost_cny"]
    with pytest.raises(AuthoringJobError):w.jobs.recover(result["id"],result["revision"])
    assert asyncio.run(submit_authoring_job(w.jobs,w.sources,w.model,w.request,1))==result
    assert w.sdk.chat_completion.await_count==1


def test_v12_static_schema_documents_match_live_contracts():
    contracts=Path(__file__).resolve().parents[3]/"docs/contracts"
    for name,model in [("authoring-request.v1.2.schema.json",AuthoringRequestV12),
                       ("compiler-draft.v1.1.schema.json",CompilerDraftOutputV12),
                       ("script-audit.v1.1.schema.json",ManualAuditReportV12),
                       ("script-audit-submit.v1.1.schema.json",SubmitAuditRequestV12)]:
        assert json.loads((contracts/name).read_text())==model.model_json_schema()


def test_v12_compiler_v7_preserves_archives_and_binds_only_the_new_prompt():
    directory = authoring_model.PROMPT_DIRECTORY
    hashes = {
        "compiler_system_v4.txt": "bf495bf658a2db5ce84751128d61ac7d9a1d17c6cf71b4b224847b7190812142",
        "compiler_system_v5.txt": "699686c68b8ad30e7b08eb84d22b7e6934f564af3f6de51dcb8fc66009c6b6a7",
        "compiler_system_v6.txt": "64ca4b7182d2af85ba741021bdf8932a4a378d18c5617c7ccfccc0fa0554cf56",
        "audit_system_v3.txt": "ee8d98550f30491b554ad40f21d253d9698af530b511cf06d0531252a0423aec",
        "audit_system_v4.txt": "3d67373a259c3fd0a6bd38c10394134cbd2a3fb03f2590b3ce7fac1e4c17519b",
    }
    for filename, expected in hashes.items():
        assert sha256((directory / filename).read_bytes()).hexdigest() == expected
    archived = (directory / "compiler_system_v5.txt").read_bytes()
    assert (directory / "compiler_system_v6.txt").read_bytes().startswith(archived)
    current = (directory / "compiler_system_v7.txt").read_bytes()
    assert current.startswith(archived)
    assert current.count(authoring_model.COMPILE_SCHEMA_TAIL_MARKER.encode()) == 1
    snapshot = AuthoringModel(fixture_config(), package_contract=PACKAGE_V12).snapshot()
    assert snapshot["prompt_hashes"]["COMPILE"] == sha256(current).hexdigest()
    assert snapshot["prompt_hashes"]["COMPILE"] not in hashes.values()
    assert snapshot["prompt_hashes"]["AUDIT"] == hashes["audit_system_v4.txt"]
    assert snapshot["schema_version"] == "authoring-model/1.2"
    assert snapshot["request_contract"] == "bailian-authoring-json/1.2"


def test_v12_archived_v6_retains_its_original_rendering_and_byte_reservation(monkeypatch):
    context, package = synthetic_v12_context_and_package()
    archived = (authoring_model.PROMPT_DIRECTORY / "compiler_system_v6.txt").read_text()
    current_prompt = authoring_model._prompt
    monkeypatch.setattr(authoring_model, "_prompt", lambda step, package_contract="script-package/1.1":
        archived if step == "COMPILE" and package_contract == PACKAGE_V12 else current_prompt(step, package_contract))
    model = AuthoringModel(fixture_config(), package_contract=PACKAGE_V12)
    prepared = model.prepare("COMPILE", context)
    assert prepared.messages[0].content == archived + "\nJSON Schema：\n" + canonical_json(authoring_model._schema("COMPILE", PACKAGE_V12))
    assert prepared.prompt_hash == "64ca4b7182d2af85ba741021bdf8932a4a378d18c5617c7ccfccc0fa0554cf56"
    assert prepared.input_tokens == sum(len(message.content.encode()) for message in prepared.messages) + 512
    assert prepared.reservation == fixture_config().pricing.amount(prepared.input_tokens, 8192 + 16)
    blocked = {"schema_version": "compiler-draft/1.1", "status": "BLOCKED", "content": None,
        "blockers": [{"code": "SOURCE_GAP", "message": "Fictional bounded archive response.",
                      "sources": package["introduction"]["sources"]}]}
    _, sdk = stub_sdk(monkeypatch, good_response(blocked))
    result = asyncio.run(model.call("COMPILE", context, prepared=prepared))
    assert sdk.chat_completion.call_args.args[0] == list(prepared.messages)
    assert result["receipt"]["prompt_hash"] == prepared.prompt_hash
    assert result["receipt"]["contract_hash"] == prepared.contract_hash
    assert sdk.chat_completion.await_count == 1


@pytest.mark.parametrize("archive", ["v5", "v6"])
def test_v12_archived_jobs_remain_readable_but_cannot_dispatch_as_v7(setup, monkeypatch, archive):
    context = setup.context | {"package_contract": PACKAGE_V12}
    request = setup.request | {"idempotency_key": "archived-" + archive, "package_contract": PACKAGE_V12}
    model = AuthoringModel(fixture_config(), package_contract=PACKAGE_V12)
    archived = (authoring_model.PROMPT_DIRECTORY / ("compiler_system_" + archive + ".txt")).read_text()
    current_prompt = authoring_model._prompt
    with monkeypatch.context() as old_version:
        old_version.setattr(authoring_model, "_prompt", lambda step, package_contract="script-package/1.1":
            archived if step == "COMPILE" and package_contract == PACKAGE_V12 else current_prompt(step, package_contract))
        old_prepared = model.prepare("COMPILE", context)
        old_snapshot = model.snapshot()
        old_job = setup.store.create(request, context, old_snapshot, 1)
        old_inputs = setup.store.inputs(old_job["id"])
    fresh = model.prepare("COMPILE", context)
    assert fresh.request_contract == old_prepared.request_contract
    assert fresh.prompt_hash != old_prepared.prompt_hash and fresh.contract_hash != old_prepared.contract_hash
    assert setup.store.get(old_job["id"]) == old_job
    assert setup.store.inputs(old_job["id"]) == old_inputs
    assert setup.store.existing_request(request, 1) == old_job
    factory, _ = stub_sdk(monkeypatch)
    with pytest.raises(AuthoringModelError, match="^AUTHORING_PREPARATION_CHANGED$"):
        asyncio.run(model.call("COMPILE", context, prepared=old_prepared))
    factory.assert_not_called()


def test_v12_v7_actual_wire_places_examples_after_schema_and_prices_all_sent_bytes(monkeypatch):
    context, package = synthetic_v12_context_and_package()
    model = AuthoringModel(fixture_config(), package_contract=PACKAGE_V12)
    prepared = model.prepare("COMPILE", context)
    prompt = (authoring_model.PROMPT_DIRECTORY / "compiler_system_v7.txt").read_text()
    prefix, tail = prompt.split(authoring_model.COMPILE_SCHEMA_TAIL_MARKER)
    schema = canonical_json(authoring_model._schema("COMPILE", PACKAGE_V12))
    system = prepared.messages[0].content
    assert system == prefix + "\nJSON Schema：\n" + schema + "\n" + tail
    assert system.endswith(tail) and system.index(schema) < system.index("最终字段对照")
    assert authoring_model.COMPILE_SCHEMA_TAIL_MARKER not in system
    assert not any(text in system for text in ["打开资料柜", "蓝色封条", "ev_key", "synthetic-investigation-authoring-v1"])
    fragments = [json.loads(line) for line in tail.splitlines() if line.startswith("{")]
    correct, incorrect, independent = fragments
    action = correct["mechanics"]["actions"][0]
    material = correct["evidence"][0]["release"]
    assert material["required_action_ids"] == [action["id"]]
    assert action["required_public_evidence_ids"] and material["required_public_evidence_ids"] == []
    assert incorrect["release"]["required_public_evidence_ids"] == action["required_public_evidence_ids"]
    assert independent["required_public_evidence_ids"] and independent["required_public_evidence_ids"] != action["required_public_evidence_ids"]
    assert prepared.prompt_hash == sha256(prompt.encode()).hexdigest()
    assert prepared.input_tokens == sum(len(message.content.encode()) for message in prepared.messages) + 512
    assert prepared.reservation == fixture_config().pricing.amount(prepared.input_tokens, 8192 + 16)
    blocked = {"schema_version": "compiler-draft/1.1", "status": "BLOCKED", "content": None,
        "blockers": [{"code": "SOURCE_GAP", "message": "Fictional bounded current response.",
                      "sources": package["introduction"]["sources"]}]}
    _, sdk = stub_sdk(monkeypatch, good_response(blocked))
    result = asyncio.run(model.call("COMPILE", context, prepared=prepared))
    assert sdk.chat_completion.call_args.args[0] == list(prepared.messages)
    assert sdk.chat_completion.call_args.kwargs == {"max_completion_tokens": 8192, "temperature": 0,
        "response_format": {"type": "json_object"}, "extra_body": {"enable_thinking": False, "preserve_thinking": False}}
    assert result["receipt"]["prompt_hash"] == prepared.prompt_hash
    assert result["receipt"]["contract_hash"] == prepared.contract_hash
    assert sdk.chat_completion.await_count == 1


@pytest.mark.parametrize("parts", [("body only",), ("body", "tail", "duplicate"), ("", "tail"), ("body", "")])
def test_v12_current_v7_requires_exactly_one_nonempty_schema_marker_before_dispatch(tmp_path, monkeypatch, parts):
    prompt = authoring_model.COMPILE_SCHEMA_TAIL_MARKER.join(parts)
    (tmp_path / "compiler_system_v7.txt").write_text(prompt)
    monkeypatch.setattr(authoring_model, "PROMPT_DIRECTORY", tmp_path)
    model = AuthoringModel(fixture_config(), package_contract=PACKAGE_V12)
    context, _ = synthetic_v12_context_and_package()
    factory, _ = stub_sdk(monkeypatch)
    with pytest.raises(AuthoringModelError, match="^AUTHORING_PROMPT_UNAVAILABLE$"):
        asyncio.run(model.call("COMPILE", context))
    factory.assert_not_called()
