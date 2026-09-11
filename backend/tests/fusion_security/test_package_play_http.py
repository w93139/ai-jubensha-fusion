"""Authenticated text-play HTTP edges in an isolated FastAPI app."""
from types import SimpleNamespace
import json
from unittest.mock import Mock, AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.core.auth_middleware import UnifiedAuthMiddleware


def test_default_http_service_enables_admission_and_interaction_projection():
    from unittest.mock import patch
    from src.api.routes import package_play_routes as routes
    db = object()
    with patch.object(routes, 'PackagePlayService') as service:
        assert routes.play_service(db=db) is service.return_value
    assert service.call_args.args == (db,)
    assert service.call_args.kwargs['include_interactions'] is True
    assert service.call_args.kwargs['request_scope_policy'] == 'package-request-scope/1.0'


def test_scope_denial_keeps_422_code_and_no_store(play_http):
    from src.fusion.package_play import PackagePlayError
    client, service = play_http
    service.ask.side_effect = PackagePlayError('PACKAGE_PLAY_OUT_OF_SCOPE', 422)
    response = client.post('/api/fusion/package-plays/play-x/ask', headers={'Authorization': 'Bearer player'},
        json={'idempotency_key': 'out-of-scope', 'expected_revision': 0, 'character_id': 'b', 'question': '写Python代码'})
    assert response.status_code == 422 and response.headers['Cache-Control'] == 'no-store'
    assert response.json() == {'detail': 'PACKAGE_PLAY_OUT_OF_SCOPE'}


@pytest.fixture
def play_http():
    from src.api.routes import package_play_routes as routes
    users = {'player': SimpleNamespace(id=2, is_active=True, is_admin=False),
             'disabled': SimpleNamespace(id=3, is_active=False, is_admin=False)}
    class Auth(UnifiedAuthMiddleware):
        async def get_user_from_token(self, token):
            return users.get(token)
    app = FastAPI()
    app.add_middleware(Auth)
    app.include_router(routes.router)
    service = Mock()
    service.ask = AsyncMock(return_value={"play_id": "play-" + "a" * 32, "revision": 2})
    service.find_for_opening.return_value = None
    service.get.return_value = {'play_id': 'play-' + 'a' * 32, 'revision': 1}
    service.create.return_value = {'play_id': 'play-' + 'a' * 32, 'revision': 0}
    service.act.return_value = {'play_id': 'play-' + 'a' * 32, 'revision': 2}
    app.dependency_overrides[routes.play_service] = lambda: service
    with TestClient(app) as client:
        yield client, service


@pytest.mark.parametrize('token,status', [(None, 401), ('disabled', 403)])
@pytest.mark.parametrize('method,suffix', [('GET', ''), ('POST', ''), ('GET', '/play-x'), ('POST', '/play-x/actions'), ('POST', '/play-x/ask')])
def test_package_play_http_requires_active_actor(play_http, token, status, method, suffix):
    client, service = play_http
    response = client.request(method, '/api/fusion/package-plays' + suffix,
                              headers={'Authorization': 'Bearer ' + token} if token else {})
    assert response.status_code == status
    assert service.mock_calls == []


def test_package_play_http_identity_and_exact_action_dispatch(play_http):
    client, service = play_http
    headers = {'Authorization': 'Bearer player'}
    base = '/api/fusion/package-plays'
    opening = 'package-' + 'b' * 32
    lookup = client.get(base, params={'opening_session_id': opening}, headers=headers)
    assert lookup.json() == {'success': True, 'data': None}
    service.find_for_opening.assert_called_once_with(opening, 2)
    created = client.post(base, json={'opening_session_id': opening, 'idempotency_key': 'start'}, headers=headers)
    assert created.status_code == 201
    assert service.create.call_args.args[1] == 2
    play_id = created.json()['data']['play_id']
    action = {'idempotency_key': 'advance', 'expected_revision': 1, 'action': 'ADVANCE_PHASE'}
    acted = client.post(base + '/' + play_id + '/actions', json=action, headers=headers)
    assert acted.status_code == 200
    assert service.act.call_args.args[0] == play_id and service.act.call_args.args[2] == 2
    assert service.act.call_args.args[1].model_dump() == action
    read = client.get(base + '/' + play_id, headers=headers)
    service.get.assert_called_once_with(play_id, 2)
    for response in (lookup, created, acted, read):
        assert response.headers['Cache-Control'] == 'no-store'


@pytest.mark.parametrize('body', [
    {'idempotency_key': 'k', 'expected_revision': True, 'action': 'ADVANCE_PHASE'},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'ADVANCE_PHASE', 'target': None},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'ADVANCE_PHASE', 'owner_user_id': 1},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'SHARE_MATERIAL'},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'SETTLE', 'target': None},
    {'idempotency_key': 'k', 'expected_revision': 0, 'action': 'SHARE_MATERIAL',
     'target': {'collection': 'truth', 'id': 'PRIVATE_SENTINEL'}},
])
def test_package_play_http_rejects_injected_authority_without_echo(play_http, body):
    client, service = play_http
    response = client.post('/api/fusion/package-plays/play-x/actions', json=body,
                           headers={'Authorization': 'Bearer player'})
    assert response.status_code == 422 and response.headers['Cache-Control'] == 'no-store'
    assert 'PRIVATE_SENTINEL' not in response.text
    service.act.assert_not_called()


@pytest.mark.parametrize('raw,status', [
    ('{"action":"ADVANCE_PHASE","action":"PRIVATE_SENTINEL"}', 422),
    ('PRIVATE_SENTINEL' * 600, 413),
], ids=['duplicate-keys', 'oversized'])
def test_package_play_http_bounds_raw_body_and_duplicate_json(play_http, raw, status):
    client, service = play_http
    response = client.post('/api/fusion/package-plays', content=raw, headers={'Authorization': 'Bearer player'})
    assert response.status_code == status and 'PRIVATE_SENTINEL' not in response.text
    service.create.assert_not_called()


def test_package_play_http_conflict_is_safe_and_no_store(play_http):
    from src.fusion.package_play import PackagePlayError
    client, service = play_http
    service.get.side_effect = PackagePlayError('PACKAGE_PLAY_NOT_FOUND', 404)
    response = client.get('/api/fusion/package-plays/unknown', headers={'Authorization': 'Bearer player'})
    assert response.status_code == 404 and response.headers['Cache-Control'] == 'no-store'
    assert response.json() == {'detail': 'PACKAGE_PLAY_NOT_FOUND'}


def test_package_play_http_ask_awaits_service_preserves_identity_and_unicode(play_http):
    client, service = play_http
    body = {'expected_revision': 7, 'idempotency_key': 'same-question', 'character_id': 'b',
            'question': '  灯' * 100 + '  '}
    response = client.post('/api/fusion/package-plays/play-x/ask', content=json.dumps(body),
                           headers={'Authorization': 'Bearer player'})
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    args = service.ask.await_args.args
    assert args[0] == 'play-x' and args[2] == 2
    assert args[1].question == body['question'].strip()
    assert args[1].idempotency_key == 'same-question' and args[1].expected_revision == 7


@pytest.mark.parametrize('patch', [
    {'expected_revision': True}, {'question': ' '}, {'question': '灯' * 1001},
    {'owner_user_id': 1}, {'materials': []}, {'character_id': ['b']},
    {'question': '\ud800'},
], ids=['bool-revision', 'blank', 'too-long', 'owner-injection', 'material-injection', 'role-type', 'surrogate'])
def test_package_play_http_invalid_ask_never_dispatches(play_http, patch):
    client, service = play_http
    body = {'expected_revision': 0, 'idempotency_key': 'k', 'character_id': 'b', 'question': '灯的线索？'} | patch
    response = client.post('/api/fusion/package-plays/play-x/ask', content=json.dumps(body),
                           headers={'Authorization': 'Bearer player'})
    assert response.status_code == 422 and response.headers['Cache-Control'] == 'no-store'
    service.ask.assert_not_awaited()


@pytest.mark.parametrize('token,status', [(None, 401), ('disabled', 403)])
def test_finale_continuation_requires_active_actor(play_http, token, status):
    client, service = play_http
    r = client.post('/api/fusion/package-plays/play-x/finale-motivations',
        headers={'Authorization': 'Bearer ' + token} if token else {})
    assert r.status_code == status and service.mock_calls == []


@pytest.mark.parametrize('entry', ['actions', 'guided', 'finale-motivations'])
def test_finale_http_automatic_continuation_and_owner(play_http, entry):
    client, service = play_http
    ready = {'play_id':'play-x','finale_motivation':{'complete':False}}
    done = {'play_id':'play-x','finale_motivation':{'complete':True}}
    service.complete_finale_motivations = AsyncMock(return_value=done)
    service.act.return_value = ready; service.guided.return_value = ready
    body = {'expected_revision': 1, 'idempotency_key':'next', 'action':'ADVANCE_PHASE'}
    if entry == 'guided': body = {'schema_version':'package-guided-command/1.0', 'expected_revision':1, 'idempotency_key':'next','action':'FINISH_INVESTIGATION'}
    r = client.post('/api/fusion/package-plays/play-x/' + entry,
        headers={'Authorization':'Bearer player'}, **({'json':body} if entry != 'finale-motivations' else {}))
    assert r.status_code == 200 and r.json()['data'] == done
    assert r.headers['Cache-Control'] == 'no-store'
    service.complete_finale_motivations.assert_awaited_once_with('play-x', 2)
    if entry != 'finale-motivations': service.db.commit.assert_called_once()
