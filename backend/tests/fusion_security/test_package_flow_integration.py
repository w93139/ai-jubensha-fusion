"""Rules preview migration and authenticated HTTP edges, without a live app."""
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

from src.core.auth_middleware import UnifiedAuthMiddleware
from src.db.base import SQLAlchemyBase


def migration():
    path = Path(__file__).resolve().parents[2] / 'src/db/migrations/versions/n4a5b6c7d8e9_add_package_flow.py'
    spec = importlib.util.spec_from_file_location('package_flow_migration', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_package_flow_migration_matches_models_and_preserves_openings():
    from src.db.models import ScriptPackageFlow, ScriptPackageFlowAction
    names = {model.__tablename__ for model in (ScriptPackageFlow, ScriptPackageFlowAction)}
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        for name in ('users', 'script_package_versions', 'script_package_releases'):
            connection.execute(text(f'CREATE TABLE {name} (id INTEGER PRIMARY KEY)'))
        connection.execute(text('CREATE TABLE script_package_play_sessions (session_id VARCHAR(40) PRIMARY KEY)'))
        connection.execute(text("INSERT INTO script_package_play_sessions VALUES ('existing-opening')"))
        context = MigrationContext.configure(connection, opts={
            'include_object': lambda obj, name, kind, reflected, compare: kind != 'table' or name in names})
        with Operations.context(context):
            migration().upgrade()
            assert compare_metadata(context, SQLAlchemyBase.metadata) == []
            migration().downgrade()
        assert connection.execute(text('SELECT session_id FROM script_package_play_sessions')).scalar() == 'existing-opening'
        tables = set(connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).scalars())
        assert not tables & names
    engine.dispose()
    assert migration().down_revision == 'm3f4a5b6c7d8'


def test_package_flow_migration_postgres_compiles_offline():
    output = io.StringIO()
    context = MigrationContext.configure(dialect_name='postgresql', opts={'as_sql': True, 'output_buffer': output})
    with Operations.context(context):
        migration().upgrade()
        migration().downgrade()
    sql = output.getvalue()
    assert 'CREATE TABLE script_package_flow_actions' in sql
    assert 'uq_package_flow_action_revision' in sql and 'uq_package_flow_action_key' in sql
    assert 'REFERENCES script_package_play_sessions (session_id)' in sql
    assert 'ALTER TABLE' not in sql


@pytest.fixture
def flow_http():
    from src.api.routes import package_flow_routes as routes
    users = {'player': SimpleNamespace(id=2, is_active=True, is_admin=False),
             'disabled': SimpleNamespace(id=3, is_active=False, is_admin=False)}
    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return users.get(token)
    app = FastAPI()
    app.add_middleware(Auth)
    app.include_router(routes.router)
    service = Mock()
    service.find_for_opening.return_value = None
    service.get.return_value = {'flow_id': 'flow-' + 'a' * 32, 'revision': 1}
    service.create.return_value = {'flow_id': 'flow-' + 'a' * 32, 'revision': 0}
    service.act.return_value = {'flow_id': 'flow-' + 'a' * 32, 'revision': 2}
    app.dependency_overrides[routes.flow_service] = lambda: service
    with TestClient(app) as client:
        yield client, service


@pytest.mark.parametrize('token,status', [(None, 401), ('disabled', 403)])
@pytest.mark.parametrize('method,suffix', [('GET', ''), ('POST', ''), ('GET', '/flow-x'), ('POST', '/flow-x/actions')])
def test_package_flow_http_requires_active_actor(flow_http, token, status, method, suffix):
    client, service = flow_http
    response = client.request(method, '/api/fusion/package-flows' + suffix,
                              headers={'Authorization': 'Bearer ' + token} if token else {})
    assert response.status_code == status
    assert service.mock_calls == []


def test_package_flow_http_identity_and_exact_action_dispatch(flow_http):
    client, service = flow_http
    headers = {'Authorization': 'Bearer player'}
    base = '/api/fusion/package-flows'
    opening = 'package-' + 'b' * 32
    lookup = client.get(base, params={'opening_session_id': opening}, headers=headers)
    assert lookup.json() == {'success': True, 'data': None}
    service.find_for_opening.assert_called_once_with(opening, 2)
    created = client.post(base, json={'opening_session_id': opening, 'idempotency_key': 'start'}, headers=headers)
    assert created.status_code == 201
    assert service.create.call_args.args[1] == 2
    flow_id = created.json()['data']['flow_id']
    action = {'idempotency_key': 'advance', 'expected_revision': 1, 'action': 'ADVANCE_PHASE'}
    acted = client.post(base + '/' + flow_id + '/actions', json=action, headers=headers)
    assert acted.status_code == 200
    assert service.act.call_args.args[0] == flow_id and service.act.call_args.args[2] == 2
    assert service.act.call_args.args[1].model_dump() == action
    read = client.get(base + '/' + flow_id, headers=headers)
    service.get.assert_called_once_with(flow_id, 2)
    for response in (lookup, created, acted, read):
        assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('body', [
    {'idempotency_key': 'k', 'expected_revision': True, 'action': 'ADVANCE_PHASE'},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'ADVANCE_PHASE', 'target': None},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'ADVANCE_PHASE', 'owner_user_id': 1},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'SHARE_MATERIAL'},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'SETTLE', 'text': 'PRIVATE_SENTINEL'},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'SHARE_MATERIAL',
     'target': {'collection': 'truth', 'id': 'PRIVATE_SENTINEL'}},
])
def test_package_flow_http_rejects_injected_authority_without_echo(flow_http, body):
    client, service = flow_http
    response = client.post('/api/fusion/package-flows/flow-x/actions', json=body,
                           headers={'Authorization': 'Bearer player'})
    assert response.status_code == 422 and response.headers['Cache-Control'] == 'no-store'
    assert 'PRIVATE_SENTINEL' not in response.text
    service.act.assert_not_called()


@pytest.mark.parametrize('raw,status', [
    ('{"action":"ADVANCE_PHASE","action":"PRIVATE_SENTINEL"}', 422),
    ('PRIVATE_SENTINEL' * 500, 413),
])
def test_package_flow_http_bounds_raw_body_and_duplicate_json(flow_http, raw, status):
    client, service = flow_http
    response = client.post('/api/fusion/package-flows', content=raw, headers={'Authorization': 'Bearer player'})
    assert response.status_code == status and 'PRIVATE_SENTINEL' not in response.text
    service.create.assert_not_called()


def test_package_flow_http_conflict_is_safe_and_no_store(flow_http):
    from src.fusion.package_flow import PackageFlowError
    client, service = flow_http
    service.get.side_effect = PackageFlowError('PACKAGE_FLOW_NOT_FOUND', 404)
    response = client.get('/api/fusion/package-flows/unknown', headers={'Authorization': 'Bearer player'})
    assert response.status_code == 404 and response.headers['Cache-Control'] == 'no-store'
    assert response.json() == {'detail': 'PACKAGE_FLOW_NOT_FOUND'}
