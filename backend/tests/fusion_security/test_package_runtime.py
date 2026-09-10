"""Opening-preview boundaries with fictional packages and an in-memory publisher.

Publisher approval/source verification is tested by its own suite. These tests
use a real local SQLite transaction and immutable candidate snapshots, while
the publisher seam controls current versus historical release resolution.
"""
from copy import deepcopy
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, event, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.api.routes import package_runtime_routes
from src.db.models import ScriptImportJob, ScriptPackageVersion, User
from src.db.models.package_runtime import ScriptPackagePlaySession
from src.fusion.package_import import PackageImportService
from src.fusion.package_runtime import PackageRuntimeError, PackageRuntimeService
from src.fusion.package_validation import canonical_json, content_hash, validate_package
from tests.fusion_security.test_script_packages import fictional_package, fictional_package_v11
from tests.fusion_security.test_authoring_runner import workflow, run


class FixturePublisher:
    def __init__(self):
        self.records = {}
        self.current = set()
        self.reads = []

    def list_releases(self):
        return [deepcopy(self.records[key]) for key in sorted(self.current)]

    def get_release(self, release_id, *, require_current=True):
        self.reads.append((release_id, require_current))
        if release_id not in self.records or (require_current and release_id not in self.current):
            raise ValueError("PRIVATE_PUBLISHER_DETAILS")
        return deepcopy(self.records[release_id])


@pytest.fixture
def runtime(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "preview.sqlite"),
                           connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def explicit_transactions(connection, _record):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")

    @event.listens_for(engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")

    # A copied schema keeps the publisher test double independent of its
    # approval persistence model without replacing any global ORM metadata.
    metadata = MetaData()
    for model in (User, ScriptPackageVersion, ScriptImportJob, ScriptPackagePlaySession):
        model.__table__.to_metadata(metadata)
    Table("script_package_releases", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False)
    db = factory()
    for identifier in (1, 2):
        db.add(User(id=identifier, username=f"preview-{identifier}", email=f"preview-{identifier}@example.invalid",
                    hashed_password="SYNTHETIC_NOT_A_PASSWORD", is_active=True))
    db.commit()
    publisher = FixturePublisher()
    value = SimpleNamespace(db=db, factory=factory, publisher=publisher,
                            service=PackageRuntimeService(db, publisher))
    yield value
    db.close()
    engine.dispose()


def opening_package(version="v1", factory=fictional_package):
    package = factory()
    package["content_version"] = version
    package["phases"][1]["title"] = "LATER_PHASE_PRIVATE_SENTINEL"
    reference = deepcopy(package["knowledge"][0]["sources"])
    package["knowledge"].extend([
        {"id": "public-claim", "text": "PUBLIC_CLAIM_SENTINEL", "kind": "CLAIM",
         "visibility": "PUBLIC", "character_id": None, "release": {"phase_id": "opening"},
         "disclosure": "PUBLIC", "sources": reference},
        {"id": "gated-public", "text": "GATED_INITIAL_SENTINEL", "kind": "INFERENCE",
         "visibility": "PUBLIC", "character_id": None,
         "release": {"phase_id": "opening", "required_public_evidence_ids": ["clock"]},
         "disclosure": "PUBLIC", "sources": reference},
        {"id": "later-a", "text": "LATER_a_SENTINEL", "kind": "FACT",
         "visibility": "CHARACTER_PRIVATE", "character_id": "a", "release": {"phase_id": "ending"},
         "disclosure": "MAY_SHARE", "sources": reference},
    ])
    package["evidence"].append({"id": "public-card", "text": "PUBLIC_EVIDENCE_SENTINEL",
                                "visibility": "PUBLIC", "character_id": None,
                                "release": {"phase_id": "opening", "required_public_evidence_ids": []},
                                "disclosure": "PUBLIC", "sources": reference})
    package["settlement"]["instructions"]["text"] = "SETTLEMENT_PRIVATE_SENTINEL"
    assert validate_package(package)["valid"]
    return package


def publish(runtime, document=None):
    document = document or opening_package()
    result = PackageImportService(runtime.db).submit(document, submitted_by=1,
                                                    idempotency_key=document["content_version"])
    identifier = len(runtime.publisher.records) + 1
    release = {"id": identifier, "version_id": result["version_id"], "package_hash": content_hash(document),
               "title": document["title"], "content_version": document["content_version"],
               "player_count": document["player_count"], "private_review": "PRIVATE_AUDIT_SENTINEL"}
    release["release_hash"] = content_hash(release)
    runtime.db.execute(text("INSERT INTO script_package_releases (id) VALUES (:id)"), {"id": identifier})
    runtime.db.commit()
    runtime.publisher.records[identifier] = release
    runtime.publisher.current.add(identifier)
    return release


def request(release=1, character="a", key="opening-request"):
    return {"release_id": release, "character_id": character, "idempotency_key": key}


@pytest.mark.parametrize("factory", [fictional_package, fictional_package_v11])
def test_package_runtime_exact_opening_projection_and_reload(runtime, factory):
    release = publish(runtime, opening_package(factory=factory))
    result = runtime.service.create(request(), 1)
    runtime.db.commit()
    assert result["runtime_ready"] is False and result["status"] == "READING_PREVIEW"
    assert result["available_actions"] == []
    assert result["package_hash"] == release["package_hash"]
    assert result["public_knowledge"] == [{"id": "public-claim", "text": "PUBLIC_CLAIM_SENTINEL",
                                            "kind": "CLAIM", "disclosure": "PUBLIC"}]
    assert result["private_knowledge"] == [{"id": "memory-a", "text": "PRIVATE_a_SENTINEL",
                                             "kind": "FACT", "disclosure": "MAY_SHARE"}]
    assert [item["id"] for item in result["private_evidence"]] == ["clock"]
    assert [item["id"] for item in result["public_evidence"]] == ["public-card"]
    raw = canonical_json(result)
    for forbidden in ("PRIVATE_b_SENTINEL", "SYSTEM_TRUTH_SENTINEL", "LATER_a_SENTINEL",
                      "GATED_INITIAL_SENTINEL", "LATER_PHASE_PRIVATE_SENTINEL", "SETTLEMENT_PRIVATE_SENTINEL",
                      "PRIVATE_AUDIT_SENTINEL", "relative_path", "sources", "fixture.pdf", "fixture.md"):
        assert forbidden not in raw
    with runtime.factory() as fresh:
        restored = PackageRuntimeService(fresh, runtime.publisher).get(result["session_id"], 1)
        assert restored == result


def test_package_runtime_each_owner_role_is_separate_and_cannot_switch(runtime):
    publish(runtime)
    a = runtime.service.create(request(), 1)
    b = runtime.service.create(request(character="b"), 2)
    assert a["session_id"] != b["session_id"]
    assert "PRIVATE_a_SENTINEL" not in canonical_json(b)
    assert "PRIVATE_b_SENTINEL" in canonical_json(b) and b["private_evidence"] == []
    with pytest.raises(PackageRuntimeError, match="PACKAGE_SESSION_KEY_CONFLICT"):
        runtime.service.create(request(character="b"), 1)
    with pytest.raises(PackageRuntimeError) as caught:
        runtime.service.get(a["session_id"], 2)
    assert caught.value.status_code == 404
    assert runtime.service.get(a["session_id"], 1) == a


def test_package_runtime_catalog_is_only_safe_metadata_and_names(runtime):
    release = publish(runtime)
    assert runtime.service.list_releases(1) == [{
        "id": release["id"], "version_id": release["version_id"], "title": release["title"],
        "content_version": "v1", "player_count": 2,
        "characters": [{"id": "a", "name": "值班员甲"}, {"id": "b", "name": "值班员乙"}],
        "status": "READING_PREVIEW", "runtime_ready": False}]
    runtime.publisher.current.clear()
    assert runtime.service.list_releases(1) == []


def test_package_runtime_new_release_and_withdrawal_never_rebind_existing_preview(runtime):
    publish(runtime)
    first = runtime.service.create(request(), 1)
    runtime.db.commit()
    second_document = opening_package("v2")
    second_document["knowledge"][0]["text"] = "NEW_VERSION_a_SENTINEL"
    second = publish(runtime, second_document)
    runtime.publisher.current.remove(1)
    assert runtime.service.get(first["session_id"], 1) == first
    assert runtime.service.create(request(), 1) == first
    with pytest.raises(PackageRuntimeError) as caught:
        runtime.service.create(request(key="new-on-withdrawn"), 1)
    assert caught.value.status_code == 404
    new = runtime.service.create(request(release=second["id"], key="new-on-v2"), 1)
    assert new["version_id"] != first["version_id"]
    assert "NEW_VERSION_a_SENTINEL" in canonical_json(new)
    assert runtime.publisher.reads[-2:] == [(second["id"], True), (second["id"], False)]


@pytest.mark.parametrize("body", [request(release=999), request(character="not-a-role")])
def test_package_runtime_unknown_release_or_role_has_no_session(runtime, body):
    publish(runtime)
    with pytest.raises(PackageRuntimeError) as caught:
        runtime.service.create(body, 1)
    assert caught.value.status_code == 404
    assert runtime.db.query(ScriptPackagePlaySession).count() == 0


@pytest.mark.parametrize("patch", [{"release_id": True}, {"release_id": "1"}, {"release_id": -1},
                                   {"character_id": "../a"}, {"idempotency_key": ""},
                                   {"phase_id": "ending"}, {"owner_user_id": 2}])
def test_package_runtime_strict_request_cannot_change_authority(runtime, patch):
    with pytest.raises(PackageRuntimeError) as caught:
        runtime.service.create(request() | patch, 1)
    assert caught.value.status_code == 422
    assert runtime.publisher.reads == []


@pytest.mark.parametrize("field,value", [("selected_character_id", "b"), ("owner_user_id", 2),
                                         ("version_id", 999), ("package_hash", "0" * 64),
                                         ("binding_json", '{"PRIVATE_CORRUPTION":true}')])
def test_package_runtime_sql_binding_tampering_fails_closed(runtime, field, value):
    publish(runtime)
    result = runtime.service.create(request(), 1)
    runtime.db.commit()
    with runtime.factory.begin() as fresh:
        # version FK is checked independently; other tampering is read-rejected.
        if field == "version_id":
            with pytest.raises(IntegrityError):
                fresh.execute(update(ScriptPackagePlaySession).values({field: value}))
            return
        fresh.execute(update(ScriptPackagePlaySession).values({field: value}))
    with pytest.raises(PackageRuntimeError, match="PACKAGE_RUNTIME_SNAPSHOT_INVALID"):
        runtime.service.get(result["session_id"], 2 if field == "owner_user_id" else 1)


@pytest.mark.parametrize("field", ["package", "release"])
def test_package_runtime_rejects_changed_immutable_inputs(runtime, field):
    publish(runtime)
    result = runtime.service.create(request(), 1)
    runtime.db.commit()
    if field == "release":
        runtime.publisher.records[1]["release_hash"] = "9" * 64
    else:
        with runtime.factory.begin() as db:
            row = db.get(ScriptPackageVersion, result["version_id"])
            document = json.loads(row.package_json)
            document["knowledge"][0]["text"] = "TAMPERED_PRIVATE_BODY"
            db.execute(update(ScriptPackageVersion).where(ScriptPackageVersion.id == row.id)
                       .values(package_json=canonical_json(document)))
    with pytest.raises(PackageRuntimeError, match="PACKAGE_RUNTIME_SNAPSHOT_INVALID"):
        runtime.service.get(result["session_id"], 1)


@pytest.mark.parametrize("operation", ["update", "delete"])
def test_package_runtime_orm_binding_is_immutable(runtime, operation):
    publish(runtime)
    runtime.service.create(request(), 1)
    runtime.db.commit()
    row = runtime.db.query(ScriptPackagePlaySession).one()
    if operation == "update":
        row.selected_character_id = "b"
    else:
        runtime.db.delete(row)
    with pytest.raises(ValueError, match="PACKAGE_PLAY_BINDING_IMMUTABLE"):
        runtime.db.flush()
    runtime.db.rollback()


def test_package_runtime_outer_rollback_leaves_no_binding(runtime):
    publish(runtime)
    runtime.service.create(request(), 1)
    runtime.db.rollback()
    with runtime.factory() as fresh:
        assert fresh.query(ScriptPackagePlaySession).count() == 0


def test_package_runtime_failed_projection_rolls_back_insert(runtime, monkeypatch):
    publish(runtime)
    original = runtime.publisher.get_release
    def fail_historical(release_id, *, require_current=True):
        if not require_current:
            raise ValueError("PRIVATE_REPORT_DETAILS")
        return original(release_id, require_current=require_current)
    monkeypatch.setattr(runtime.publisher, "get_release", fail_historical)
    with pytest.raises(PackageRuntimeError, match="PACKAGE_RUNTIME_SNAPSHOT_INVALID"):
        runtime.service.create(request(), 1)
    runtime.db.commit()
    assert runtime.db.query(ScriptPackagePlaySession).count() == 0


@pytest.mark.parametrize("conflict", [False, True])
def test_package_runtime_unique_race_uses_existing_binding_without_duplication(runtime, monkeypatch, conflict):
    publish(runtime)
    first = runtime.service.create(request(), 1)
    runtime.db.commit()
    actual = runtime.service._existing
    calls = 0
    def miss_first(owner, key, digest):
        nonlocal calls
        calls += 1
        return None if calls == 1 else actual(owner, key, digest)
    monkeypatch.setattr(runtime.service, "_existing", miss_first)
    if conflict:
        with pytest.raises(PackageRuntimeError, match="PACKAGE_SESSION_KEY_CONFLICT"):
            runtime.service.create(request(character="b"), 1)
    else:
        assert runtime.service.create(request(), 1) == first
    assert calls == 2 and runtime.db.query(ScriptPackagePlaySession).count() == 1


@pytest.fixture
def client(runtime):
    app = FastAPI()
    app.include_router(package_runtime_routes.router)
    app.dependency_overrides[package_runtime_routes.runtime_service] = lambda: runtime.service
    @app.middleware("http")
    async def fake_identity(request, call_next):
        identity = request.headers.get("Authorization")
        if identity in ("player-1", "player-2", "disabled"):
            request.state.current_user = SimpleNamespace(id=2 if identity == "player-2" else 1,
                                                 is_active=identity != "disabled", is_admin=False)
        return await call_next(request)
    with TestClient(app) as value:
        yield value


@pytest.mark.parametrize("token,status", [(None, 401), ("disabled", 403)])
def test_package_runtime_http_requires_active_user_even_without_unified_middleware(client, token, status):
    response = client.get("/api/fusion/package-releases", headers={"Authorization": token} if token else {})
    assert response.status_code == status and response.headers["Cache-Control"] == "no-store"


def test_package_runtime_http_create_read_catalog_and_owner_404(client, runtime):
    publish(runtime)
    headers = {"Authorization": "player-1"}
    catalog = client.get("/api/fusion/package-releases", headers=headers)
    assert catalog.status_code == 200 and len(catalog.json()["data"]) == 1
    created = client.post("/api/fusion/package-sessions", json=request(), headers=headers)
    assert created.status_code == 201 and created.headers["Cache-Control"] == "no-store"
    result = created.json()["data"]
    url = "/api/fusion/package-sessions/" + result["session_id"]
    assert client.get(url, headers=headers).json()["data"] == result
    denied = client.get(url, headers={"Authorization": "player-2"})
    assert denied.status_code == 404 and "PRIVATE_" not in denied.text
    assert denied.headers["Cache-Control"] == "no-store"
    assert client.post(url + "/actions", json={"type": "advance_phase"}, headers=headers).status_code == 404


@pytest.mark.parametrize("payload,status", [('{"release_id":1,"character_id":"PRIVATE_INVALID/ID","idempotency_key":"k"}', 422),
                                           ('{"release_id":1,"release_id":2}', 422),
                                           ('{"PRIVATE_EXTRA":"secret"}', 422),
                                           ('{"private":"' + 'S' * 5000 + '"}', 413)],
                         ids=["invalid-role", "duplicate-key", "extra-field", "oversize"])
def test_package_runtime_http_invalid_body_is_safe_and_not_cached(client, payload, status):
    response = client.post("/api/fusion/package-sessions", content=payload, headers={"Authorization": "player-1"})
    assert response.status_code == status and response.headers["Cache-Control"] == "no-store"
    assert "PRIVATE_" not in response.text and "secret" not in response.text


def test_package_runtime_bad_session_path_is_safe_404(client):
    response = client.get("/api/fusion/package-sessions/PRIVATE_INVALID_PATH", headers={"Authorization": "player-1"})
    assert response.status_code == 404 and "PRIVATE_INVALID_PATH" not in response.text
    assert response.headers["Cache-Control"] == "no-store"


def test_package_runtime_real_publisher_binds_once_and_new_review_expires_new_sessions(workflow):
    from src.db.models.script_publication import ScriptPackageRelease, ScriptPublicationApproval
    from src.db.models.script_review import ScriptFindingDisposition
    from src.fusion.script_publication import ScriptPublicationService
    from src.fusion.script_review import ScriptReviewService
    from src.schemas.script_publication import ApprovePublicationRequest, PublishPackageRequest
    from src.schemas.script_review import SubmitAuditRequest

    engine = workflow.factory.kw["bind"]
    for model in (ScriptFindingDisposition, ScriptPublicationApproval, ScriptPackageRelease, ScriptPackagePlaySession):
        model.__table__.create(engine)
    completed = run(workflow)
    assert completed["state"] == "COMPLETED" and workflow.sdk.chat_completion.await_count == 2
    version_id = completed["candidate_version_id"]
    with workflow.factory() as db:
        review = ScriptReviewService(db)
        raw = {"idempotency_key": "complete-human-review", "expected_package_hash": content_hash(workflow.package),
               "bundle_hash": workflow.request.bundle_hash,
               "report": {"schema_version": "script-audit/1.0", "summary": "Fictional test review, never a real approval.",
                          "coverage": ["PROVENANCE", "TIMELINE", "EVIDENCE", "KNOWLEDGE_BOUNDARY", "PLAYABILITY"],
                          "findings": []}}
        body = SubmitAuditRequest.model_validate(raw)
        _, package = review.prepare_audit(version_id, body, 1)
        verified = workflow.sources.verify(workflow.request.bundle_hash, document=package)
        review.save_audit(version_id, body, 1, verified, workflow.sources)
        db.commit()
        publisher = ScriptPublicationService(db, workflow.sources)
        state = publisher.gate_state(version_id, workflow.sources)
        assert state["can_approve"]
        approval_body = ApprovePublicationRequest.model_validate({
            "idempotency_key": "fixture-approval", "expected_package_hash": content_hash(workflow.package),
            "bundle_hash": workflow.request.bundle_hash, "expected_basis_hash": state["basis_hash"],
            "model_dispositions": [{"job_id": completed["id"], "finding_id": "f1", "status": "ACKNOWLEDGED",
                                     "note": "Synthetic fixture only; test the exact reviewed finding."}],
            "note": "Synthetic integration test authorization only."})
        approval = publisher.approve(version_id, approval_body, 1, workflow.sources)
        db.commit()
        release = publisher.publish(version_id, PublishPackageRequest.model_validate({
            "idempotency_key": "fixture-release", "approval_id": approval["id"],
            "expected_approval_hash": approval["approval_hash"], "expected_basis_hash": approval["basis_hash"]}),
            1, workflow.sources)
        db.commit()
        runtime = PackageRuntimeService(db, publisher)
        catalog = runtime.list_releases(1)
        assert catalog[0]["characters"] == [{"id": item["id"], "name": item["name"]} for item in package["characters"]]
        first = runtime.create(request(release["id"], package["characters"][0]["id"]), 1)
        db.commit()
        assert first["package_hash"] == release["package_hash"] and first["version_id"] == version_id
        before = publisher.get_release(release["id"], require_current=False)
        raw["idempotency_key"] = "later-human-review"
        body = SubmitAuditRequest.model_validate(raw)
        _, package = review.prepare_audit(version_id, body, 1)
        verified = workflow.sources.verify(workflow.request.bundle_hash, document=package)
        review.save_audit(version_id, body, 1, verified, workflow.sources)
        db.commit()
        assert runtime.list_releases(1) == []
        with pytest.raises(PackageRuntimeError, match="PACKAGE_RELEASE_UNAVAILABLE"):
            runtime.create(request(release["id"], package["characters"][0]["id"], "after-review-change"), 1)
        assert runtime.get(first["session_id"], 1) == first
        assert publisher.get_release(release["id"], require_current=False) == before
        db.commit()
    with workflow.factory() as fresh:
        runtime = PackageRuntimeService(fresh, ScriptPublicationService(fresh, workflow.sources))
        restored = runtime.get(first["session_id"], 1)
        assert restored == first
        # Keep ORM rows cached, then change raw storage without synchronizing
        # the identity map. Historical reads must still recheck real hashes.
        for model, identifier, field in ((ScriptPackageRelease, release["id"], "release_json"),
                                          (ScriptPublicationApproval, approval["id"], "approval_json")):
            held = fresh.get(model, identifier)
            assert held is not None
            fresh.execute(update(model).where(model.id == identifier).values(
                {field: '{"PRIVATE_TAMPERED_RECORD":true}'}).execution_options(synchronize_session=False))
            with pytest.raises(PackageRuntimeError, match="PACKAGE_RUNTIME_SNAPSHOT_INVALID"):
                runtime.get(first["session_id"], 1)
            fresh.rollback()
            assert runtime.get(first["session_id"], 1) == first
    assert workflow.sdk.chat_completion.await_count == 2
