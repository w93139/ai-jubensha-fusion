"""Fictional candidate packages only. No source files, real DB or models."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from pydantic import ValidationError
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text, update
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.api.routes import script_package_routes
from src.core.auth_middleware import UnifiedAuthMiddleware
from src.db.base import SQLAlchemyBase
from src.db.models import ScriptDBModel, ScriptImportJob, ScriptPackageVersion, User
from src.fusion.package_import import PackageConflict, PackageImportService
from src.fusion.package_validation import (
    MAX_PACKAGE_BYTES, PackageInputError, canonical_json, content_hash, parse_package_json, validate_package,
)
from src.schemas.script_package import ScriptPackage, ScriptPackageV11, parse_script_package


def fictional_package() -> dict:
    source = [{"source_id": "normalized", "anchor": "fixture-opening"}]
    return {
        "schema_version": "script-package/1.0", "script_key": "fictional-gallery",
        "content_version": "draft-1", "title": "虚构展馆测试包", "player_count": 2,
        "sources": [
            {"id": "original", "relative_path": "original/fixture.pdf", "sha256": "1" * 64,
             "kind": "original", "media_type": "application/pdf", "page_count": 2},
            {"id": "normalized", "relative_path": "normalized/fixture.md", "sha256": "2" * 64,
             "kind": "normalized", "media_type": "text/markdown", "original_source_id": "original"},
        ],
        "introduction": {"text": "展馆的钟停了，两位值班员核对记录。", "sources": deepcopy(source)},
        "characters": [{"id": key, "name": name, "sources": deepcopy(source)}
                       for key, name in [("a", "值班员甲"), ("b", "值班员乙")]],
        "initial_phase_id": "opening",
        "phases": [
            {"id": "opening", "title": "入场", "next_phase_id": "ending", "sources": deepcopy(source)},
            {"id": "ending", "title": "复盘", "next_phase_id": None, "sources": deepcopy(source)},
        ],
        "knowledge": [
            {"id": f"memory-{key}", "text": f"PRIVATE_{key}_SENTINEL",
             "kind": "FACT", "visibility": "CHARACTER_PRIVATE", "character_id": key,
             "release": {"phase_id": "opening"}, "disclosure": "MAY_SHARE", "sources": deepcopy(source)}
            for key in ["a", "b"]
        ],
        "evidence": [
            {"id": "clock", "text": "钟的维修卡", "visibility": "CHARACTER_PRIVATE", "character_id": "a",
             "release": {"phase_id": "opening"}, "disclosure": "MAY_SHARE", "sources": deepcopy(source)},
            {"id": "record", "text": "公开的值班记录", "visibility": "PUBLIC", "character_id": None,
             "release": {"phase_id": "ending", "required_public_evidence_ids": ["clock"]},
             "disclosure": "PUBLIC", "sources": deepcopy(source)},
        ],
        "truth": [{"id": "answer", "text": "SYSTEM_TRUTH_SENTINEL：钟在维修。",
                   "visibility": "SYSTEM_TRUTH", "sources": deepcopy(source)}],
        "settlement": {"phase_id": "ending", "truth_ids": ["answer"],
                       "instructions": {"text": "按维修记录复盘。", "sources": deepcopy(source)}},
    }


def fictional_package_v11() -> dict:
    package = fictional_package()
    package["schema_version"] = "script-package/1.1"
    for source in package["sources"]:
        original = source.pop("original_source_id", None)
        source["original_source_ids"] = [original] if original is not None else []
    package["sources"].extend([
        {"id": "original-extra", "relative_path": "original/addendum.md", "sha256": "3" * 64,
         "kind": "original", "media_type": "text/markdown", "original_source_ids": []},
        {"id": "editorial", "relative_path": "supplements/note.md", "sha256": "4" * 64,
         "kind": "supplement", "media_type": "text/markdown", "original_source_ids": [],
         "provenance_note": "Synthetic editorial supplement; never claimed as recovered original."},
    ])
    package["sources"][1]["original_source_ids"].append("original-extra")
    return package


def set_at(document: dict, path: tuple, value) -> None:
    target = document
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def test_valid_candidate_is_still_not_publishable():
    package = fictional_package()
    report = validate_package(package)
    assert report["valid"] and not report["issues"]
    assert not report["publication_ready"]
    assert report["pending_gates"] == ["SOURCE_VERIFICATION", "RUNTIME_COMPATIBILITY", "AUDIT", "HUMAN_REVIEW"]
    assert report["package_hash"] == content_hash(package)
    assert all(marker not in canonical_json(report) for marker in ["PRIVATE_a_SENTINEL", "SYSTEM_TRUTH_SENTINEL"])


def test_v11_multiple_originals_and_editorial_provenance_are_explicit_and_hash_bound():
    package = fictional_package_v11()
    parsed = parse_script_package(package)
    assert isinstance(parsed, ScriptPackageV11)
    assert parse_script_package(parsed.model_dump()) == parsed
    report = validate_package(package)
    assert report["valid"] and not report["publication_ready"]
    assert report["contract_version"] == "script-package/1.1"
    assert report["package_hash"] == content_hash(package)
    assert "provenance_note" not in canonical_json(report)
    for path, value in [(("sources", 1, "original_source_ids"), ["original-extra", "original"]),
                        (("sources", 3, "provenance_note"), "Revised editorial origin")]:
        changed = deepcopy(package)
        set_at(changed, path, value)
        assert content_hash(changed) != report["package_hash"]


@pytest.mark.parametrize("path,value,code", [
    (("sources", 1, "original_source_ids"), [], "SCHEMA_INVALID"),
    (("sources", 1, "original_source_ids"), None, "SCHEMA_INVALID"),
    (("sources", 1, "original_source_ids"), ["original", "original"], "SCHEMA_INVALID"),
    (("sources", 1, "original_source_ids"), [f"src-{i}" for i in range(201)], "SCHEMA_INVALID"),
    (("sources", 1, "original_source_ids"), ["missing"], "ORIGINAL_SOURCE_MISSING"),
    (("sources", 1, "original_source_ids"), ["normalized"], "ORIGINAL_SOURCE_MISSING"),
    (("sources", 1, "original_source_ids"), ["editorial"], "ORIGINAL_SOURCE_MISSING"),
    (("sources", 0, "original_source_ids"), ["original-extra"], "SCHEMA_INVALID"),
    (("sources", 3, "original_source_ids"), ["original"], "SCHEMA_INVALID"),
    (("sources", 3, "provenance_note"), None, "SCHEMA_INVALID"),
    (("sources", 3, "provenance_note"), " ", "SCHEMA_INVALID"),
    (("sources", 3, "provenance_note"), "x" * 2001, "SCHEMA_INVALID"),
    (("sources", 0, "provenance_note"), "This is actually editorial", "SCHEMA_INVALID"),
    (("sources", 1, "provenance_note"), None, "SCHEMA_INVALID"),
    (("sources", 1, "original_source_id"), "original", "SCHEMA_INVALID"),
    (("sources", 0, "relative_path"), "../outside.md", "SCHEMA_INVALID"),
])
def test_v11_invalid_provenance_fails_closed(path, value, code):
    package = fictional_package_v11()
    set_at(package, path, value)
    report = validate_package(package)
    assert not report["valid"] and not report["publication_ready"]
    assert code in {item["code"] for item in report["issues"]}


@pytest.mark.parametrize("index,field", [(0, "original_source_ids"), (3, "provenance_note")])
def test_v11_provenance_fields_cannot_be_omitted(index, field):
    package = fictional_package_v11()
    package["sources"][index].pop(field)
    assert not validate_package(package)["valid"]


def test_v11_errors_do_not_echo_editorial_notes_or_unknown_fields():
    package = fictional_package_v11()
    package["sources"][0]["provenance_note"] = "PRIVATE_PROVENANCE_SENTINEL"
    package["sources"][3]["PRIVATE_FIELD_SENTINEL"] = "PRIVATE_VALUE_SENTINEL"
    report = canonical_json(validate_package(package))
    assert "SENTINEL" not in report


def test_v1_remains_a_strict_separate_contract():
    package = fictional_package()
    assert type(parse_script_package(package)) is ScriptPackage
    assert validate_package(package)["contract_version"] == "script-package/1.0"
    with pytest.raises(ValidationError):
        ScriptPackage.model_validate(fictional_package_v11())
    package["sources"][0]["original_source_ids"] = []
    assert not validate_package(package)["valid"]
    package = fictional_package()
    package["sources"][0]["kind"] = "supplement"
    assert not validate_package(package)["valid"]


@pytest.mark.parametrize("path,value,code", [
    (("schema_version",), "future/9", "SCHEMA_INVALID"),
    (("player_count",), "2", "SCHEMA_INVALID"),
    (("player_count",), True, "SCHEMA_INVALID"),
    (("player_count",), 3, "CHARACTER_COUNT_MISMATCH"),
    (("sources",), [], "SCHEMA_INVALID"),
    (("sources", 0, "sha256"), "invalid", "SCHEMA_INVALID"),
    (("sources", 1, "original_source_id"), "missing", "ORIGINAL_SOURCE_MISSING"),
    (("sources", 1, "original_source_id"), "normalized", "ORIGINAL_SOURCE_MISSING"),
    (("sources", 0, "original_source_id"), "normalized", "INVALID_SOURCE_LINK"),
    (("sources", 1, "relative_path"), "original/fixture.pdf", "DUPLICATE_SOURCE_PATH"),
    (("characters", 1, "id"), "a", "DUPLICATE_ID"),
    (("introduction", "sources", 0, "source_id"), "foreign-script", "SOURCE_NOT_FOUND"),
    (("introduction", "sources", 0), {"source_id": "original", "page": 3}, "SOURCE_PAGE_OUT_OF_RANGE"),
    (("introduction", "sources", 0), {"source_id": "original"}, "SOURCE_LOCATION_MISSING"),
    (("initial_phase_id",), "missing", "PHASE_NOT_FOUND"),
    (("phases", 0, "next_phase_id"), None, "UNREACHABLE_PHASE"),
    (("phases", 0, "next_phase_id"), "missing", "PHASE_NOT_FOUND"),
    (("phases", 1, "next_phase_id"), "opening", "PHASE_CYCLE"),
    (("settlement", "phase_id"), "opening", "INVALID_SETTLEMENT_PHASE"),
    (("settlement", "truth_ids"), ["missing"], "TRUTH_NOT_FOUND"),
    (("settlement", "truth_ids"), ["answer", "answer"], "DUPLICATE_REFERENCE"),
    (("truth", 0, "visibility"), "PUBLIC", "SCHEMA_INVALID"),
    (("knowledge", 0, "visibility"), "MURDERER_ONLY", "SCHEMA_INVALID"),
    (("knowledge", 0, "character_id"), "other-script-character", "INVALID_PRIVATE_SCOPE"),
    (("knowledge", 0, "character_id"), None, "INVALID_PRIVATE_SCOPE"),
    (("knowledge", 0, "visibility"), "PUBLIC", "INVALID_PUBLIC_SCOPE"),
    (("knowledge", 0, "disclosure"), "PUBLIC", "INVALID_PRIVATE_SCOPE"),
    (("knowledge", 0, "release", "phase_id"), "missing", "PHASE_NOT_FOUND"),
    (("knowledge", 0, "release", "phase_id"), "ending", "INITIAL_KNOWLEDGE_MISSING"),
    (("evidence", 0, "visibility"), "PUBLIC", "INVALID_PUBLIC_SCOPE"),
    (("evidence", 1, "release", "required_public_evidence_ids"), ["missing"], "EVIDENCE_NOT_FOUND"),
    (("evidence", 1, "release", "required_public_evidence_ids"), ["clock", "clock"], "DUPLICATE_REFERENCE"),
    (("evidence", 0, "disclosure"), "KEEP_PRIVATE", "UNSATISFIABLE_RELEASE"),
    (("evidence", 0, "release", "required_public_evidence_ids"), ["record"], "UNREACHABLE_EVIDENCE"),
    (("evidence", 0, "release", "expression"), "shell('anything')", "SCHEMA_INVALID"),
    (("approved",), True, "SCHEMA_INVALID"),
    (("status",), "PUBLISHED", "SCHEMA_INVALID"),
    (("knowledge", 0, "is_murderer"), True, "SCHEMA_INVALID"),
])
def test_invalid_candidates_fail_closed(path, value, code):
    package = fictional_package()
    set_at(package, path, value)
    report = validate_package(package)
    assert not report["valid"] and not report["publication_ready"]
    assert code in {item["code"] for item in report["issues"]}


@pytest.mark.parametrize("path", ["/tmp/private.md", "../private.md", "a/../private.md", "a//b", "./b",
                                  "C:\\private.md", "https://example.com/source", "file%2emd", "a\x00b", "~/.env"])
def test_source_paths_are_relative_locators_only(path):
    package = fictional_package()
    package["sources"][0]["relative_path"] = path
    assert not validate_package(package)["valid"]


@pytest.mark.parametrize("collection", ["sources", "characters", "phases", "knowledge", "evidence", "truth"])
def test_all_entity_ids_are_unique(collection):
    package = fictional_package()
    package[collection].append(deepcopy(package[collection][0]))
    assert "DUPLICATE_ID" in {item["code"] for item in validate_package(package)["issues"]}


def test_reports_hide_private_invalid_values_and_unknown_field_names():
    package = fictional_package()
    package["PRIVATE_UNKNOWN_FIELD_SENTINEL"] = {"secret": "PRIVATE_VALUE_SENTINEL"}
    package["knowledge"][0]["kind"] = "PRIVATE_BAD_ENUM_SENTINEL"
    output = canonical_json(validate_package(package))
    assert "SENTINEL" not in output
    assert "<field>" in output and "/knowledge/0/kind" in output


def test_canonical_hash_preserves_content_but_ignores_object_key_order():
    package = fictional_package()
    assert content_hash(package) == content_hash(dict(reversed(list(package.items()))))
    for path, value in [(("sources", 0, "sha256"), "3" * 64), (("content_version",), "draft-2"),
                        (("knowledge", 0, "text"), "changed"), (("sources", 1, "original_source_id"), "changed")]:
        changed = deepcopy(package)
        set_at(changed, path, value)
        assert content_hash(changed) != content_hash(package)


@pytest.mark.parametrize("value", [{1: "non-string key"}, {"x": float("nan")}, {"x": float("inf")},
                                   {"x": object()}, {"x": "\ud800"}, {"x": "a" * MAX_PACKAGE_BYTES}])
def test_noncanonical_inputs_are_rejected(value):
    with pytest.raises(PackageInputError):
        canonical_json(value)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x": NaN}', b'{"x": Infinity}', b'\xff', b'{private-secret'])
def test_json_parser_does_not_silently_discard_ambiguous_input(raw):
    with pytest.raises(PackageInputError) as exc:
        parse_package_json(raw)
    assert "private-secret" not in str(exc.value)


def test_cyclic_and_deep_inputs_fail_with_safe_errors():
    cyclic = {}
    cyclic["self"] = cyclic
    with pytest.raises(PackageInputError):
        canonical_json(cyclic)
    with pytest.raises(PackageInputError):
        parse_package_json(b"[" * 2000 + b"]" * 2000)


def test_long_dependency_chain_and_bounded_reports():
    package = fictional_package()
    item = package["evidence"][0]
    package["evidence"] = []
    for index in range(1000):
        clue = deepcopy(item)
        clue["id"] = f"clue-{index}"
        clue["release"]["required_public_evidence_ids"] = [f"clue-{index - 1}"] if index else []
        package["evidence"].append(clue)
    assert validate_package(package)["valid"]
    for clue in package["evidence"]:
        clue["character_id"] = "foreign"
    report = validate_package(package)
    assert not report["valid"] and report["issues_truncated"]
    assert len(report["issues"]) == 200
    canonical_json(report)


@pytest.fixture
def import_db():
    engine = create_engine("sqlite://", poolclass=StaticPool,
                           connect_args={"check_same_thread": False, "autocommit": False})
    SQLAlchemyBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)
    db = factory()
    user = User(username="package-admin", email="package@example.invalid", hashed_password="fixture", is_admin=True)
    db.add(user)
    db.commit()
    actor = user.id
    yield db, actor, factory
    db.close()
    engine.dispose()


def submit(db, actor, package=None, key="import-fixture-1"):
    return PackageImportService(db).submit(package or fictional_package(), submitted_by=actor, idempotency_key=key)


def test_import_persists_hash_bound_draft_and_does_not_create_runtime_script(import_db):
    db, actor, _ = import_db
    result = submit(db, actor)
    assert result["status"] == "SUCCEEDED" and result["version_id"]
    assert result["publication_ready"] is False
    assert db.query(ScriptDBModel).count() == 0
    db.commit()
    db.expire_all()
    service = PackageImportService(db)
    assert service.get_job(result["id"]) == result
    version = service.get_version(result["version_id"])
    assert version["package"] == fictional_package()
    assert version["manifest_hash"] == content_hash(fictional_package()["sources"])


def test_import_v11_persists_actual_contract_and_preserves_v1_snapshot(import_db):
    db, actor, _ = import_db
    first = submit(db, actor)
    package = fictional_package_v11()
    package["content_version"] = "draft-2"
    second = submit(db, actor, package, key="v11-import")
    db.commit()
    db.expire_all()
    service = PackageImportService(db)
    assert service.get_version(first["version_id"])["package"] == fictional_package()
    assert service.get_version(second["version_id"])["package"] == package
    assert db.get(ScriptPackageVersion, first["version_id"]).contract_version == "script-package/1.0"
    assert db.get(ScriptPackageVersion, second["version_id"]).contract_version == "script-package/1.1"
    assert second["report"]["contract_version"] == "script-package/1.1"
    assert second["input_hash"] == content_hash(package) and not second["publication_ready"]
    assert db.query(ScriptDBModel).count() == 0


def test_invalid_import_persists_blocked_report_without_candidate(import_db):
    db, actor, _ = import_db
    invalid = fictional_package()
    invalid["knowledge"][0]["character_id"] = "foreign-character"
    result = submit(db, actor, invalid)
    db.commit()
    assert result["status"] == "BLOCKED" and result["version_id"] is None
    assert db.query(ScriptPackageVersion).count() == 0
    assert db.query(ScriptImportJob).count() == 1
    assert PackageImportService(db).get_job(result["id"]) == result


def test_duplicate_input_and_reused_idempotency_key(import_db):
    db, actor, _ = import_db
    first = submit(db, actor)
    db.commit()
    assert submit(db, actor) == first
    second = submit(db, actor, key="import-fixture-2")
    assert second["version_id"] == first["version_id"] and second["id"] != first["id"]
    changed = fictional_package()
    changed["title"] = "another candidate"
    with pytest.raises(PackageConflict, match="幂等键"):
        submit(db, actor, changed)
    assert db.query(ScriptPackageVersion).count() == 1
    assert db.query(ScriptImportJob).count() == 2


def test_idempotency_uniqueness_race_returns_existing_result(import_db, monkeypatch):
    db, actor, _ = import_db
    first = submit(db, actor)
    db.commit()
    service = PackageImportService(db)
    lookup = service._existing_job
    # Emulate a stale initial lookup; the real UNIQUE constraint must reject
    # the duplicate INSERT and the service must recover after its savepoint.
    calls = 0

    def stale_once(*args):
        nonlocal calls
        calls += 1
        return None if calls == 1 else lookup(*args)

    monkeypatch.setattr(service, "_existing_job", stale_once)
    assert service.submit(fictional_package(), submitted_by=actor, idempotency_key="import-fixture-1") == first
    assert db.query(ScriptImportJob).count() == db.query(ScriptPackageVersion).count() == 1


def test_version_uniqueness_race_reuses_version_after_savepoint(import_db, monkeypatch):
    db, actor, _ = import_db
    first = submit(db, actor)
    db.commit()
    query = db.query
    stale = Mock()
    stale.filter_by.return_value.one_or_none.return_value = None
    missed = False

    def stale_version_once(model):
        nonlocal missed
        if model is ScriptPackageVersion and not missed:
            missed = True
            return stale
        return query(model)

    monkeypatch.setattr(db, "query", stale_version_once)
    second = submit(db, actor, key="raced-version")
    assert second["version_id"] == first["version_id"] and second["id"] != first["id"]
    assert db.query(ScriptPackageVersion).count() == 1
    assert db.query(ScriptImportJob).count() == 2


def test_changed_content_requires_new_version_and_keeps_previous_snapshot(import_db):
    db, actor, _ = import_db
    first = submit(db, actor)
    changed = fictional_package()
    changed["knowledge"][0]["text"] = "new memory"
    with pytest.raises(PackageConflict, match="新版本"):
        submit(db, actor, changed, key="changed-1")
    changed["content_version"] = "draft-2"
    second = submit(db, actor, changed, key="changed-2")
    service = PackageImportService(db)
    assert first["input_hash"] != second["input_hash"]
    assert first["version_id"] != second["version_id"]
    assert service.get_version(first["version_id"])["package"] == fictional_package()
    assert service.get_version(second["version_id"])["package"] == changed


def test_caller_rollback_removes_both_snapshot_and_job(import_db):
    db, actor, _ = import_db
    submit(db, actor)
    db.rollback()
    assert db.query(ScriptImportJob).count() == db.query(ScriptPackageVersion).count() == 0


def test_report_insert_failure_does_not_leave_half_import(import_db):
    db, actor, _ = import_db

    def fail_insert(mapper, connection, target):
        raise RuntimeError("synthetic insert failure")

    event.listen(ScriptImportJob, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="synthetic"):
            submit(db, actor)
    finally:
        event.remove(ScriptImportJob, "before_insert", fail_insert)
    assert db.query(ScriptImportJob).count() == db.query(ScriptPackageVersion).count() == 0


@pytest.mark.parametrize("model,field", [(ScriptPackageVersion, "package_json"), (ScriptImportJob, "report_json")])
@pytest.mark.parametrize("operation", ["update", "delete"])
def test_orm_cannot_modify_completed_snapshot(import_db, model, field, operation):
    db, actor, _ = import_db
    submit(db, actor)
    db.commit()
    row = db.query(model).one()
    if operation == "update":
        setattr(row, field, "{}")
    else:
        db.delete(row)
    with pytest.raises(ValueError, match="不可修改"):
        db.flush()
    db.rollback()


@pytest.mark.parametrize("model,field", [(ScriptPackageVersion, "package_json"), (ScriptImportJob, "report_json")])
def test_out_of_band_corruption_is_detected(import_db, model, field):
    db, actor, _ = import_db
    result = submit(db, actor)
    db.commit()
    db.execute(update(model).values({field: "{}"}))
    db.commit()
    db.expire_all()
    with pytest.raises(PackageConflict, match="完整性"):
        PackageImportService(db).get_job(result["id"])


@pytest.fixture
def package_client(import_db):
    db, actor, factory = import_db
    identities = {
        "admin": SimpleNamespace(id=actor, is_active=True, is_admin=True),
        "player": SimpleNamespace(id=actor, is_active=True, is_admin=False),
        "disabled": SimpleNamespace(id=actor, is_active=False, is_admin=True),
    }

    class OfflineAuth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return identities.get(token)

    def test_session():
        with factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    # End the fixture's read transaction before using this single connection.
    db.commit()
    app = FastAPI()
    app.include_router(script_package_routes.router)
    app.add_middleware(OfflineAuth)
    app.dependency_overrides[script_package_routes.get_db_session] = test_session
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize("path,method", [("script-imports", "POST"), ("script-imports/1", "GET"), ("script-packages/1", "GET")])
@pytest.mark.parametrize("token,status", [(None, 401), ("player", 403), ("disabled", 403)])
def test_candidate_routes_require_active_admin(package_client, path, method, token, status):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    result = package_client.request(method, f"/api/admin/fusion/{path}", headers=headers)
    assert result.status_code == status and "SENTINEL" not in result.text


def test_real_http_handlers_import_replay_and_read_private_version(package_client):
    headers = {"Authorization": "Bearer admin"}
    body = {"idempotency_key": "http-1", "package": fictional_package()}
    result = package_client.post("/api/admin/fusion/script-imports", json=body, headers=headers)
    assert result.status_code == 200
    data = result.json()["data"]
    assert data["status"] == "SUCCEEDED" and "SENTINEL" not in result.text
    assert package_client.post("/api/admin/fusion/script-imports", json=body, headers=headers).json() == result.json()
    assert package_client.get(f"/api/admin/fusion/script-imports/{data['id']}", headers=headers).json() == result.json()
    version = package_client.get(f"/api/admin/fusion/script-packages/{data['version_id']}", headers=headers)
    assert version.json()["data"]["package"] == fictional_package()
    body["package"]["title"] = "changed"
    assert package_client.post("/api/admin/fusion/script-imports", json=body, headers=headers).status_code == 409
    assert package_client.get("/api/admin/fusion/script-imports/9999", headers=headers).status_code == 404


@pytest.mark.parametrize("raw,status", [(b'{"package":{"secret":"PRIVATE_INPUT_SENTINEL"}}', 422),
                                      (b'{"x":1,"x":2}', 422), (b'not-json', 422),
                                      (b'x' * (MAX_PACKAGE_BYTES + 1), 413)])
def test_http_body_errors_never_echo_private_payload(package_client, raw, status, caplog):
    result = package_client.post("/api/admin/fusion/script-imports", content=raw,
                                 headers={"Authorization": "Bearer admin", "Content-Type": "application/json"})
    assert result.status_code == status
    assert "PRIVATE_INPUT_SENTINEL" not in result.text + caplog.text


def test_http_records_schema_errors_instead_of_global_validation_handler(package_client, caplog):
    package = fictional_package()
    package["PRIVATE_INPUT_SENTINEL"] = "PRIVATE_VALUE_SENTINEL"
    result = package_client.post("/api/admin/fusion/script-imports", json={"idempotency_key": "invalid-1", "package": package},
                                 headers={"Authorization": "Bearer admin"})
    assert result.status_code == 200
    assert result.json()["data"]["status"] == "BLOCKED"
    assert "SENTINEL" not in result.text + caplog.text


def test_handlers_require_admin_even_without_global_middleware():
    app = FastAPI()
    app.include_router(script_package_routes.router)
    fake_service = Mock()
    app.dependency_overrides[script_package_routes.import_service] = lambda: fake_service
    with TestClient(app) as client:
        assert client.post("/api/admin/fusion/script-imports", json={}).status_code == 401
        assert client.get("/api/admin/fusion/script-imports/1").status_code == 401
        assert client.get("/api/admin/fusion/script-packages/1").status_code == 401
    assert fake_service.mock_calls == []


def test_exported_json_schema_matches_contract():
    path = Path(__file__).resolve().parents[3] / "docs/contracts/script-package.v1.schema.json"
    schema = json.loads(path.read_text())
    assert schema == {"$schema": "https://json-schema.org/draft/2020-12/schema", **ScriptPackage.model_json_schema()}


def test_exported_v11_json_schema_matches_contract():
    path = Path(__file__).resolve().parents[3] / "docs/contracts/script-package.v1.1.schema.json"
    schema = json.loads(path.read_text())
    assert schema == {"$schema": "https://json-schema.org/draft/2020-12/schema", **ScriptPackageV11.model_json_schema()}


def load_migration():
    path = Path(__file__).resolve().parents[2] / "src/db/migrations/versions/j0d1e2f3a4b5_add_script_package_intake.py"
    spec = importlib.util.spec_from_file_location("candidate_migration_fixture", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_matches_models_and_reverses_without_app_startup():
    migration = load_migration()
    engine = create_engine("sqlite://")
    tables = {"script_package_versions", "script_import_jobs"}
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE users (id INTEGER PRIMARY KEY)"))
        context = MigrationContext.configure(connection, opts={
            "include_object": lambda obj, name, kind, reflected, compare: kind != "table" or name in tables,
        })
        with Operations.context(context):
            migration.upgrade()
            assert compare_metadata(context, SQLAlchemyBase.metadata) == []
            migration.downgrade()
        assert connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars().all() == ["users"]
    engine.dispose()
    assert migration.down_revision == "i9c0d1e2f3a4"


def test_postgres_migration_compiles_offline_without_connecting():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        load_migration().upgrade()
    sql = output.getvalue()
    assert "CREATE TABLE script_package_versions" in sql
    assert "uq_script_import_actor_key" in sql and "REFERENCES users (id)" in sql
