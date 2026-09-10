"""Fictional v1.2 sources, real review/publication stores and offline adapters.

The generated source explicitly contains each fact, action and phase budget.
Nothing in this fixture is a review or approval of commercial material.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.api.routes import script_review_routes
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.db.base import SQLAlchemyBase
from src.db.models import User
from src.db.models.authoring_job import AuthoringAttempt
from src.db.models.package_play import ScriptPackagePlayEvent
from src.db.models.script_review import ScriptAuditRecord, ScriptFindingDisposition
from src.fusion.authoring_jobs import AuthoringJobStore
from src.fusion.authoring_model import AUDIT_CATEGORIES, AuthoringModel
from src.fusion.authoring_runner import AuthoringRunner, submit_authoring_job
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.agents import PlayerModelSettings
from src.fusion.budget import BudgetPolicy
from src.fusion.package_import import PackageImportService
from src.fusion.package_play import PackagePlayError, PackagePlayService
from src.fusion.package_role_model import PackageRoleModel
from src.fusion.package_runtime import PackageRuntimeService
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from src.fusion.script_publication import ScriptPublicationService
from src.fusion.script_review import ReviewInputError, ScriptReviewService
from src.fusion.source_bundles import SourceBundleStore
from src.schemas.authoring import AuthoringRequestV12, CompilerContentV12
from src.schemas.script_publication import ApprovePublicationRequest, PublishPackageRequest
from src.schemas.script_review import FindingDispositionRequest, parse_submit_audit_request
from tests.fusion_security.test_authoring_model import fixture_config, good_response
from tests.fusion_security.test_package_investigation_store import investigation_document, perform_body
from tests.fusion_security.test_package_play_store import MODEL, action_body, ask_body, response
from tests.fusion_security.test_script_review import review_db


def make_investigation_sources(root):
    """Reusable private synthetic source + exact candidate; no network or DB."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    inputs = root / "inputs"
    inputs.mkdir(mode=0o700)
    package = investigation_document("publication-investigation-v1")
    package["title"] = "虚构展馆搜证验收包"
    package["phases"][1]["title"] = "复盘"
    for action in package["mechanics"]["actions"]:
        action["allowed_character_ids"] = [item["id"] for item in package["characters"]]
    sections = [("introduction", package["introduction"])]
    sections.extend((collection + "-" + item["id"], item)
                    for collection in ("characters", "phases", "knowledge", "evidence", "truth")
                    for item in package[collection])
    sections.append(("settlement", package["settlement"]["instructions"]))
    sections.extend(("action-" + item["id"], item) for item in package["mechanics"]["actions"])
    sections.extend(("budget-" + item["phase_id"], item) for item in package["mechanics"]["phase_budgets"])
    # Canonical source blocks explicitly state every field. Each unique heading
    # is narrow enough for both model extraction and human target references.
    material = "# 虚构夹具说明\n仅用于离线软件核验，全部人物、资料和规则均为虚构。\n\n"
    for anchor, item in sections:
        literal = item.get("text", item.get("name", item.get("title", item.get("label", ""))))
        rules = {key: value for key, value in item.items() if key not in {"sources", "text", "name", "title", "label"}}
        material += "# " + anchor + "\n" + literal + "\n规则声明：" + canonical_json(rules) + "\n\n"
    material += "# 指定结尾绑定\n" + canonical_json({key: value for key, value in package["settlement"].items()
                                                    if key != "instructions"}) + "\n"
    for name in ("original.md", "revised.md"):
        path = inputs / name
        path.write_text(material, encoding="utf-8")
        path.chmod(0o600)
    store = SourceBundleStore(root / "source-bundles")
    frozen = store.freeze(inputs, {
        "schema_version": "source-plan/1.0", "script_key": package["script_key"], "edition": "synthetic-investigation-v1",
        "notes": ["虚构软件夹具；修订文本与虚构原文一致，不代表真实剧本人审。"],
        "sources": [
            {"relative_path": "original.md", "kind": "original", "material_type": "rules"},
            {"relative_path": "revised.md", "kind": "revised", "material_type": "rules", "original_paths": ["original.md"]},
        ],
    })
    selected = next(item for item in frozen["sources"] if item["relative_path"] == "revised.md")
    request = AuthoringRequestV12(package_contract="script-package/1.2", idempotency_key="synthetic-investigation-authoring",
        bundle_hash=frozen["bundle_hash"], source_ids=[selected["id"]], title=package["title"],
        content_version=package["content_version"], player_count=package["player_count"])
    context = prepare_authoring_sources(store, request)
    package["sources"] = deepcopy(context["sources"])
    for anchor, item in sections:
        item["sources"] = [{"source_id": selected["id"], "anchor": anchor}]
    assert validate_package(package)["valid"], validate_package(package)
    verified = store.verify(request.bundle_hash, document=package)
    assert verified["valid"], verified["issues"]
    return SimpleNamespace(root=root, sources=store, request=request, context=context, package=package,
                           document=package, bundle_hash=request.bundle_hash, verified=verified)


def investigation_report(package, *, model=False):
    return {"schema_version": "script-audit/1.1", "summary": "虚构模型审核替身，不是人审。" if model else "虚构软件夹具的人审模拟记录。",
        "coverage": sorted(AUDIT_CATEGORIES), "findings": [
            {"id": "check-action", "category": "EVIDENCE", "severity": "WARNING" if model else "BLOCKER",
             "target": {"collection": "mechanics.actions", "id": "open-case"},
             "message": "模拟检查：核对展柜调查需要已完成钥匙动作且钥匙已经公开。",
             "sources": deepcopy(package["mechanics"]["actions"][1]["sources"])},
            {"id": "check-budget", "category": "PLAYABILITY", "severity": "WARNING",
             "target": {"collection": "mechanics.phase_budgets", "id": "ending"},
             "message": "模拟检查：核对末阶段必须消耗完一点评审用行动额度。",
             "sources": deepcopy(package["mechanics"]["phase_budgets"][1]["sources"])},
        ]}


def manual_body(case, *, key="investigation-human-review"):
    return parse_submit_audit_request({"idempotency_key": key, "expected_package_hash": content_hash(case.package),
        "bundle_hash": case.bundle_hash, "report": investigation_report(case.package)})


def save_manual(case, db, body=None):
    body = body or manual_body(case)
    reviews = ScriptReviewService(db)
    existing, document = reviews.prepare_audit(case.version_id, body, 1)
    if existing is not None:
        return existing
    verified = case.sources.verify(case.bundle_hash, document=document)
    return reviews.save_audit(case.version_id, body, 1, verified, case.sources)


def decision(audit, finding_id, status, *, key=None):
    return FindingDispositionRequest(idempotency_key=key or "resolve-" + finding_id,
        expected_package_hash=audit["package_hash"], expected_audit_hash=audit["audit_hash"],
        expected_revision=audit["revision"], finding_id=finding_id, status=status,
        note="虚构人工核验：按来源中明确的条件和额度逐项确认，仅为软件夹具。")


def resolve_manual_report(case):
    """Explicit simulated decisions, separate from fixture construction."""
    with case.sessions.begin() as db:
        reviews = ScriptReviewService(db)
        audit = reviews.get_review(case.version_id)["audits"][0]
        audit = reviews.add_disposition(audit["id"], decision(audit, "check-action", "DISMISSED"), 1)
        case.manual = reviews.add_disposition(audit["id"], decision(audit, "check-budget", "ACKNOWLEDGED"), 1)
    return case.manual


def make_investigation_publication_case(root, *, manual=True, resolve_manual=False):
    """Real finite authoring chain, private durable DB, never auto-published.

    Intended for offline tests and an explicitly isolated browser fixture. The
    two authoring responses and any role response are SDK substitutes. All four
    users are synthetic; 1/2 are administrators and 3/4 are ordinary players.
    """
    case = make_investigation_sources(root)
    case.engine = create_engine("sqlite:///" + str(root / "investigation.sqlite3"),
                                connect_args={"check_same_thread": False})
    @event.listens_for(case.engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
    SQLAlchemyBase.metadata.create_all(case.engine)
    (root / "investigation.sqlite3").chmod(0o600)
    case.sessions = sessionmaker(bind=case.engine, autoflush=False)
    case.factory = case.sessions
    with case.sessions.begin() as db:
        db.add_all([User(id=identifier, username=f"investigation-{identifier}",
                        email=f"investigation-{identifier}@example.invalid", hashed_password="NOT_A_REAL_LOGIN",
                        is_active=True, is_admin=identifier in {1, 2}) for identifier in (1, 2, 3, 4)])
    case.jobs = AuthoringJobStore(case.sessions)
    case.model = AuthoringModel(fixture_config(), package_contract="script-package/1.2")
    draft = {"schema_version": "compiler-draft/1.1", "status": "CANDIDATE", "blockers": [],
             "content": {key: deepcopy(case.package[key]) for key in CompilerContentV12.model_fields}}
    case.authoring_sdk = Mock()
    case.authoring_sdk._client = None
    case.authoring_sdk.chat_completion = AsyncMock(side_effect=[
        good_response(draft), good_response(investigation_report(case.package, model=True))])
    with patch("src.fusion.authoring_model.OpenAILLMService", return_value=case.authoring_sdk):
        queued = asyncio.run(submit_authoring_job(case.jobs, case.sources, case.model, case.request, 1))
        case.job = asyncio.run(AuthoringRunner(case.jobs, case.sources, case.model).run(queued["id"], allow_paid=True))
    assert case.job["state"] == "COMPLETED", case.job["error_code"]
    assert case.authoring_sdk.chat_completion.await_count == 2
    case.version_id = case.job["candidate_version_id"]
    case.manual = None
    if manual:
        with case.sessions.begin() as db:
            case.manual = save_manual(case, db)
        if resolve_manual:
            resolve_manual_report(case)
    case.role_sdk = SimpleNamespace(chat_completion=AsyncMock(return_value=response(
        refs=[{"collection": "evidence", "id": "b-action-card"}])))
    settings = PlayerModelSettings(provider="volcengine_ark", model=MODEL, timeout_seconds=2,
        retries=0, max_output_tokens=64, max_input_bytes=24000, thinking_mode="disabled",
        temperature=0, paid_calls_enabled=True)
    case.role_model = PackageRoleModel(case.role_sdk, settings)
    case.policy = BudgetPolicy(1_000_000, Decimal("10"), Decimal("1"), Decimal("0.1"), Decimal("2"),
                               True, "synthetic-investigation/1")
    case.clock = [1000]
    return case


def real_play(case, db):
    return PackagePlayService(db, ScriptPublicationService(db, case.sources), case.role_model,
                              case.policy, lambda: case.clock[0])


def publish_investigation_case(case):
    """Explicit test human approval followed by separate release registration."""
    with case.sessions.begin() as db:
        service = ScriptPublicationService(db, case.sources)
        gate = service.gate_state(case.version_id, case.sources)
        assert gate["can_approve"], gate["checks"]
        dispositions = [{"job_id": report["job_id"], "finding_id": finding["id"], "status": "ACKNOWLEDGED",
                         "note": "虚构人工逐项核对模型建议；这不是对真实材料的批准。"}
                        for report in gate["model_reports"] for finding in report["report"]["findings"]
                        if finding["severity"] == "WARNING"]
        approval = service.approve(case.version_id, ApprovePublicationRequest(
            idempotency_key="synthetic-approve", expected_package_hash=content_hash(case.package),
            bundle_hash=case.bundle_hash, expected_basis_hash=gate["basis_hash"],
            model_dispositions=dispositions, note="虚构软件夹具独立人工确认。"), 1, case.sources)
    with case.sessions.begin() as db:
        release = ScriptPublicationService(db, case.sources).publish(case.version_id, PublishPackageRequest(
            idempotency_key="synthetic-publish", approval_id=approval["id"],
            expected_approval_hash=approval["approval_hash"], expected_basis_hash=approval["basis_hash"]), 1, case.sources)
    return approval, release


@pytest.fixture
def investigation_review(review_db, tmp_path):
    case = make_investigation_sources(tmp_path / "investigation-private")
    case.db, case.sessions = review_db
    result = PackageImportService(case.db).submit(case.package, submitted_by=1, idempotency_key="candidate-investigation")
    case.version_id = result["version_id"]
    case.db.commit()
    return case


def test_package_investigation_publication_manual_targets_source_binding_and_dispositions(investigation_review):
    case = investigation_review
    audit = save_manual(case, case.db)
    assert audit["report"]["schema_version"] == "script-audit/1.1"
    assert audit["open_blockers"] == audit["open_warnings"] == 1
    assert case.sources.get_report(audit["source_report_hash"])["verifier_version"] == "source-verifier/1.2"
    reviews = ScriptReviewService(case.db)
    with pytest.raises(ReviewInputError, match="阻断问题不能"):
        reviews.add_disposition(audit["id"], decision(audit, "check-action", "ACKNOWLEDGED"), 1)
    resolved = reviews.add_disposition(audit["id"], decision(audit, "check-action", "DISMISSED"), 1)
    resolved = reviews.add_disposition(audit["id"], decision(resolved, "check-budget", "ACKNOWLEDGED"), 1)
    case.db.commit()
    assert resolved["revision"] == 2 and resolved["open_blockers"] == resolved["open_warnings"] == 0
    assert not resolved["publication_ready"]
    with case.sessions() as db:
        reread = ScriptReviewService(db).get_review(case.version_id)
        assert reread["audits"] == [resolved]
        references = {(item["target"]["collection"], item["target"]["id"]): item["sources"] for item in reread["references"]}
        assert references[("mechanics.actions", "open-case")] == case.package["mechanics"]["actions"][1]["sources"]
        assert references[("mechanics.phase_budgets", "ending")] == case.package["mechanics"]["phase_budgets"][1]["sources"]
        row = db.get(ScriptAuditRecord, audit["id"])
        raw = json.loads(row.audit_json)
        assert raw["package_validator_version"] == "script-package-validator/1.2"
        assert content_hash(raw) == row.audit_hash == audit["audit_hash"]
        assert db.query(ScriptFindingDisposition).count() == 2
        assert db.query(AuthoringAttempt).count() == 0
    assert save_manual(case, case.db) == resolved


@pytest.mark.parametrize("change", ["unknown-action", "wrong-budget-id", "other-valid-anchor", "unknown-anchor", "other-source"])
def test_package_investigation_publication_rejects_unbound_mechanic_targets(investigation_review, change, caplog):
    case = investigation_review
    raw = manual_body(case).model_dump()
    finding = raw["report"]["findings"][0]
    if change == "unknown-action":
        finding["target"]["id"] = "PRIVATE_UNKNOWN_ACTION"
    elif change == "wrong-budget-id":
        finding["target"] = {"collection": "mechanics.phase_budgets", "id": "open-case"}
    elif change == "other-valid-anchor":
        finding["sources"] = deepcopy(case.package["mechanics"]["phase_budgets"][0]["sources"])
    elif change == "unknown-anchor":
        finding["sources"][0]["anchor"] = "PRIVATE_UNKNOWN_ANCHOR"
    else:
        finding["sources"][0]["source_id"] = case.package["sources"][0]["id"]
    with pytest.raises(ReviewInputError) as error:
        save_manual(case, case.db, parse_submit_audit_request(raw))
    assert "PRIVATE_UNKNOWN" not in str(error.value) + caplog.text
    assert case.db.query(ScriptAuditRecord).count() == 0


@pytest.mark.parametrize("candidate_contract,report_contract", [
    ("script-package/1.2", "script-audit/1.0"), ("script-package/1.1", "script-audit/1.1"),
])
def test_package_investigation_publication_report_contract_must_match_candidate(investigation_review, candidate_contract, report_contract):
    case = investigation_review
    if candidate_contract == "script-package/1.1":
        legacy = deepcopy(case.package)
        legacy.update(schema_version=candidate_contract, content_version="legacy-source-fixture")
        del legacy["mechanics"]
        for collection in ("knowledge", "evidence"):
            for item in legacy[collection]:
                item["release"].pop("required_action_ids", None)
        result = PackageImportService(case.db).submit(legacy, submitted_by=1, idempotency_key="legacy-candidate")
        case.package, case.version_id = legacy, result["version_id"]
    body = parse_submit_audit_request({"idempotency_key": "wrong-report-version", "bundle_hash": case.bundle_hash,
        "expected_package_hash": content_hash(case.package), "report": {"schema_version": report_contract,
        "summary": "虚构错版报告", "coverage": sorted(AUDIT_CATEGORIES), "findings": []}})
    with pytest.raises(ReviewInputError, match="报告版本与候选包不匹配"):
        save_manual(case, case.db, body)
    assert case.db.query(ScriptAuditRecord).count() == 0


def test_package_investigation_publication_candidate_missing_mechanic_anchor_cannot_record_review(investigation_review):
    case = investigation_review
    broken = deepcopy(case.package)
    broken["content_version"] = "missing-anchor"
    broken["mechanics"]["actions"][1]["sources"][0]["anchor"] = "PRIVATE_MISSING_SOURCE_ANCHOR"
    result = PackageImportService(case.db).submit(broken, submitted_by=1, idempotency_key="missing-anchor")
    case.package, case.version_id = broken, result["version_id"]
    with pytest.raises(ReviewInputError, match="当前来源核验未通过"):
        save_manual(case, case.db)
    assert case.db.query(ScriptAuditRecord).count() == 0


def test_package_investigation_publication_http_records_new_report_and_targets(investigation_review):
    case = investigation_review
    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return SimpleNamespace(id=1, is_active=True, is_admin=True) if token == "synthetic-admin" else None
    app = FastAPI()
    app.add_middleware(Auth)
    app.include_router(script_review_routes.router)
    app.dependency_overrides[script_review_routes.review_service] = lambda: ScriptReviewService(case.db)
    app.dependency_overrides[script_review_routes.source_store] = lambda: case.sources
    headers = {"Authorization": "Bearer synthetic-admin"}
    with TestClient(app) as client:
        response = client.post(f"/api/admin/fusion/script-packages/{case.version_id}/audits", json=manual_body(case).model_dump(), headers=headers)
        assert response.status_code == 200 and response.headers["Cache-Control"] == "no-store"
        audit = response.json()["data"]
        result = client.post(f"/api/admin/fusion/script-audits/{audit['id']}/dispositions",
                             json=decision(audit, "check-action", "DISMISSED").model_dump(), headers=headers)
        assert result.status_code == 200 and result.json()["data"]["open_blockers"] == 0
        reread = client.get(f"/api/admin/fusion/script-packages/{case.version_id}/review", headers=headers)
        assert reread.status_code == 200 and reread.json()["data"]["audits"] == [result.json()["data"]]
        assert all(secret not in reread.text for secret in ("PRIVATE_a_SENTINEL", "SYSTEM_TRUTH_SENTINEL"))


def test_package_investigation_publication_authoring_to_real_release_play_and_history(tmp_path):
    case = make_investigation_publication_case(tmp_path / "complete-private")
    try:
        assert case.job["publication_ready"] is False
        assert [item["status"] for item in case.job["attempts"]] == ["SUCCEEDED", "SUCCEEDED"]
        with case.sessions() as db:
            publisher = ScriptPublicationService(db, case.sources)
            gate = publisher.gate_state(case.version_id, case.sources)
            assert not gate["can_approve"] and gate["approval"] is None and gate["release"] is None
            assert gate["model_reports"][0]["report"]["schema_version"] == "script-audit/1.1"
            assert db.query(ScriptAuditRecord).count() == 1 and db.query(AuthoringAttempt).count() == 2
            assert PackageImportService(db).get_version(case.version_id)["package"] == case.package
        resolve_manual_report(case)
        approval, release = publish_investigation_case(case)
        assert approval["package_hash"] == release["package_hash"] == content_hash(case.package)
        with case.sessions.begin() as db:
            publisher = ScriptPublicationService(db, case.sources)
            assert publisher.get_release(release["id"])["package"] == case.package
            db.rollback()  # End the explicit creation fence before the next source read.
        with case.sessions.begin() as db:
            opening = PackageRuntimeService(db, ScriptPublicationService(db, case.sources)).create(
                {"release_id": release["id"], "character_id": "a", "idempotency_key": "synthetic-opening"}, 3)
        assert opening["supports_rules_preview"] is False and "clock" not in {item["id"] for item in opening["private_evidence"]}
        with case.sessions.begin() as db:
            initial = real_play(case, db).create({"opening_session_id": opening["session_id"], "idempotency_key": "synthetic-play"}, 3)
        assert initial["mechanics"]["remaining_points"] == 3
        for revision, action_id in enumerate(("find-key", "open-case")):
            with case.sessions.begin() as db:
                searched = real_play(case, db).act(initial["play_id"], perform_body(action_id, revision), 3)
        assert searched["mechanics"]["remaining_points"] == 0 and searched["can_advance"]
        with case.sessions() as db:
            answered = asyncio.run(real_play(case, db).ask(initial["play_id"], ask_body(2), 3))
        assert answered["last_ai_status"] == "OK" and answered["revision"] == 4
        assert answered["dialogue"][0]["text"] == "证据原文：B_ACTION_REWARD_SENTINEL"
        assert any(item["id"] == "b-action-card" and item["shared_by_character_id"] == "b" for item in answered["public_evidence"])
        assert answered["budget"]["used_tokens"] == 110 and answered["mechanics"]["spent_points"] == 3
        for body in (action_body(4), action_body(5, "advance", "ADVANCE_PHASE"),
                     perform_body("next-search", 6), action_body(7, "settle", "SETTLE")):
            with case.sessions.begin() as db:
                settled = real_play(case, db).act(initial["play_id"], body, 3)
        assert settled["settled"] and settled["revision"] == 8
        assert settled["mechanics"]["remaining_points"] == 0
        assert settled["settlement"]["truths"] == [{"id": "answer", "text": "SYSTEM_TRUTH_SENTINEL：钟在维修。"}]
        assert next(item for item in settled["public_knowledge"] if item["id"] == "public-claim")["kind"] == "CLAIM"
        with case.sessions() as db:
            hashes = [(row.event_hash, row.state_hash) for row in db.query(ScriptPackagePlayEvent).order_by(ScriptPackagePlayEvent.revision)]
            assert real_play(case, db).get(initial["play_id"], 3) == settled
            assert PackageRuntimeService(db, ScriptPublicationService(db, case.sources)).get(opening["session_id"], 3) == opening
            assert len(hashes) == 8
        # Reopening a finding on a mechanics target invalidates the release's
        # current basis. Already acquired content remains an immutable history.
        with case.sessions.begin() as db:
            review = ScriptReviewService(db).get_review(case.version_id)["audits"][0]
            ScriptReviewService(db).add_disposition(review["id"], decision(review, "check-action", "OPEN", key="reopen-action"), 2)
        with case.sessions() as db:
            publisher = ScriptPublicationService(db, case.sources)
            gate = publisher.gate_state(case.version_id, case.sources)
            assert gate["basis_hash"] != approval["basis_hash"] and not gate["approval"]["valid"]
            assert publisher.list_releases() == []
            assert real_play(case, db).get(initial["play_id"], 3) == settled
            with pytest.raises(PackagePlayError, match="^PACKAGE_PLAY_RELEASE_UNAVAILABLE$"):
                real_play(case, db).create({"opening_session_id": opening["session_id"], "idempotency_key": "new-play-after-reopen"}, 3)
            assert hashes == [(row.event_hash, row.state_hash) for row in db.query(ScriptPackagePlayEvent).order_by(ScriptPackagePlayEvent.revision)]
        assert case.authoring_sdk.chat_completion.await_count == 2 and case.role_sdk.chat_completion.await_count == 1
        assert case.root.stat().st_mode & 0o777 == 0o700
        assert all(path.stat().st_mode & 0o777 == 0o600 for path in case.root.rglob("*") if path.is_file())
    finally:
        case.engine.dispose()
