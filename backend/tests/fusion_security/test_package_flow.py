"""Persistent rules previews over fictional packages and isolated SQLite files."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from threading import Barrier
from types import SimpleNamespace

import pytest
from sqlalchemy import Column, Integer, MetaData, Table, create_engine, event, text, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import sessionmaker

from src.db.models import ScriptImportJob, ScriptPackageVersion, User
from src.db.models.package_flow import ScriptPackageFlow, ScriptPackageFlowAction
from src.db.models.package_runtime import ScriptPackagePlaySession
from src.fusion.package_flow import PackageFlowError, PackageFlowService
from src.fusion.package_runtime import PackageRuntimeService
from src.fusion.package_validation import canonical_json, content_hash
from src.schemas.package_flow import PackageFlowActionRequest
from tests.fusion_security.test_package_runtime import FixturePublisher, opening_package, publish, request, runtime
from tests.fusion_security.test_script_publication import add_manual_report, publication_case, publish_case


@pytest.fixture
def flow(runtime):
    ScriptPackageFlow.__table__.create(runtime.db.get_bind())
    ScriptPackageFlowAction.__table__.create(runtime.db.get_bind())
    runtime.flow = PackageFlowService(runtime.db, runtime.publisher)
    return runtime


@pytest.fixture
def native_flow(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "native.sqlite"),
                           connect_args={"check_same_thread": False, "timeout": 2})

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    metadata = MetaData()
    for model in (User, ScriptPackageVersion, ScriptImportJob, ScriptPackagePlaySession,
                  ScriptPackageFlow, ScriptPackageFlowAction):
        model.__table__.to_metadata(metadata)
    Table("script_package_releases", metadata, Column("id", Integer, primary_key=True))
    metadata.create_all(engine)
    factory = sessionmaker(engine, autoflush=False)
    db = factory()
    db.add(User(id=1, username="flow-fixture", email="flow@example.invalid",
                hashed_password="SYNTHETIC", is_active=True))
    db.commit()
    publisher = FixturePublisher()
    result = SimpleNamespace(db=db, factory=factory, publisher=publisher,
                             service=PackageRuntimeService(db, publisher),
                             flow=PackageFlowService(db, publisher))
    yield result
    db.close()
    engine.dispose()


def create_body(opening, key="flow-create"):
    return {"opening_session_id": opening["session_id"], "idempotency_key": key}


def command(revision=0, key="share-clock", action="SHARE_MATERIAL", target=None):
    result = {"expected_revision": revision, "idempotency_key": key, "action": action}
    if action == "SHARE_MATERIAL":
        result["target"] = target or {"collection": "evidence", "id": "clock"}
    return result


def start(flow, document=None):
    publish(flow, document)
    opening = flow.service.create(request(), 1)
    flow.db.commit()
    result = flow.flow.create(create_body(opening), 1)
    flow.db.commit()
    return opening, result


def test_package_flow_chain_reload_fixed_views_and_no_hidden_payload(flow):
    opening, initial = start(flow)
    shared = flow.flow.act(initial["flow_id"], command(), 1)
    flow.db.commit()
    assert shared["revision"] == 1
    assert [item["id"] for item in shared["public_knowledge"]] == ["public-claim", "gated-public"]
    card = next(item for item in shared["public_evidence"] if item["id"] == "clock")
    assert card["shared_by_character_id"] == "a" and card["can_share"] is False
    assert shared["private_evidence"] == []
    final = flow.flow.act(initial["flow_id"], command(1, "advance", "ADVANCE_PHASE"), 1)
    flow.db.commit()
    assert final["revision"] == 2 and final["phase_complete"] is True
    assert final["runtime_ready"] is False and final["status"] == "RULES_PREVIEW"
    assert final["current_phase"]["id"] == "ending"
    assert "LATER_a_SENTINEL" in canonical_json(final)
    assert flow.service.get(opening["session_id"], 1) == opening
    with flow.factory() as db:
        service = PackageFlowService(db, flow.publisher)
        assert service.get(initial["flow_id"], 1) == final
        assert service.find_for_opening(opening["session_id"], 1) == final
        assert service.act(initial["flow_id"], command(), 1) == shared
    raw = canonical_json(final)
    for forbidden in ("PRIVATE_b_SENTINEL", "SYSTEM_TRUTH_SENTINEL", "SETTLEMENT_PRIVATE_SENTINEL",
                      "PRIVATE_AUDIT_SENTINEL", "sources", "relative_path", "state_hash", "request_hash"):
        assert forbidden not in raw
    events = flow.db.query(ScriptPackageFlowAction).order_by(ScriptPackageFlowAction.revision).all()
    assert len(events) == 2 and events[1].previous_event_hash == events[0].event_hash
    persisted = "".join(item.event_json + item.request_json for item in events)
    assert "SENTINEL" not in persisted and "text" not in persisted and "sources" not in persisted


def test_package_flow_explicit_creation_closure_never_upgrades_opening(flow):
    package = opening_package()
    package["knowledge"][3]["release"]["required_public_evidence_ids"] = ["public-card"]
    opening, initial = start(flow, package)
    assert "GATED_INITIAL_SENTINEL" not in canonical_json(opening)
    assert "GATED_INITIAL_SENTINEL" in canonical_json(initial)
    assert flow.service.get(opening["session_id"], 1) == opening


def test_package_flow_creation_idempotence_and_opening_lookup(flow):
    publish(flow)
    opening = flow.service.create(request(), 1)
    flow.db.commit()
    assert flow.flow.find_for_opening(opening["session_id"], 1) is None
    initial = flow.flow.create(create_body(opening), 1)
    flow.db.commit()
    assert flow.flow.create(create_body(opening), 1) == initial
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_OPENING_CONFLICT"):
        flow.flow.create(create_body(opening, "different-key"), 1)
    another = flow.service.create(request(key="different-opening"), 1)
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_KEY_CONFLICT"):
        flow.flow.create(create_body(another), 1)
    assert flow.db.query(ScriptPackageFlow).count() == 1


def test_package_flow_expired_release_freezes_new_grants_not_history_or_exact_retries(flow):
    opening, initial = start(flow)
    result = flow.flow.act(initial["flow_id"], command(), 1)
    flow.db.commit()
    another = flow.service.create(request(key="unstarted-opening"), 1)
    flow.db.commit()
    flow.publisher.current.clear()
    flow.publisher.reads.clear()
    assert flow.flow.get(initial["flow_id"], 1) == result
    assert flow.flow.act(initial["flow_id"], command(), 1) == result
    assert flow.flow.create(create_body(opening), 1) == result
    assert not any(current for _, current in flow.publisher.reads)
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_RELEASE_UNAVAILABLE"):
        flow.flow.act(initial["flow_id"], command(1, "advance", "ADVANCE_PHASE"), 1)
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_RELEASE_UNAVAILABLE"):
        flow.flow.create(create_body(another, "start-later"), 1)
    assert flow.flow.get(initial["flow_id"], 1) == result


def test_package_flow_actor_and_object_permissions(flow):
    opening, initial = start(flow)
    for call in (lambda: flow.flow.get(initial["flow_id"], 2),
                 lambda: flow.flow.act(initial["flow_id"], command(), 2),
                 lambda: flow.flow.find_for_opening(opening["session_id"], 2),
                 lambda: flow.flow.create(create_body(opening), 2),
                 lambda: flow.flow.get("../PRIVATE_SENTINEL", 1)):
        with pytest.raises(PackageFlowError) as caught:
            call()
        assert caught.value.status_code == 404 and "SENTINEL" not in str(caught.value)
    for owner in (None, 0, True, "1"):
        with pytest.raises(PackageFlowError) as caught:
            flow.flow.get(initial["flow_id"], owner)
        assert caught.value.status_code == 401


@pytest.mark.parametrize("patch", [
    {"owner_user_id": 2}, {"phase_id": "ending"}, {"expected_revision": True},
    {"expected_revision": "0"}, {"target": {"collection": "truth", "id": "answer"}},
    {"target": {"collection": "evidence", "id": "clock", "text": "PRIVATE_SENTINEL"}},
    {"action": "ADVANCE_PHASE", "target": None},
])
def test_package_flow_strict_action_body_rejects_authority_injection(flow, patch):
    _, initial = start(flow)
    with pytest.raises(PackageFlowError) as caught:
        flow.flow.act(initial["flow_id"], command() | patch, 1)
    assert caught.value.status_code == 422 and "SENTINEL" not in str(caught.value)
    assert flow.db.query(ScriptPackageFlowAction).count() == 0


def test_package_flow_schema_object_and_revision_conflicts(flow):
    _, initial = start(flow)
    body = PackageFlowActionRequest.model_validate(command(action="ADVANCE_PHASE"))
    result = flow.flow.act(initial["flow_id"], body, 1)
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_KEY_CONFLICT"):
        flow.flow.act(initial["flow_id"], command(), 1)
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_REVISION_CONFLICT"):
        flow.flow.act(initial["flow_id"], command(key="stale"), 1)
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_PHASE_COMPLETE"):
        flow.flow.act(initial["flow_id"], command(1, "twice", "ADVANCE_PHASE"), 1)
    assert flow.flow.get(initial["flow_id"], 1) == result
    assert flow.db.query(ScriptPackageFlowAction).count() == 1


def test_package_flow_invalid_share_preserves_history_and_safe_error(flow):
    _, initial = start(flow)
    for target in ({"collection": "knowledge", "id": "memory-b"},
                   {"collection": "knowledge", "id": "later-a"},
                   {"collection": "evidence", "id": "public-card"},
                   {"collection": "knowledge", "id": "PRIVATE_SENTINEL"}):
        with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_MATERIAL_NOT_SHAREABLE"):
            flow.flow.act(initial["flow_id"], command(target=target), 1)
    assert flow.flow.get(initial["flow_id"], 1) == initial
    assert flow.db.query(ScriptPackageFlowAction).count() == 0


@pytest.mark.parametrize("field,value", [("selected_character_id", "b"), ("binding_hash", "0" * 64),
                                        ("request_hash", "0" * 64), ("binding_json", '{"text":"PRIVATE_SENTINEL"}')])
def test_package_flow_cached_binding_tampering_rejected(flow, field, value):
    _, initial = start(flow)
    cached = flow.db.query(ScriptPackageFlow).one()
    flow.db.execute(update(ScriptPackageFlow).values({field: value}).execution_options(synchronize_session=False))
    assert getattr(cached, field) != value
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_SNAPSHOT_INVALID"):
        flow.flow.get(initial["flow_id"], 1)


@pytest.mark.parametrize("field,value", [("request_json", '{"text":"PRIVATE_SENTINEL"}'),
                                        ("request_hash", "0" * 64), ("previous_event_hash", "0" * 64),
                                        ("state_hash", "0" * 64), ("event_hash", "0" * 64),
                                        ("event_json", '{"text":"PRIVATE_SENTINEL"}'), ("revision", 4)])
def test_package_flow_cached_action_tampering_rejected(flow, field, value):
    _, initial = start(flow)
    flow.flow.act(initial["flow_id"], command(), 1)
    flow.db.commit()
    cached = flow.db.query(ScriptPackageFlowAction).one()
    flow.db.execute(update(ScriptPackageFlowAction).values({field: value}).execution_options(synchronize_session=False))
    assert getattr(cached, field) != value
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_HISTORY_INVALID"):
        flow.flow.get(initial["flow_id"], 1)
    with pytest.raises(PackageFlowError):
        flow.flow.act(initial["flow_id"], command(), 1)


def test_package_flow_rehashed_invalid_state_or_request_is_not_authority(flow):
    _, initial = start(flow)
    flow.flow.act(initial["flow_id"], command(), 1)
    flow.db.commit()
    row = flow.db.query(ScriptPackageFlowAction).one()
    payload = json.loads(row.event_json)
    payload["state_hash"] = "0" * 64
    flow.db.execute(update(ScriptPackageFlowAction).values(state_hash="0" * 64,
                    event_json=canonical_json(payload), event_hash=content_hash(payload)))
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_HISTORY_INVALID"):
        flow.flow.get(initial["flow_id"], 1)
    flow.db.rollback()
    row = flow.db.query(ScriptPackageFlowAction).one()
    bad = command(target={"collection": "knowledge", "id": "memory-b"})
    payload = json.loads(row.event_json)
    payload["request_hash"] = content_hash(bad)
    flow.db.execute(update(ScriptPackageFlowAction).values(request_json=canonical_json(bad),
                    request_hash=content_hash(bad), event_json=canonical_json(payload), event_hash=content_hash(payload)))
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_HISTORY_INVALID"):
        flow.flow.get(initial["flow_id"], 1)


def test_package_flow_middle_event_deletion_and_history_after_retry_are_checked(flow):
    _, initial = start(flow)
    flow.flow.act(initial["flow_id"], command(), 1)
    flow.flow.act(initial["flow_id"], command(1, "advance", "ADVANCE_PHASE"), 1)
    flow.db.commit()
    # Exact retries validate later receipts too; a bad later event cannot be
    # silently ignored while returning the earlier event's valid projection.
    flow.db.execute(text("UPDATE script_package_flow_actions SET state_hash=:bad WHERE revision=2"), {"bad": "0" * 64})
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_HISTORY_INVALID"):
        flow.flow.act(initial["flow_id"], command(), 1)
    flow.db.rollback()
    flow.db.execute(text("DELETE FROM script_package_flow_actions WHERE revision=1"))
    with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_HISTORY_INVALID"):
        flow.flow.get(initial["flow_id"], 1)


@pytest.mark.parametrize("model", [ScriptPackageFlow, ScriptPackageFlowAction])
def test_package_flow_orm_records_cannot_change_or_delete(flow, model):
    _, initial = start(flow)
    flow.flow.act(initial["flow_id"], command(), 1)
    flow.db.commit()
    row = flow.db.query(model).one()
    row.idempotency_key = "changed"
    with pytest.raises(ValueError, match="PACKAGE_FLOW_RECORD_IMMUTABLE"):
        flow.db.flush()
    flow.db.rollback()
    flow.db.delete(flow.db.query(model).one())
    with pytest.raises(ValueError, match="PACKAGE_FLOW_RECORD_IMMUTABLE"):
        flow.db.flush()
    flow.db.rollback()


def test_package_flow_native_sqlite_outer_rollback_covers_create_and_action(native_flow):
    flow = native_flow
    publish(flow)
    opening = flow.service.create(request(), 1)
    flow.db.commit()
    flow.flow.create(create_body(opening), 1)
    flow.db.rollback()
    with flow.factory() as db:
        assert db.query(ScriptPackageFlow).count() == 0
    initial = flow.flow.create(create_body(opening), 1)
    flow.db.commit()
    flow.flow.act(initial["flow_id"], command(), 1)
    flow.db.rollback()
    with flow.factory() as db:
        assert db.query(ScriptPackageFlowAction).count() == 0
        assert PackageFlowService(db, flow.publisher).get(initial["flow_id"], 1) == initial


def test_package_flow_projection_failure_rolls_back_whole_receipt(flow, monkeypatch):
    _, initial = start(flow)
    def fail(*args):
        raise RuntimeError("SYNTHETIC_FAILURE")
    with monkeypatch.context() as patch:
        patch.setattr(flow.flow, "_view", fail)
        with pytest.raises(RuntimeError, match="SYNTHETIC_FAILURE"):
            flow.flow.act(initial["flow_id"], command(), 1)
    assert flow.db.query(ScriptPackageFlowAction).count() == 0
    flow.db.commit()
    assert flow.flow.get(initial["flow_id"], 1) == initial


def test_package_flow_sql_unique_revision_and_key_constraints(flow):
    _, initial = start(flow)
    flow.flow.act(initial["flow_id"], command(), 1)
    flow.db.commit()
    row = flow.db.query(ScriptPackageFlowAction).one()
    values = {column.name: getattr(row, column.name) for column in row.__table__.columns
              if column.name not in {"id", "created_at", "updated_at"}}
    for changes in ({"idempotency_key": "another"}, {"revision": 2}):
        with pytest.raises(IntegrityError), flow.db.begin_nested():
            flow.db.execute(ScriptPackageFlowAction.__table__.insert().values(values | changes))
    assert flow.db.query(ScriptPackageFlowAction).count() == 1


@pytest.mark.parametrize("same_key", [False, True])
def test_package_flow_file_sqlite_concurrent_revision_has_one_winner(native_flow, same_key):
    flow = native_flow
    _, initial = start(flow)
    barrier = Barrier(2)

    def attempt(key):
        with flow.factory() as db:
            publisher = FixturePublisher()
            publisher.records = deepcopy(flow.publisher.records)
            publisher.current = set(flow.publisher.current)
            original = publisher.get_release
            def fenced(release_id, *, require_current=True):
                result = original(release_id, require_current=require_current)
                if require_current:
                    barrier.wait(timeout=5)
                return result
            publisher.get_release = fenced
            try:
                result = PackageFlowService(db, publisher).act(initial["flow_id"], command(key=key), 1)
                db.commit()
                return result["revision"]
            except PackageFlowError as exc:
                db.rollback()
                return exc.code
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["concurrent-a", "concurrent-a" if same_key else "concurrent-b"]))
    if same_key:
        assert results == [1, 1]
    else:
        assert results.count(1) == 1
        assert next(result for result in results if result != 1) in {"PACKAGE_FLOW_REVISION_CONFLICT", "PACKAGE_FLOW_WRITE_CONFLICT"}
    flow.db.rollback()
    assert flow.db.query(ScriptPackageFlowAction).count() == 1
    assert flow.flow.get(initial["flow_id"], 1)["revision"] == 1


def test_package_flow_publisher_lock_failure_is_safe(flow, monkeypatch):
    _, initial = start(flow)
    original = flow.publisher.get_release
    def broken(release_id, *, require_current=True):
        if require_current:
            raise OperationalError("SELECT PRIVATE_DATABASE_SENTINEL", {}, Exception("PRIVATE_SQL_DETAILS"))
        return original(release_id, require_current=False)
    monkeypatch.setattr(flow.publisher, "get_release", broken)
    with pytest.raises(PackageFlowError) as caught:
        flow.flow.act(initial["flow_id"], command(), 1)
    assert caught.value.code == "PACKAGE_FLOW_WRITE_CONFLICT"
    assert "PRIVATE" not in str(caught.value)
    assert flow.db.query(ScriptPackageFlowAction).count() == 0


@pytest.mark.parametrize("expiration", ["review", "source"])
def test_package_flow_real_publisher_freezes_new_grants_preserves_fixed_history(publication_case, expiration):
    from src.fusion.script_publication import ScriptPublicationService
    case = publication_case
    release = publish_case(case)
    character = case.package["characters"][0]["id"]
    with case.sessions() as db:
        publisher = ScriptPublicationService(db, case.sources)
        runtime_service = PackageRuntimeService(db, publisher)
        service = PackageFlowService(db, publisher)
        opening = runtime_service.create(request(release["id"], character), 1)
        unused_opening = runtime_service.create(request(release["id"], character, "unused-opening"), 1)
        db.commit()
        initial = service.create(create_body(opening), 1)
        db.commit()
        advance = command(0, "real-publisher-advance", "ADVANCE_PHASE")
        final = service.act(initial["flow_id"], advance, 1)
        db.commit()
        assert final["revision"] == 1 and final["phase_complete"] is True
        assert final["release_id"] == release["id"] and final["version_id"] == case.version_id
    charged = case.jobs.get(case.job["id"])["charged_cost_cny"]
    if expiration == "review":
        add_manual_report(case, key="review-after-rules-start")
    else:
        # Only the isolated fixture's own synthetic source is modified.
        source = case.sources.root / case.request.bundle_hash / "files" / "fixture.md"
        source.write_text("SYNTHETIC_INVALID_SOURCE_SENTINEL", encoding="utf-8")
    with case.sessions() as db:
        publisher = ScriptPublicationService(db, case.sources)
        service = PackageFlowService(db, publisher)
        assert service.get(initial["flow_id"], 1) == final
        assert service.act(initial["flow_id"], advance, 1) == final
        assert service.create(create_body(opening), 1) == final
        assert service.find_for_opening(opening["session_id"], 1) == final
        assert PackageRuntimeService(db, publisher).get(opening["session_id"], 1) == opening
        own = next(item for item in final["private_knowledge"] if item["can_share"])
        with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_RELEASE_UNAVAILABLE"):
            service.act(initial["flow_id"], command(1, "new-grant", target={"collection": "knowledge", "id": own["id"]}), 1)
        with pytest.raises(PackageFlowError, match="PACKAGE_FLOW_RELEASE_UNAVAILABLE"):
            service.create(create_body(unused_opening, "after-expiration"), 1)
        assert db.query(ScriptPackageFlow).count() == 1
        assert db.query(ScriptPackageFlowAction).count() == 1
    assert case.jobs.get(case.job["id"])["charged_cost_cny"] == charged
