"""Offline integration boundaries around publication and immutable bindings."""
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from src.core.auth_middleware import UnifiedAuthMiddleware
from src.db.base import SQLAlchemyBase
from src.db.models.script_model import ScriptStatus
from src.fusion.package_import import PackageNotFound
from src.fusion.publication_lock import lock_candidate_version
from src.fusion.service import FusionGameError, FusionGameService


def migration():
    path = Path(__file__).resolve().parents[2] / 'src/db/migrations/versions/m3f4a5b6c7d8_add_publication_and_package_binding.py'
    spec = importlib.util.spec_from_file_location('publication_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_publication_migration_matches_models_and_preserves_old_rows():
    # Import all three models even when this test is selected on its own.
    from src.db.models import ScriptPackagePlaySession, ScriptPackageRelease, ScriptPublicationApproval
    names = {item.__tablename__ for item in (ScriptPackagePlaySession, ScriptPackageRelease, ScriptPublicationApproval)}
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE users (id INTEGER PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE script_package_versions (id INTEGER PRIMARY KEY)'))
        connection.execute(text('INSERT INTO script_package_versions VALUES (123)'))
        context = MigrationContext.configure(connection, opts={
            'include_object': lambda obj, name, kind, reflected, compare: kind != 'table' or name in names})
        with Operations.context(context):
            migration().upgrade()
            assert compare_metadata(context, SQLAlchemyBase.metadata) == []
            migration().downgrade()
        assert set(connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars()) == {'users', 'script_package_versions'}
        assert connection.execute(text('SELECT id FROM script_package_versions')).scalar() == 123
    engine.dispose()
    assert migration().down_revision == 'l2e3f4a5b6c7'


def test_publication_migration_postgres_compiles_without_connection():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
    with Operations.context(context):
        migration().upgrade()
        migration().downgrade()
    sql = output.getvalue()
    assert 'CREATE TABLE script_package_play_sessions' in sql
    assert 'uq_script_release_version' in sql and 'uq_package_play_actor_key' in sql
    assert 'REFERENCES script_publication_approvals (id)' in sql
    assert 'ALTER TABLE game_sessions' not in sql


def test_candidate_lock_serializes_writers_and_preserves_snapshot(tmp_path):
    engine = create_engine('sqlite:///' + str(tmp_path / 'candidate-lock.sqlite3'), connect_args={'timeout': 0})
    with engine.begin() as db:
        db.execute(text('CREATE TABLE script_package_versions (id INTEGER PRIMARY KEY, package_json TEXT)'))
        db.execute(text("INSERT INTO script_package_versions VALUES (1, 'frozen')"))
    with Session(engine) as first, Session(engine) as second:
        lock_candidate_version(first, 1)
        with pytest.raises(OperationalError):
            lock_candidate_version(second, 1)
        second.rollback()
        first.rollback()
        lock_candidate_version(second, 1)
        assert second.execute(text('SELECT package_json FROM script_package_versions WHERE id=1')).scalar() == 'frozen'
        second.rollback()
        with pytest.raises(PackageNotFound):
            lock_candidate_version(second, 99)
    engine.dispose()


def test_legacy_publish_denied_even_when_flat_validation_would_pass():
    script = SimpleNamespace(id=7, status=ScriptStatus.REVIEW, is_public=False)
    db = Mock()
    db.get.return_value = script
    service = FusionGameService(db)
    service.validate_script = Mock(return_value={'valid': True})
    with pytest.raises(FusionGameError, match='候选审核'):
        service.set_script_status(7, ScriptStatus.PUBLISHED)
    assert script.status == ScriptStatus.REVIEW and script.is_public is False
    db.commit.assert_not_called()
    service.validate_script.assert_not_called()


@pytest.fixture
def publication_http():
    from src.api.routes import script_publication_routes as routes
    users = {'admin': SimpleNamespace(id=1, is_active=True, is_admin=True),
             'player': SimpleNamespace(id=2, is_active=True, is_admin=False),
             'disabled': SimpleNamespace(id=3, is_active=False, is_admin=True)}
    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return users.get(token)
    app = FastAPI()
    app.add_middleware(Auth)
    app.include_router(routes.router)
    service = Mock()
    service.gate_state.return_value = {'can_approve': False, 'checks': []}
    app.dependency_overrides[routes.publication_service] = lambda: service
    app.dependency_overrides[routes.source_store] = lambda: Mock()
    with TestClient(app) as client:
        yield client, service


@pytest.mark.parametrize('token,status', [(None, 401), ('player', 403), ('disabled', 403)])
@pytest.mark.parametrize('suffix,method', [('publication', 'GET'), ('approvals', 'POST'), ('publish', 'POST')])
def test_publication_requires_active_administrator(publication_http, token, status, suffix, method):
    client, service = publication_http
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    response = client.request(method, '/api/admin/fusion/script-packages/1/' + suffix, headers=headers)
    assert response.status_code == status
    assert service.mock_calls == []


def test_publication_read_no_store_and_bad_input_not_echoed(publication_http):
    client, service = publication_http
    headers = {'Authorization': 'Bearer admin'}
    response = client.get('/api/admin/fusion/script-packages/1/publication', headers=headers)
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    bad = client.post('/api/admin/fusion/script-packages/1/approvals', headers=headers,
                      json={'note': 'PRIVATE_DIAGNOSTIC_SENTINEL', 'is_approved': True})
    assert bad.status_code == 422 and 'PRIVATE_DIAGNOSTIC_SENTINEL' not in bad.text
    service.approve.assert_not_called()
