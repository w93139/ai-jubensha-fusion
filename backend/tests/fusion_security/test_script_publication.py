"""Offline human publication gates over real private source and SQLite stores."""
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event, update
from sqlalchemy.orm import sessionmaker

from src.db.base import SQLAlchemyBase
from src.db.models import User
from src.db.models.script_publication import ScriptPackageRelease, ScriptPublicationApproval
from src.fusion import script_publication
from src.fusion.authoring_jobs import AuthoringJobStore
from src.fusion.authoring_model import AuthoringModel
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.package_validation import content_hash
from src.fusion.package_import import PackageImportService
from src.fusion.script_publication import PublicationError, ScriptPublicationService
from src.fusion.script_review import ScriptReviewService
from src.schemas.script_publication import ApprovePublicationRequest, PublishPackageRequest
from src.schemas.script_review import FindingDispositionRequest, SubmitAuditRequest
from tests.fusion_security.test_authoring_jobs import prepared_metadata, receipt_for
from tests.fusion_security.test_authoring_model import fixture_config, fixture_report, smoke_expected_package, smoke_module


def add_model_job(case, *, key="model-one", severity="WARNING", outcome="SUCCEEDED"):
    request = case.request.model_dump() | {"idempotency_key": key}
    job = case.jobs.create(request, case.context, case.model.snapshot(), 1)
    token = case.jobs.claim(job["id"])
    prepared = prepared_metadata(case.model, "COMPILE", case.context)
    attempt = case.jobs.reserve(job["id"], token, "COMPILE", prepared)
    case.jobs.dispatch(job["id"], token, attempt["id"])
    case.jobs.finish_attempt(attempt["id"], {"status": "CANDIDATE", "package": case.package, "blockers": []}, receipt_for(case, prepared))
    verified = case.sources.verify(case.request.bundle_hash, document=case.package)
    job = case.jobs.materialize(job["id"], token, case.package, verified["report_hash"])
    if outcome == "PENDING":
        return job
    prepared = prepared_metadata(case.model, "AUDIT", case.context, case.package)
    attempt = case.jobs.reserve(job["id"], token, "AUDIT", prepared)
    case.jobs.dispatch(job["id"], token, attempt["id"])
    if outcome == "IN_FLIGHT":
        return case.jobs.get(job["id"])
    if outcome == "SUCCEEDED":
        report = fixture_report(case.package)
        report["findings"][0]["severity"] = severity
        case.jobs.finish_attempt(attempt["id"], report, receipt_for(case, prepared))
        return case.jobs.checkpoint(job["id"], token, step="DONE", state="COMPLETED")
    code = "AUDIT_OUTPUT_INVALID" if outcome == "FAILED" else "AUTHORING_TIMEOUT_USAGE_UNKNOWN"
    case.jobs.finish_attempt(attempt["id"], None, receipt_for(case, prepared, known=outcome != "UNKNOWN", code=code), code)
    return case.jobs.checkpoint(job["id"], token, step="AUDIT", state="BLOCKED" if outcome == "FAILED" else "NEEDS_RECONCILIATION", error_code=code)


def add_manual_report(case, *, key="manual-one", severity=None, coverage=None):
    report = fixture_report(case.package)
    report["summary"] = "Synthetic human review, recorded separately from the model."
    if severity is None:
        report["findings"] = []
    else:
        report["findings"][0]["severity"] = severity
    if coverage is not None:
        report["coverage"] = coverage
    body = SubmitAuditRequest(idempotency_key=key, expected_package_hash=content_hash(case.package),
                              bundle_hash=case.request.bundle_hash, report=report)
    verified = case.sources.verify(case.request.bundle_hash, document=case.package)
    with case.sessions.begin() as db:
        return ScriptReviewService(db).save_audit(case.version_id, body, 1, verified, case.sources)


def dispose_manual(case, audit, status, *, key="human-decision"):
    body = FindingDispositionRequest(idempotency_key=key, expected_package_hash=audit["package_hash"],
                                    expected_audit_hash=audit["audit_hash"], expected_revision=audit["revision"],
                                    finding_id="f1", status=status, note="Synthetic reviewer rationale.")
    with case.sessions.begin() as db:
        return ScriptReviewService(db).add_disposition(audit["id"], body, 1)


def make_publication_case(root, *, manual=True, model_outcome="SUCCEEDED", model_severity="WARNING"):
    """Reusable offline fixture; never approves or publishes on construction."""
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    module = smoke_module()
    sources, request = module.make_synthetic_bundle(root)
    context = prepare_authoring_sources(sources, request)
    engine = create_engine(f"sqlite:///{root / 'publication.sqlite3'}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        # Intentionally retain sqlite3 legacy transactions: outer rollback is
        # part of the contract, not supplied by a test-only BEGIN hook.
        connection.execute("PRAGMA foreign_keys=ON")

    SQLAlchemyBase.metadata.create_all(engine)
    (root / "publication.sqlite3").chmod(0o600)
    sessions = sessionmaker(bind=engine, autoflush=False)
    with sessions.begin() as db:
        db.add_all([User(id=i, username=f"publication-{i}", email=f"publication-{i}@example.invalid",
                         hashed_password="synthetic-not-a-login", is_admin=i != 3, is_active=i != 4) for i in (1, 2, 3, 4)])
    case = SimpleNamespace(root=root, sources=sources, request=request, context=context,
                           package=smoke_expected_package(context, module), engine=engine, sessions=sessions,
                           jobs=AuthoringJobStore(sessions), model=AuthoringModel(fixture_config()))
    case.job = add_model_job(case, severity=model_severity, outcome=model_outcome)
    case.version_id = case.job["candidate_version_id"]
    case.manual = add_manual_report(case) if manual else None
    return case


@pytest.fixture
def publication_case(tmp_path):
    case = make_publication_case(tmp_path / "private")
    yield case
    case.engine.dispose()


def gate(case):
    with case.sessions() as db:
        return ScriptPublicationService(db, case.sources).gate_state(case.version_id, case.sources)


def approval_request(case, *, key="approve-one"):
    state = gate(case)
    decisions = [{"job_id": report["job_id"], "finding_id": finding["id"],
                  "status": "DISMISSED" if finding["severity"] == "BLOCKER" else "ACKNOWLEDGED",
                  "note": "Synthetic human reviewed this model finding independently."}
                 for report in state["model_reports"] if report["report"]
                 for finding in report["report"]["findings"] if finding["severity"] in {"WARNING", "BLOCKER"}]
    return ApprovePublicationRequest(idempotency_key=key, expected_package_hash=state["candidate"]["package_hash"],
                                     bundle_hash=state["bundle_hash"], expected_basis_hash=state["basis_hash"],
                                     model_dispositions=decisions, note="Synthetic explicit human approval.")


def approve_case(case, *, body=None, actor=1):
    body = body or approval_request(case)
    with case.sessions.begin() as db:
        return ScriptPublicationService(db, case.sources).approve(case.version_id, body, actor, case.sources)


def publication_request(approval, *, key="publish-one"):
    return PublishPackageRequest(idempotency_key=key, approval_id=approval["id"],
                                 expected_approval_hash=approval["approval_hash"], expected_basis_hash=approval["basis_hash"])


def publish_case(case, approval=None):
    approval = approval or approve_case(case)
    with case.sessions.begin() as db:
        return ScriptPublicationService(db, case.sources).publish(case.version_id, publication_request(approval), 1, case.sources)


def test_publication_complete_human_gate_preserves_separate_model_report(publication_case):
    case = publication_case
    before = gate(case)
    assert before["can_approve"] and not before["can_publish"]
    assert before["manual_reports"][0]["report"]["findings"] == []
    assert before["model_reports"][0]["report"]["findings"][0]["severity"] == "WARNING"
    approval = approve_case(case)
    approved = gate(case)
    assert approved["basis_hash"] == before["basis_hash"] and approved["approval"]["valid"] and approved["can_publish"]
    assert approval["source_report_hash"] != case.manual["source_report_hash"]
    release = publish_case(case, approval)
    released = gate(case)
    assert not released["can_approve"] and not released["can_publish"] and released["release"]["current_approval_valid"]
    assert release["source_report_hash"] != approval["source_report_hash"]
    with case.sessions() as db:
        service = ScriptPublicationService(db, case.sources)
        current = service.get_release(release["id"])
        assert current["package"] == case.package and current["package_hash"] == content_hash(case.package)
        db.rollback()
        assert service.list_releases()[0]["id"] == release["id"]
        assert "package" not in service.list_releases()[0]


def test_publication_manual_report_is_required_even_with_valid_model(tmp_path):
    case = make_publication_case(tmp_path / "private", manual=False)
    try:
        state = gate(case)
        assert not state["can_approve"]
        assert not next(item["passed"] for item in state["checks"] if item["code"] == "MANUAL_AUDIT_COMPLETE")
        add_manual_report(case, coverage=["PROVENANCE"])
        assert not gate(case)["can_approve"]
        add_manual_report(case, key="manual-full")
        assert gate(case)["can_approve"]
    finally:
        case.engine.dispose()


@pytest.mark.parametrize("actor", [True, 0, 3, 4, 999])
def test_publication_requires_active_human_admin(publication_case, actor):
    case = publication_case
    with pytest.raises(PublicationError, match="^PUBLICATION_ADMIN_REQUIRED$"):
        approve_case(case, actor=actor)
    approval = approve_case(case)
    with case.sessions.begin() as db, pytest.raises(PublicationError, match="^PUBLICATION_ADMIN_REQUIRED$"):
        ScriptPublicationService(db).publish(case.version_id, publication_request(approval), actor, case.sources)


@pytest.mark.parametrize("change", ["missing", "extra", "blocker-ack"])
def test_publication_all_model_findings_need_exact_human_decisions(tmp_path, change):
    case = make_publication_case(tmp_path / "private", model_severity="BLOCKER")
    try:
        body = approval_request(case).model_dump()
        if change == "missing":
            body["model_dispositions"] = []
        elif change == "extra":
            body["model_dispositions"].append(body["model_dispositions"][0] | {"finding_id": "invented"})
        else:
            body["model_dispositions"][0]["status"] = "ACKNOWLEDGED"
        with pytest.raises(PublicationError, match="^PUBLICATION_MODEL_FINDINGS_UNRESOLVED$"):
            approve_case(case, body=ApprovePublicationRequest.model_validate(body))
        assert approve_case(case)["model_dispositions"][0]["status"] == "DISMISSED"
    finally:
        case.engine.dispose()


def test_publication_does_not_hide_old_blockers_behind_latest_twenty_reports(publication_case):
    case = publication_case
    old = add_manual_report(case, key="old-blocker", severity="BLOCKER")
    for index in range(21):
        add_manual_report(case, key=f"later-{index}")
    state = gate(case)
    assert len(state["manual_reports"]) == 23 and not state["can_approve"]
    dispose_manual(case, old, "DISMISSED")
    assert gate(case)["can_approve"]


def test_publication_new_human_report_and_disposition_expire_prior_approval(publication_case):
    case = publication_case
    approval = approve_case(case)
    warning = add_manual_report(case, key="new-warning", severity="WARNING")
    assert not gate(case)["approval"]["valid"]
    dispose_manual(case, warning, "ACKNOWLEDGED")
    assert gate(case)["can_approve"] and not gate(case)["approval"]["valid"]
    with case.sessions.begin() as db, pytest.raises(PublicationError, match="^PUBLICATION_APPROVAL_EXPIRED$"):
        ScriptPublicationService(db).publish(case.version_id, publication_request(approval), 1, case.sources)
    second = approve_case(case, body=approval_request(case, key="approve-two"))
    assert second["basis_hash"] != approval["basis_hash"]


@pytest.mark.parametrize("outcome", ["PENDING", "IN_FLIGHT", "UNKNOWN", "FAILED"])
def test_publication_related_job_history_and_unknown_usage_gate(publication_case, outcome):
    case = publication_case
    old = approve_case(case)
    add_model_job(case, key="additional-job", outcome=outcome)
    state = gate(case)
    assert len(state["model_reports"]) == 2 and state["basis_hash"] != old["basis_hash"]
    assert not state["approval"]["valid"]
    assert state["can_approve"] is (outcome == "FAILED")


def test_publication_released_snapshot_survives_new_report_but_new_sessions_do_not(publication_case):
    case = publication_case
    body = approval_request(case)
    approval = approve_case(case, body=body)
    release = publish_case(case, approval)
    add_manual_report(case, key="later-review")
    state = gate(case)
    assert not state["can_approve"] and not state["release"]["current_approval_valid"]
    assert approve_case(case, body=body) == approval  # exact immutable replay
    with pytest.raises(PublicationError, match="^PUBLICATION_VERSION_ALREADY_RELEASED$"):
        approve_case(case, body=approval_request(case, key="new-approval"))
    with case.sessions() as db:
        service = ScriptPublicationService(db, case.sources)
        assert service.get_release(release["id"], require_current=False)["package"] == case.package
        assert service.list_releases() == []
        with pytest.raises(PublicationError, match="^PUBLICATION_APPROVAL_EXPIRED$"):
            service.get_release(release["id"])


@pytest.mark.parametrize("step", ["approve", "publish"])
def test_publication_sqlite_legacy_outer_rollback_undoes_snapshot(publication_case, step):
    case = publication_case
    approval = approve_case(case) if step == "publish" else None
    body = publication_request(approval) if approval else approval_request(case)
    model = ScriptPackageRelease if approval else ScriptPublicationApproval
    with case.sessions() as db:
        service = ScriptPublicationService(db)
        getattr(service, step)(case.version_id, body, 1, case.sources)
        assert db.query(model).count() == 1
        db.rollback()
    with case.sessions() as db:
        assert db.query(model).count() == 0


def test_publication_idempotent_replay_has_no_new_verification_or_records(publication_case, monkeypatch):
    case = publication_case
    body = approval_request(case)
    approval = approve_case(case, body=body)
    release = publish_case(case, approval)
    monkeypatch.setattr(case.sources, "verify", lambda *args, **kwargs: pytest.fail("idempotent replay must not write new source report"))
    assert approve_case(case, body=body) == approval
    assert publish_case(case, approval) == release
    changed = body.model_copy(update={"note": "Changed human decision."})
    with pytest.raises(PublicationError, match="^PUBLICATION_IDEMPOTENCY_CONFLICT$"):
        approve_case(case, body=changed)


def test_publication_basis_is_rechecked_after_source_io(publication_case, monkeypatch):
    case = publication_case
    body = approval_request(case)
    original = case.sources.verify
    changed = False

    def verify(*args, **kwargs):
        nonlocal changed
        result = original(*args, **kwargs)
        if kwargs.get("persist") and not changed:
            changed = True
            add_manual_report(case, key="review-during-source-check")
        return result

    monkeypatch.setattr(case.sources, "verify", verify)
    with pytest.raises(PublicationError, match="^PUBLICATION_BASIS_CHANGED$"):
        approve_case(case, body=body)
    with case.sessions() as db:
        assert db.query(ScriptPublicationApproval).count() == 0


def test_publication_catalog_is_read_only_but_new_session_takes_candidate_lock(publication_case, monkeypatch):
    case = publication_case
    release = publish_case(case)
    calls = []
    original = script_publication.lock_candidate_version
    monkeypatch.setattr(script_publication, "lock_candidate_version", lambda db, version: (calls.append(version), original(db, version))[1])
    with case.sessions() as db:
        service = ScriptPublicationService(db, case.sources)
        assert len(service.list_releases()) == 1 and calls == []
        service.get_release(release["id"])
        assert calls == [case.version_id]


@pytest.mark.parametrize("model,column", [(ScriptPublicationApproval, "approval_json"), (ScriptPackageRelease, "release_json")])
def test_publication_refreshes_identity_map_before_integrity_check(publication_case, model, column):
    case = publication_case
    release = publish_case(case)
    identifier = release["approval_id"] if model is ScriptPublicationApproval else release["id"]
    with case.sessions() as db:
        row = db.get(model, identifier)
        db.execute(update(model).where(model.id == identifier).values({column: '{"PRIVATE_SENTINEL":true}'}), execution_options={"synchronize_session": False})
        assert "PRIVATE_SENTINEL" not in getattr(row, column)
        with pytest.raises(PublicationError) as caught:
            ScriptPublicationService(db, case.sources).get_release(release["id"], require_current=False)
        assert "INTEGRITY_FAILED" in str(caught.value) and "PRIVATE_SENTINEL" not in str(caught.value)
        db.rollback()


def test_publication_bounded_full_history_fails_closed(publication_case, monkeypatch):
    case = publication_case
    monkeypatch.setattr(script_publication, "MAX_REPORTS", 1)
    add_manual_report(case, key="overflow")
    with pytest.raises(PublicationError, match="^PUBLICATION_REVIEW_LIMIT_EXCEEDED$"):
        gate(case)


def test_publication_changed_source_blocks_actions_but_preserves_historical_release(publication_case):
    case = publication_case
    approval = approve_case(case)
    release = publish_case(case, approval)
    target = case.sources.root / case.request.bundle_hash / "files" / "fixture.md"
    target.write_text("PRIVATE_CORRUPTED_SOURCE_SENTINEL", encoding="utf-8")
    state = gate(case)
    assert not next(item["passed"] for item in state["checks"] if item["code"] == "SOURCE_VERIFIED")
    assert not state["approval"]["valid"] and not state["release"]["current_approval_valid"]
    assert "PRIVATE_CORRUPTED_SOURCE_SENTINEL" not in str(state)
    with case.sessions() as db:
        service = ScriptPublicationService(db, case.sources)
        assert service.list_releases() == []
        assert service.get_release(release["id"], require_current=False)["package"] == case.package
        with pytest.raises(PublicationError, match="^PUBLICATION_APPROVAL_EXPIRED$"):
            service.get_release(release["id"])


@pytest.mark.parametrize("step", ["approve", "publish"])
def test_publication_changed_source_rejected_before_new_snapshot(publication_case, step):
    case = publication_case
    approval = approve_case(case) if step == "publish" else None
    body = publication_request(approval) if approval else approval_request(case)
    target = case.sources.root / case.request.bundle_hash / "files" / "fixture.md"
    target.write_text("PRIVATE_CORRUPTED_SOURCE_SENTINEL", encoding="utf-8")
    with case.sessions.begin() as db, pytest.raises(PublicationError, match="^PUBLICATION_SOURCE_UNVERIFIED$"):
        getattr(ScriptPublicationService(db), step)(case.version_id, body, 1, case.sources)
    with case.sessions() as db:
        assert db.query(ScriptPackageRelease if approval else ScriptPublicationApproval).count() == 0


def test_publication_candidate_without_audits_or_bundle_returns_closed_gate(publication_case):
    case = publication_case
    document = case.package | {"content_version": "unreviewed-v2"}
    with case.sessions.begin() as db:
        result = PackageImportService(db).submit(document, submitted_by=1, idempotency_key="unreviewed-candidate")
    with case.sessions() as db:
        state = ScriptPublicationService(db).gate_state(result["version_id"], case.sources)
    assert state["bundle_hash"] is None and state["manual_reports"] == state["model_reports"] == []
    assert not state["can_approve"]
    assert {item["code"] for item in state["checks"] if not item["passed"]} >= {"MANUAL_AUDIT_COMPLETE", "MODEL_AUDIT_COMPLETE", "SOURCE_SCOPE_VALID"}


def test_publication_mixed_bundles_cannot_be_hidden_by_explicit_selection(publication_case):
    case = publication_case
    other = case.sources.freeze(case.root / "synthetic-inputs", {
        "schema_version": "source-plan/1.0", "script_key": case.context["script_key"], "edition": "second-freeze",
        "notes": ["A distinct synthetic evidence snapshot."],
        "sources": [{"relative_path": "fixture.md", "kind": "original", "material_type": "host"}]})
    assert other["bundle_hash"] != case.request.bundle_hash
    original = case.request
    case.request = original.model_copy(update={"bundle_hash": other["bundle_hash"]})
    add_manual_report(case, key="other-bundle")
    case.request = original
    with case.sessions() as db:
        service = ScriptPublicationService(db)
        for selected in (None, original.bundle_hash, other["bundle_hash"]):
            state = service.gate_state(case.version_id, case.sources, selected)
            assert state["bundle_hash"] is None and not state["can_approve"]
            assert not next(item["passed"] for item in state["checks"] if item["code"] == "SOURCE_SCOPE_VALID")
