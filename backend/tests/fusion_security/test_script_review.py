"""Offline manual-review workflow: synthetic sources and a private SQLite DB only."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.routes import script_review_routes
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.db.base import SQLAlchemyBase
from src.db.models import ScriptDBModel, ScriptImportJob, ScriptPackageVersion, User
from src.db.models.script_review import ScriptAuditRecord, ScriptFindingDisposition
from src.fusion.package_import import PackageConflict, PackageImportService, PackageNotFound
from src.fusion.package_validation import canonical_json, content_hash
from src.fusion.script_review import ReviewInputError, ScriptReviewService
from src.fusion.source_bundles import SourceBundleError, SourceBundleStore
from src.schemas.script_review import FindingDispositionRequest, SubmitAuditRequest
from tests.fusion_security.test_source_bundles import bundle, candidate_for, candidate_v11_for


@pytest.fixture
def review_db():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def explicit_transactions(connection, _record):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    SQLAlchemyBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    with factory() as db:
        db.add(User(id=1, username="review-admin", email="review@example.invalid",
                    hashed_password="synthetic", is_admin=True))
        db.commit()
        yield db, factory
    engine.dispose()


@pytest.fixture
def context(review_db, bundle):
    db, factory = review_db
    document = candidate_for(bundle)
    result = PackageImportService(db).submit(document, submitted_by=1, idempotency_key="candidate-1")
    db.commit()
    return SimpleNamespace(db=db, factory=factory, document=document, version_id=result["version_id"],
                           store=bundle[2], bundle_hash=bundle[3]["bundle_hash"], service=ScriptReviewService(db))


def audit_request(context, **changes):
    body = {
        "idempotency_key": "audit-1", "expected_package_hash": content_hash(context.document),
        "bundle_hash": context.bundle_hash,
        "report": {"schema_version": "script-audit/1.0", "summary": "Synthetic manual review",
                   "coverage": ["PROVENANCE", "KNOWLEDGE_BOUNDARY"], "findings": [
                       {"id": "private-boundary", "category": "KNOWLEDGE_BOUNDARY", "severity": "BLOCKER",
                        "target": {"collection": "knowledge", "id": "memory-a"},
                        "message": "Confirm the opening disclosure boundary.",
                        "sources": deepcopy(context.document["knowledge"][0]["sources"])},
                       {"id": "source-note", "category": "PROVENANCE", "severity": "WARNING",
                        "target": {"collection": "introduction", "id": None},
                        "message": "Check the synthetic source note.",
                        "sources": deepcopy(context.document["introduction"]["sources"])},
                   ]},
    }
    body.update(changes)
    return SubmitAuditRequest.model_validate(body)


def submit_audit(context, body=None):
    body = body or audit_request(context)
    existing, document = context.service.prepare_audit(context.version_id, body, 1)
    if existing is not None:
        return existing
    verified = context.store.verify(body.bundle_hash, document=document)
    return context.service.save_audit(context.version_id, body, 1, verified, context.store)


def disposition(audit, **changes):
    body = {"idempotency_key": "disposition-1", "expected_package_hash": audit["package_hash"],
            "expected_audit_hash": audit["audit_hash"], "expected_revision": audit["revision"],
            "finding_id": "source-note", "status": "ACKNOWLEDGED", "note": "Synthetic note checked."}
    body.update(changes)
    return FindingDispositionRequest.model_validate(body)


@pytest.mark.parametrize("factory", [candidate_for, candidate_v11_for])
def test_script_review_success_chain_survives_session_reload(review_db, bundle, factory):
    db, sessions = review_db
    document = factory(bundle)
    version = PackageImportService(db).submit(document, submitted_by=1, idempotency_key="candidate-1")
    context = SimpleNamespace(db=db, document=document, version_id=version["version_id"],
                              store=bundle[2], bundle_hash=bundle[3]["bundle_hash"], service=ScriptReviewService(db))
    audit = submit_audit(context)
    assert audit["revision"] == 0 and audit["open_blockers"] == audit["open_warnings"] == 1
    result = context.service.add_disposition(audit["id"], disposition(audit), 1)
    assert result["revision"] == 1 and result["open_warnings"] == 0
    db.commit()
    with sessions() as fresh:
        restored = ScriptReviewService(fresh).get_review(context.version_id)
        assert restored["audits"] == [result]
        assert restored["candidate"]["contract_version"] == document["schema_version"]
        assert not restored["publication_ready"] and not result["publication_ready"]
        assert fresh.query(ScriptDBModel).count() == 0
        assert PackageImportService(fresh).get_job(version["id"])["report"]["pending_gates"] == [
            "SOURCE_VERIFICATION", "RUNTIME_COMPATIBILITY", "AUDIT", "HUMAN_REVIEW"]


def test_script_review_binds_exact_candidate_source_and_submitted_actor(context):
    request = audit_request(context)
    audit = submit_audit(context, request)
    row = context.db.get(ScriptAuditRecord, audit["id"])
    raw = json.loads(row.audit_json)
    assert row.audit_hash == content_hash(raw) == audit["audit_hash"]
    assert raw["request"] == request.model_dump()
    report = SourceBundleStore(context.store.root).get_report(audit["source_report_hash"])
    assert report["package_hash"] == content_hash(context.document) == audit["package_hash"]
    assert report["bundle_hash"] == audit["bundle_hash"] == context.bundle_hash
    assert raw["submitted_by"] == audit["submitted_by"] == 1
    assert raw["source_verifier_version"] == report["verifier_version"]


@pytest.mark.parametrize("case", ["entity", "source", "anchor", "page"])
def test_script_review_rejects_unbound_entities_and_source_locations(context, case, caplog):
    request = audit_request(context).model_dump()
    finding = request["report"]["findings"][0]
    if case == "entity":
        finding["target"]["id"] = "PRIVATE_FOREIGN_ENTITY"
    else:
        field, value = {"source": ("source_id", "PRIVATE_FOREIGN_SOURCE"),
                        "anchor": ("anchor", "L1"), "page": ("page", 1)}[case]
        finding["sources"][0][field] = value
    with pytest.raises(ReviewInputError) as caught:
        submit_audit(context, SubmitAuditRequest.model_validate(request))
    assert "PRIVATE_" not in str(caught.value) + caplog.text
    assert context.db.query(ScriptAuditRecord).count() == 0


def test_script_review_accepts_settlement_and_every_valid_target_type(context):
    raw = audit_request(context).model_dump()
    targets = [("introduction", None), ("settlement", None), ("characters", "a"), ("phases", "opening"),
               ("knowledge", "memory-a"), ("evidence", "clock"), ("truth", "answer")]
    template = raw["report"]["findings"][0]
    raw["report"]["findings"] = [dict(deepcopy(template), id=f"target-{index}",
                                       target={"collection": collection, "id": identifier})
                                  for index, (collection, identifier) in enumerate(targets)]
    assert len(submit_audit(context, SubmitAuditRequest.model_validate(raw))["report"]["findings"]) == len(targets)


@pytest.mark.parametrize("case", ["unpersisted", "wrong-package", "wrong-bundle", "changed-file"])
def test_script_review_rejects_untrusted_or_mismatched_source_receipts(context, bundle, case):
    request = audit_request(context)
    if case == "wrong-package":
        document = deepcopy(context.document)
        document["title"] = "Different synthetic package"
        receipt = context.store.verify(context.bundle_hash, document=document)
    elif case == "wrong-bundle":
        plan = deepcopy(bundle[1])
        plan["edition"] = "different-edition"
        frozen = context.store.freeze(bundle[0], plan)
        receipt = context.store.verify(frozen["bundle_hash"], document=context.document)
    else:
        if case == "changed-file":
            (context.store.root / context.bundle_hash / "files/normalized.txt").write_text("PRIVATE_CHANGED_FILE")
        receipt = context.store.verify(context.bundle_hash, document=context.document, persist=case != "unpersisted")
    with pytest.raises((ReviewInputError, SourceBundleError)):
        context.service.save_audit(context.version_id, request, 1, receipt, context.store)
    assert context.db.query(ScriptAuditRecord).count() == 0


def test_script_review_does_not_accept_changed_receipt_or_retired_verifier_for_new_audit(context):
    request = audit_request(context)
    receipt = context.store.verify(context.bundle_hash, document=context.document)
    changed = deepcopy(receipt)
    changed["checked_files"] += 1
    with pytest.raises(ReviewInputError):
        context.service.save_audit(context.version_id, request, 1, changed, context.store)
    historical = deepcopy(receipt)
    historical.pop("report_hash")
    historical["verifier_version"] = "source-verifier/1.0"
    digest = content_hash(historical)
    context.store._save_report(context.bundle_hash, digest, historical)
    saved = context.store.get_report(digest)
    assert saved["verifier_version"] == "source-verifier/1.0"
    with pytest.raises(ReviewInputError):
        context.service.save_audit(context.version_id, request, 1, saved, context.store)
    assert context.db.query(ScriptAuditRecord).count() == 0


def test_script_review_read_models_do_not_copy_candidate_private_bodies(context):
    submit_audit(context)
    encoded = canonical_json([context.service.list_candidates(), context.service.get_review(context.version_id)])
    assert all(marker not in encoded for marker in ("PRIVATE_a_SENTINEL", "PRIVATE_b_SENTINEL", "SYSTEM_TRUTH_SENTINEL"))
    assert "Synthetic manual review" in encoded


def test_script_review_audit_idempotency_replays_and_rejects_changed_report(context):
    request = audit_request(context)
    first = submit_audit(context, request)
    context.db.commit()
    assert submit_audit(context, request) == first
    raw = request.model_dump()
    raw["report"]["summary"] = "Different audit"
    with pytest.raises(PackageConflict, match="幂等键"):
        submit_audit(context, SubmitAuditRequest.model_validate(raw))
    assert context.db.query(ScriptAuditRecord).count() == 1


def test_script_review_disposition_idempotency_is_stable_after_other_changes(context):
    audit = submit_audit(context)
    body = disposition(audit)
    first = context.service.add_disposition(audit["id"], body, 1)
    second = context.service.add_disposition(audit["id"], disposition(first, idempotency_key="disposition-2", status="OPEN"), 1)
    assert context.service.add_disposition(audit["id"], body, 1) == second
    with pytest.raises(PackageConflict, match="幂等键"):
        context.service.add_disposition(audit["id"], disposition(audit, note="Different note"), 1)
    assert context.db.query(ScriptFindingDisposition).count() == 2


def test_script_review_stale_hashes_and_revision_never_record_a_decision(context):
    with pytest.raises(PackageConflict):
        submit_audit(context, audit_request(context, expected_package_hash="0" * 64))
    audit = submit_audit(context)
    for changes in ({"expected_package_hash": "0" * 64}, {"expected_audit_hash": "0" * 64}, {"expected_revision": 1}):
        with pytest.raises(PackageConflict):
            context.service.add_disposition(audit["id"], disposition(audit, **changes), 1)
    assert context.db.query(ScriptFindingDisposition).count() == 0


def test_script_review_blocker_cannot_be_acknowledged_and_unknown_finding_is_rejected(context):
    audit = submit_audit(context)
    for finding in ("private-boundary", "missing-finding"):
        with pytest.raises(ReviewInputError):
            context.service.add_disposition(audit["id"], disposition(audit, finding_id=finding), 1)
    assert context.db.query(ScriptFindingDisposition).count() == 0


def test_script_review_dismiss_and_reopen_preserve_history_and_never_publish(context):
    audit = submit_audit(context)
    dismissed = context.service.add_disposition(audit["id"], disposition(
        audit, finding_id="private-boundary", status="DISMISSED", note="Synthetic false positive: boundary is explicit."), 1)
    assert dismissed["open_blockers"] == 0 and not dismissed["publication_ready"]
    reopened = context.service.add_disposition(audit["id"], disposition(
        dismissed, idempotency_key="reopen-1", finding_id="private-boundary", status="OPEN", note="A second reviewer requests recheck."), 1)
    assert reopened["open_blockers"] == 1
    assert [entry["status"] for entry in reopened["dispositions"]] == ["DISMISSED", "OPEN"]
    assert [entry["revision"] for entry in reopened["dispositions"]] == [1, 2]
    assert dismissed["dispositions"][0] == reopened["dispositions"][0]


def test_script_review_new_candidate_cannot_reuse_previous_audit_or_key(context):
    old = submit_audit(context)
    context.document = deepcopy(context.document)
    context.document.update(content_version="draft-2", title="Second synthetic version")
    imported = PackageImportService(context.db).submit(context.document, submitted_by=1, idempotency_key="candidate-2")
    context.version_id = imported["version_id"]
    assert context.service.get_review(context.version_id)["audits"] == []
    with pytest.raises(PackageConflict, match="幂等键"):
        submit_audit(context)
    new = submit_audit(context, audit_request(context, idempotency_key="audit-2"))
    assert new["package_hash"] != old["package_hash"] and new["audit_hash"] != old["audit_hash"]
    with pytest.raises(PackageConflict):
        context.service.add_disposition(new["id"], disposition(old), 1)


@pytest.mark.parametrize("model,field", [(ScriptAuditRecord, "audit_json"), (ScriptFindingDisposition, "disposition_json")])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_script_review_orm_rejects_mutation_and_deletion(context, model, field, operation):
    audit = submit_audit(context)
    context.service.add_disposition(audit["id"], disposition(audit), 1)
    context.db.commit()
    row = context.db.query(model).one()
    if operation == "update":
        setattr(row, field, "{}")
    else:
        context.db.delete(row)
    with pytest.raises(ValueError, match="不可修改"):
        context.db.flush()
    context.db.rollback()


@pytest.mark.parametrize("model,field,value", [
    (ScriptAuditRecord, "audit_json", "{}"), (ScriptAuditRecord, "package_hash", "0" * 64),
    (ScriptFindingDisposition, "disposition_json", "{}"), (ScriptFindingDisposition, "revision", 2),
])
def test_script_review_raw_sql_tampering_is_detected_on_read(context, model, field, value):
    audit = submit_audit(context)
    context.service.add_disposition(audit["id"], disposition(audit), 1)
    context.db.commit()
    context.db.execute(update(model).values({field: value}))
    context.db.commit()
    context.db.expire_all()
    with pytest.raises(PackageConflict, match="完整性"):
        context.service.get_review(context.version_id)


def test_script_review_caller_rollback_removes_new_records_preserves_candidate(context):
    audit = submit_audit(context)
    context.service.add_disposition(audit["id"], disposition(audit), 1)
    context.db.rollback()
    assert context.db.query(ScriptAuditRecord).count() == context.db.query(ScriptFindingDisposition).count() == 0
    assert context.db.query(ScriptPackageVersion).count() == context.db.query(ScriptImportJob).count() == 1


def test_script_review_insert_failure_rolls_back_savepoint_without_losing_prior_audit(context):
    audit = submit_audit(context)
    context.db.commit()

    def fail(_mapper, _connection, _row):
        raise RuntimeError("Synthetic insert failure")

    event.listen(ScriptFindingDisposition, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="Synthetic"):
            context.service.add_disposition(audit["id"], disposition(audit), 1)
    finally:
        event.remove(ScriptFindingDisposition, "before_insert", fail)
    assert context.service.get_review(context.version_id)["audits"] == [audit]
    assert context.db.query(ScriptFindingDisposition).count() == 0
    assert context.service.add_disposition(audit["id"], disposition(audit), 1)["revision"] == 1


def test_script_review_audit_unique_race_recovers_identical_submission(context, monkeypatch):
    request = audit_request(context)
    first = submit_audit(context, request)
    context.db.commit()
    real_lookup = context.service._existing_audit
    calls = 0

    def stale_once(*args):
        nonlocal calls
        calls += 1
        return None if calls == 1 else real_lookup(*args)

    monkeypatch.setattr(context.service, "_existing_audit", stale_once)
    verified = context.store.get_report(first["source_report_hash"])
    assert context.service.save_audit(context.version_id, request, 1, verified, context.store) == first
    assert calls == 2 and context.db.query(ScriptAuditRecord).count() == 1


@pytest.mark.parametrize("same_key", [True, False])
def test_script_review_disposition_unique_race_replays_or_conflicts_without_duplicate(context, monkeypatch, same_key):
    audit = submit_audit(context)
    request = disposition(audit)
    winner = context.service.add_disposition(audit["id"], request, 1)
    context.db.commit()
    read = context.service._audit_result
    calls = 0

    def stale_once(*args):
        nonlocal calls
        calls += 1
        return audit if calls == 1 else read(*args)

    query = context.db.query
    missing = Mock()
    missing.filter_by.return_value.one_or_none.return_value = None
    missed = False

    def stale_key_once(model):
        nonlocal missed
        if model is ScriptFindingDisposition and not missed:
            missed = True
            return missing
        return query(model)

    monkeypatch.setattr(context.service, "_audit_result", stale_once)
    monkeypatch.setattr(context.db, "query", stale_key_once)
    if same_key:
        assert context.service.add_disposition(audit["id"], request, 1) == winner
    else:
        with pytest.raises(PackageConflict):
            context.service.add_disposition(audit["id"], disposition(audit, idempotency_key="competing-key"), 1)
    assert query(ScriptFindingDisposition).count() == 1


@pytest.fixture
def review_client(context):
    class OfflineAuth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return {"admin": SimpleNamespace(id=1, is_active=True, is_admin=True),
                    "player": SimpleNamespace(id=1, is_active=True, is_admin=False),
                    "disabled": SimpleNamespace(id=1, is_active=False, is_admin=True)}.get(token)

    def session_scope():
        with context.factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    context.db.commit()
    app = FastAPI()
    app.include_router(script_review_routes.router)
    app.add_middleware(OfflineAuth)
    app.dependency_overrides[script_review_routes.get_db_session] = session_scope
    app.dependency_overrides[script_review_routes.source_store] = lambda: context.store
    with TestClient(app) as client:
        yield client


def test_script_review_http_all_endpoints_require_active_admin(review_client):
    paths = [("GET", "review-candidates"), ("GET", "script-packages/1/review"),
             ("POST", "script-packages/1/audits"), ("POST", "script-audits/1/dispositions")]
    for token, expected in [(None, 401), ("player", 403), ("disabled", 403)]:
        for method, path in paths:
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            response = review_client.request(method, f"/api/admin/fusion/{path}", headers=headers, json={})
            assert response.status_code == expected


def test_script_review_http_real_handlers_save_replay_read_and_dispose(review_client, context):
    headers = {"Authorization": "Bearer admin"}
    path = f"/api/admin/fusion/script-packages/{context.version_id}"
    request = audit_request(context).model_dump()
    submitted = review_client.post(f"{path}/audits", headers=headers, json=request)
    assert submitted.status_code == 200 and submitted.headers["cache-control"] == "no-store"
    audit = submitted.json()["data"]
    replay = review_client.post(f"{path}/audits", headers=headers, json=request)
    assert replay.json() == submitted.json()
    result = review_client.post(f"/api/admin/fusion/script-audits/{audit['id']}/dispositions", headers=headers,
                                json=disposition(audit).model_dump())
    assert result.status_code == 200 and result.json()["data"]["revision"] == 1
    restored = review_client.get(f"{path}/review", headers=headers)
    assert restored.json()["data"]["audits"] == [result.json()["data"]]
    listing = review_client.get("/api/admin/fusion/review-candidates", headers=headers)
    assert listing.json()["data"][0]["id"] == context.version_id
    assert "SENTINEL" not in submitted.text + result.text + listing.text + restored.text
    assert review_client.get("/api/admin/fusion/script-packages/99999/review", headers=headers).status_code == 404
    context.db.expire_all()
    assert context.db.query(ScriptAuditRecord).count() == context.db.query(ScriptFindingDisposition).count() == 1


def test_script_review_http_handlers_enforce_auth_without_middleware(context):
    app = FastAPI()
    app.include_router(script_review_routes.router)
    fake = Mock()
    app.dependency_overrides[script_review_routes.review_service] = lambda: fake
    app.dependency_overrides[script_review_routes.source_store] = lambda: context.store
    with TestClient(app) as client:
        for method, path in [("GET", "review-candidates"), ("GET", "script-packages/1/review"),
                             ("POST", "script-packages/1/audits"), ("POST", "script-audits/1/dispositions")]:
            assert client.request(method, f"/api/admin/fusion/{path}", json={}).status_code == 401
    assert fake.mock_calls == []


@pytest.mark.parametrize("identifier", ["PRIVATE_BAD_ID", "0", "2147483648", "１"])
def test_script_review_http_bad_identifiers_are_sanitized(review_client, identifier, caplog):
    headers = {"Authorization": "Bearer admin"}
    for method, path in [("GET", f"script-packages/{identifier}/review"), ("POST", f"script-packages/{identifier}/audits"),
                         ("POST", f"script-audits/{identifier}/dispositions")]:
        response = review_client.request(method, f"/api/admin/fusion/{path}", headers=headers, json={})
        assert response.status_code == 422
        assert "PRIVATE_BAD_ID" not in response.text + caplog.text


@pytest.mark.parametrize("raw,status", [(b'{"summary":"PRIVATE_REVIEW_SENTINEL"}', 422),
                                      (b'{"x":1,"x":2}', 422), (b"PRIVATE_REVIEW_SENTINEL" * 24000, 413)])
def test_script_review_http_bad_and_oversized_bodies_are_sanitized(review_client, raw, status, caplog):
    for path in ("script-packages/1/audits", "script-audits/1/dispositions"):
        response = review_client.post(f"/api/admin/fusion/{path}", content=raw,
                                      headers={"Authorization": "Bearer admin", "Content-Type": "application/json"})
        assert response.status_code == status
        assert "PRIVATE_REVIEW_SENTINEL" not in response.text + caplog.text


def load_review_migration():
    path = Path(__file__).resolve().parents[2] / "src/db/migrations/versions/k1d2e3f4a5b6_add_script_review_records.py"
    spec = importlib.util.spec_from_file_location("review_migration_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_script_review_migration_matches_orm_and_downgrades_without_data_loss():
    engine = create_engine("sqlite://")
    migration = load_review_migration()
    tables = {"script_audit_records", "script_finding_dispositions"}
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE script_package_versions (id INTEGER PRIMARY KEY)"))
        connection.execute(text("INSERT INTO script_package_versions VALUES (123)"))
        context = MigrationContext.configure(connection, opts={
            "include_object": lambda obj, name, kind, reflected, compare: kind != "table" or name in tables})
        with Operations.context(context):
            migration.upgrade()
            assert compare_metadata(context, SQLAlchemyBase.metadata) == []
            migration.downgrade()
        assert set(connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()) == {
            "users", "script_package_versions"}
        assert connection.execute(text("SELECT id FROM script_package_versions")).scalar() == 123
    engine.dispose()
    assert migration.down_revision == "j0d1e2f3a4b5"


def test_script_review_postgres_migration_compiles_offline():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        load_review_migration().upgrade()
        load_review_migration().downgrade()
    sql = output.getvalue()
    assert "CREATE TABLE script_audit_records" in sql and "CREATE TABLE script_finding_dispositions" in sql
    assert "uq_script_audit_actor_key" in sql and "uq_script_disposition_revision" in sql
    assert "REFERENCES users (id)" in sql and "REFERENCES script_package_versions (id)" in sql
    assert "DROP TABLE script_finding_dispositions" in sql and "DROP TABLE script_audit_records" in sql
