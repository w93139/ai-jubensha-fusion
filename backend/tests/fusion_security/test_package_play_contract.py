"""Text-play wire/ledger contracts, using fictional rows and offline migrations."""
from datetime import datetime
import importlib.util
import io
import json
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from jsonschema import Draft202012Validator
from pydantic import ValidationError
import pytest
from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.dialects.postgresql import dialect as postgres_dialect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.schema import CreateTable

from src.db.base import SQLAlchemyBase
from src.db.models.package_play import ScriptPackagePlay, ScriptPackagePlayEvent
from src.schemas.package_play import CreatePackagePlayRequest, PackagePlayActionRequest, PackagePlayAskRequest


ROOT = Path(__file__).resolve().parents[3]
OPENING = "package-" + "a" * 32
PLAY = "play-" + "a" * 32
SCHEMAS = {"create": CreatePackagePlayRequest, "action": PackagePlayActionRequest, "ask": PackagePlayAskRequest}


def document(name):
    return json.loads((ROOT / "docs/contracts" / f"package-play-{name}.v1.schema.json").read_text(encoding="utf-8"))


def migration():
    path = ROOT / "backend/src/db/migrations/versions/o5b6c7d8e9f0_add_package_play.py"
    spec = importlib.util.spec_from_file_location("package_play_migration", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def valid_action(action="ADVANCE_PHASE"):
    payload = {"idempotency_key": "play-action", "expected_revision": 0, "action": action}
    if action == "SHARE_MATERIAL":
        payload["target"] = {"collection": "evidence", "id": "invented-card"}
    elif action == "PERFORM_ACTION":
        payload["target"] = {"action_id": "invented-search"}
    return payload


def valid_ask():
    return {"idempotency_key": "play-question", "expected_revision": 0,
            "character_id": "invented-character", "question": "  虚构时钟停在几点？\n"}


@pytest.mark.parametrize("name", SCHEMAS)
def test_package_play_static_schemas_exactly_match_models(name):
    schema = document(name)
    Draft202012Validator.check_schema(schema)
    assert schema == SCHEMAS[name].model_json_schema() | {"$schema": "https://json-schema.org/draft/2020-12/schema"}
    assert schema["additionalProperties"] is False


@pytest.mark.parametrize("action", ["ADVANCE_PHASE", "SETTLE", "SHARE_MATERIAL", "PERFORM_ACTION"])
def test_package_play_actions_roundtrip_without_injecting_null_target(action):
    payload = valid_action(action)
    parsed = PackagePlayActionRequest.model_validate(payload)
    assert parsed.model_dump() == payload
    assert PackagePlayActionRequest.model_validate(parsed.model_dump()) == parsed
    assert PackagePlayActionRequest.model_validate_json(parsed.model_dump_json()) == parsed
    assert Draft202012Validator(document("action")).is_valid(payload)
    with pytest.raises(ValidationError):
        parsed.expected_revision = 2


@pytest.mark.parametrize("payload", [
    valid_action() | {"target": None}, valid_action("SETTLE") | {"target": None},
    valid_action("SETTLE") | {"target": {"collection": "evidence", "id": "invented-card"}},
    {"expected_revision": 0, "idempotency_key": "k", "action": "SHARE_MATERIAL"},
    valid_action("SHARE_MATERIAL") | {"target": None},
    valid_action("SHARE_MATERIAL") | {"target": {"collection": "truth", "id": "answer"}},
    valid_action("SHARE_MATERIAL") | {"target": {"collection": "knowledge", "id": "card", "text": "PRIVATE_SENTINEL"}},
    valid_action() | {"expected_revision": True}, valid_action() | {"expected_revision": "0"},
    valid_action() | {"expected_revision": -1}, valid_action() | {"phase_id": "last-phase"},
    valid_action() | {"owner_user_id": 2}, valid_action() | {"action": "AI_REQUEST"},
    valid_action("PERFORM_ACTION") | {"target": None},
    valid_action("PERFORM_ACTION") | {"target": {"collection": "evidence", "id": "invented-card"}},
    valid_action("PERFORM_ACTION") | {"target": {"action_id": "invented-search", "cost": 0}},
    valid_action("PERFORM_ACTION") | {"target": {"action_id": "invented-search", "actor_character_id": "b"}},
    valid_action("SHARE_MATERIAL") | {"target": {"action_id": "invented-search"}},
    {"expected_revision": 0, "idempotency_key": "k", "action": "PERFORM_ACTION"},
])
def test_package_play_action_schema_rejects_authority_injection_and_invalid_branches(payload):
    assert not Draft202012Validator(document("action")).is_valid(payload)
    with pytest.raises(ValidationError):
        PackagePlayActionRequest.model_validate(payload)


def test_package_play_create_accepts_only_an_opening_reference_and_request_key():
    valid = {"opening_session_id": OPENING, "idempotency_key": "new-play"}
    assert CreatePackagePlayRequest.model_validate(valid).model_dump() == valid
    schema = Draft202012Validator(document("create"))
    assert schema.is_valid(valid)
    for patch in ({"opening_session_id": "flow-" + "a" * 32},
                  {"opening_session_id": OPENING + "\n"}, {"opening_session_id": "package-" + "A" * 32},
                  {"opening_session_id": 1}, {"character_id": "another-character"},
                  {"release_id": 1}, {"idempotency_key": "../path"}):
        payload = valid | patch
        assert not schema.is_valid(payload)
        with pytest.raises(ValidationError):
            CreatePackagePlayRequest.model_validate(payload)


def test_package_play_ask_canonicalizes_question_and_preserves_meaningful_content():
    body = valid_ask()
    parsed = PackagePlayAskRequest.model_validate(body)
    assert parsed.question == "虚构时钟停在几点？"
    assert PackagePlayAskRequest.model_validate(body | {"question": parsed.question}) == parsed
    assert PackagePlayAskRequest.model_validate_json(parsed.model_dump_json()) == parsed
    for question in ("字" * 1000, "第一行\n第二行", "\u3000带有全角空白\u3000"):
        assert Draft202012Validator(document("ask")).is_valid(body | {"question": question})
        assert PackagePlayAskRequest.model_validate(body | {"question": question}).question == question.strip()


@pytest.mark.parametrize("patch", [
    {"question": ""}, {"question": " \t\r\n\u3000"}, {"question": "\x1c\x1d\x1e\x1f"},
    {"question": "字" * 1001}, {"question": " " * 1000 + "字"}, {"question": None}, {"question": 123},
    {"expected_revision": True}, {"expected_revision": -1}, {"character_id": "../character"},
    {"character_id": None}, {"provider": "client-choice"}, {"context": "PRIVATE_SENTINEL"},
])
def test_package_play_ask_rejects_blank_oversize_and_caller_supplied_model_context(patch):
    payload = valid_ask() | patch
    assert not Draft202012Validator(document("ask")).is_valid(payload)
    with pytest.raises(ValidationError):
        PackagePlayAskRequest.model_validate(payload)


def ancestors(connection):
    # Only synthetic parent keys are needed to check the new foreign keys.
    for table in ("users", "script_package_releases", "script_package_versions"):
        connection.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
        connection.execute(text(f"INSERT INTO {table} (id) VALUES (1), (2)"))
    connection.execute(text("CREATE TABLE script_package_play_sessions (session_id VARCHAR(40) PRIMARY KEY)"))
    connection.execute(text("INSERT INTO script_package_play_sessions (session_id) VALUES (:first), (:second)"),
                       {"first": OPENING, "second": "package-" + "b" * 32})
    connection.execute(text("CREATE TABLE script_package_flows (flow_id VARCHAR(37) PRIMARY KEY)"))
    connection.execute(text("INSERT INTO script_package_flows (flow_id) VALUES ('existing-flow')"))


@pytest.fixture
def database(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "isolated-play.sqlite3"))
    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")
    with engine.begin() as connection:
        ancestors(connection)
        with Operations.context(MigrationContext.configure(connection)):
            migration().upgrade()
    yield engine
    engine.dispose()


def play_values():
    return {"play_id": PLAY, "owner_user_id": 1, "opening_session_id": OPENING,
            "release_id": 1, "version_id": 1, "package_hash": "1" * 64,
            "selected_character_id": "invented-character", "idempotency_key": "play-create",
            "request_hash": "2" * 64, "binding_json": "{}", "binding_hash": "3" * 64}


def event_values(revision=1, kind="ACTION"):
    return {"play_id": PLAY, "revision": revision, "kind": kind, "idempotency_key": f"event-{revision}",
            "request_json": "{}", "request_hash": "2" * 64, "previous_event_hash": "3" * 64,
            "state_hash": "4" * 64, "event_json": "{}", "event_hash": "5" * 64}


def test_package_play_migration_matches_models_and_preserves_prior_stages(database):
    names = {ScriptPackagePlay.__tablename__, ScriptPackagePlayEvent.__tablename__}
    with database.begin() as connection:
        context = MigrationContext.configure(connection, opts={
            "include_object": lambda obj, name, kind, reflected, compare: kind != "table" or name in names})
        assert compare_metadata(context, SQLAlchemyBase.metadata) == []
        checks = {item["name"] for item in inspect(connection).get_check_constraints("script_package_play_events")}
        assert checks == {"ck_package_play_event_revision_positive", "ck_package_play_event_kind"}
        with Operations.context(context):
            migration().downgrade()
        assert connection.execute(text("SELECT session_id FROM script_package_play_sessions ORDER BY session_id")).scalars().all() == [OPENING, "package-" + "b" * 32]
        assert connection.execute(text("SELECT flow_id FROM script_package_flows")).scalar() == "existing-flow"
        assert not names.intersection(inspect(connection).get_table_names())
    assert migration().down_revision == "n4a5b6c7d8e9"


def test_package_play_migration_and_orm_postgres_compile_without_live_database():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name="postgresql", opts={"as_sql": True, "output_buffer": output})
    with Operations.context(context):
        migration().upgrade()
        migration().downgrade()
    sql = output.getvalue()
    assert "ALTER TABLE" not in sql
    assert "REFERENCES script_package_play_sessions (session_id)" in sql
    assert "REFERENCES script_package_plays (play_id)" in sql
    for model in (ScriptPackagePlay, ScriptPackagePlayEvent):
        compiled = str(CreateTable(model.__table__).compile(dialect=postgres_dialect()))
        for constraint in model.__table__.constraints:
            if constraint.name:
                assert constraint.name in compiled and constraint.name in sql
    # PostgreSQL unique constraint names also name indexes, so these names
    # must not collide with the prior opening/flow models.
    all_names = [constraint.name for table in SQLAlchemyBase.metadata.tables.values()
                 for constraint in table.constraints if constraint.name]
    for model in (ScriptPackagePlay, ScriptPackagePlayEvent):
        assert all(all_names.count(constraint.name) == 1 for constraint in model.__table__.constraints if constraint.name)


def test_package_play_event_kinds_append_independently_and_reload(database):
    with Session(database) as db:
        db.add(ScriptPackagePlay(**play_values()))
        db.flush()
        db.add_all([ScriptPackagePlayEvent(**event_values(number, kind))
                    for number, kind in enumerate(("ACTION", "AI_REQUEST", "AI_RESULT"), 1)])
        db.commit()
    with Session(database) as db:
        rows = db.query(ScriptPackagePlayEvent).order_by(ScriptPackagePlayEvent.revision).all()
        assert [row.kind for row in rows] == ["ACTION", "AI_REQUEST", "AI_RESULT"]
        assert all(isinstance(row.created_at, datetime) for row in rows)
        assert [row.idempotency_key for row in rows] == ["event-1", "event-2", "event-3"]


@pytest.mark.parametrize("patch", [{"revision": 0}, {"revision": -1}, {"kind": "AI_UNKNOWN"},
                                   {"kind": None}, {"play_id": "play-" + "f" * 32}])
def test_package_play_event_database_rejects_invalid_revision_kind_or_parent(database, patch):
    with Session(database) as db:
        db.add(ScriptPackagePlay(**play_values()))
        db.commit()
        db.add(ScriptPackagePlayEvent(**(event_values() | patch)))
        with pytest.raises(IntegrityError):
            db.flush()
        db.rollback()
        assert db.query(ScriptPackagePlayEvent).count() == 0


def test_package_play_database_serializes_opening_owner_key_and_event_revision_keys(database):
    with Session(database) as db:
        db.add(ScriptPackagePlay(**play_values()))
        db.flush()
        db.add(ScriptPackagePlayEvent(**event_values()))
        db.commit()
        for patch in ({"play_id": "play-" + "b" * 32, "owner_user_id": 2, "idempotency_key": "other"},
                      {"play_id": "play-" + "b" * 32, "opening_session_id": "package-" + "b" * 32},
                      {"opening_session_id": "package-" + "b" * 32, "idempotency_key": "other"}):
            with pytest.raises(IntegrityError), db.begin_nested():
                db.add(ScriptPackagePlay(**(play_values() | patch)))
                db.flush()
        for patch in ({"idempotency_key": "other-event"}, {"revision": 2, "kind": "AI_RESULT"}):
            with pytest.raises(IntegrityError), db.begin_nested():
                db.add(ScriptPackagePlayEvent(**(event_values() | patch)))
                db.flush()
        assert db.query(ScriptPackagePlay).count() == 1 and db.query(ScriptPackagePlayEvent).count() == 1


@pytest.mark.parametrize("model", [ScriptPackagePlay, ScriptPackagePlayEvent])
def test_package_play_orm_immutable_records_reject_update_and_delete(database, model):
    with Session(database) as db:
        db.add(ScriptPackagePlay(**play_values()))
        db.flush()
        db.add(ScriptPackagePlayEvent(**event_values()))
        db.commit()
        row = db.query(model).one()
        row.idempotency_key = "cannot-rewrite"
        with pytest.raises(ValueError, match="PACKAGE_PLAY_RECORD_IMMUTABLE"):
            db.flush()
        db.rollback()
        db.delete(db.query(model).one())
        with pytest.raises(ValueError, match="PACKAGE_PLAY_RECORD_IMMUTABLE"):
            db.flush()
        db.rollback()
        assert db.query(model).count() == 1
