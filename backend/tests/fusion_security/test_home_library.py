"""Account-scoped homepage metadata; synthetic saves only, never network I/O."""
from unittest.mock import patch

import pytest
from sqlalchemy import text

from src.db.models.package_play import ScriptPackagePlay, ScriptPackagePlayEvent
from src.fusion.package_play import PackagePlayError
from src.fusion.package_validation import canonical_json
from tests.fusion_security.test_package_play_http import play_http
from tests.fusion_security.test_package_play_store import play, start, action_body
from tests.fusion_security.test_package_runtime import runtime, request


def another(play, key, owner=1):
    opening = play.service.create(request(key=key), owner)
    view = play.play.create({'opening_session_id': opening['session_id'], 'idempotency_key': key}, owner)
    play.db.commit()
    return view


def snapshot(play):
    return {
        model.__tablename__: [tuple(row) for row in play.db.execute(
            text('SELECT * FROM ' + model.__tablename__ + ' ORDER BY id')).all()]
        for model in (ScriptPackagePlay, ScriptPackagePlayEvent)
    }


def test_home_library_empty_owner_and_exact_safe_projection(play):
    assert play.play.list_library(1) == {'items': [], 'has_more': False}
    opening, view = start(play)
    before = snapshot(play)
    response = play.play.list_library(1)
    assert response['has_more'] is False and len(response['items']) == 1
    item = response['items'][0]
    assert set(item) == {'play_id', 'opening_session_id', 'title', 'character_name',
                         'phase_label', 'settled', 'revision', 'updated_at'}
    assert item['play_id'] == view['play_id'] and item['opening_session_id'] == opening['session_id']
    assert item['title'] == view['script']['title'] and item['character_name'] == view['characters'][0]['name']
    assert item['phase_label'] == '阅读材料' and item['revision'] == 0 and item['settled'] is False
    assert item['updated_at'].endswith('Z')
    assert play.play.list_library(2) == {'items': [], 'has_more': False}
    assert snapshot(play) == before
    assert play.play.get(view['play_id'], 1)['budget'] == view['budget']
    play.sdk.chat_completion.assert_not_awaited()
    for forbidden in ('SENTINEL', 'budget', 'content_version', 'package_hash', 'truth', 'knowledge'):
        assert forbidden not in canonical_json(response)


def test_home_library_recent_event_sort_owner_filter_and_bounded_replay(play):
    _, first = start(play)
    second = another(play, 'second')
    third = another(play, 'third')
    foreign = another(play, 'foreign', owner=2)
    # Equal timestamps still have stable newest-created ordering, and an event
    # can make an older save become the first continue entry.
    for view in (first, second, third, foreign):
        play.db.execute(text('UPDATE script_package_plays SET created_at=:at WHERE play_id=:id'),
                        {'at': '2020-01-01 00:00:00', 'id': view['play_id']})
    play.db.commit()
    assert [i['play_id'] for i in play.play.list_library(1)['items']] == [third['play_id'], second['play_id'], first['play_id']]
    advanced = play.play.act(first['play_id'], action_body(action='ADVANCE_PHASE'), 1)
    play.db.commit()
    before = snapshot(play)
    with patch.object(play.play, 'get', wraps=play.play.get) as get:
        page = play.play.list_library(1, 0, 2)
        assert get.call_count == 2
    assert page['has_more'] is True
    assert [i['play_id'] for i in page['items']] == [first['play_id'], third['play_id']]
    assert page['items'][0]['revision'] == advanced['revision']
    assert page['items'][0]['phase_label'] == '终局答卷'
    last = play.play.list_library(1, 2, 2)
    assert [i['play_id'] for i in last['items']] == [second['play_id']] and last['has_more'] is False
    assert play.play.list_library(1, 999, 20) == {'items': [], 'has_more': False}
    assert [i['play_id'] for i in play.play.list_library(2)['items']] == [foreign['play_id']]
    assert snapshot(play) == before
    play.sdk.chat_completion.assert_not_awaited()


def test_home_library_settled_save_omits_unlocked_truth_and_does_not_write(play):
    _, view = start(play)
    play.play.act(view['play_id'], action_body(action='ADVANCE_PHASE'), 1)
    settled = play.play.act(view['play_id'], action_body(1, 'settle', 'SETTLE'), 1)
    play.db.commit()
    assert 'SYSTEM_TRUTH_SENTINEL' in canonical_json(settled)
    before = snapshot(play)
    library = play.play.list_library(1)
    assert library['items'][0]['settled'] is True
    assert library['items'][0]['phase_label'] == '已结束'
    assert library['items'][0]['revision'] == 2
    assert 'SENTINEL' not in canonical_json(library)
    assert snapshot(play) == before
    play.sdk.chat_completion.assert_not_awaited()


@pytest.mark.parametrize('kind,settled,label', [
    ('READING', False, '阅读材料'), ('INVESTIGATION', False, '共同调查'),
    ('FINALE', False, '终局答卷'), ('FINALE', True, '已结束'),
])
def test_home_library_never_uses_private_phase_title(play, kind, settled, label):
    _, view = start(play)
    view['full_game'] = {'phase_kind': kind}
    view['settled'] = settled
    view['current_phase']['title'] = 'PRIVATE_STAGE_INTERNAL_SENTINEL'
    with patch.object(play.play, 'get', return_value=view):
        response = play.play.list_library(1)
    assert response['items'][0]['phase_label'] == label
    assert 'SENTINEL' not in canonical_json(response)


@pytest.mark.parametrize('offset,limit', [(-1, 20), (True, 20), (0, 0), (0, 51), (0, True), ('0', 20)])
def test_home_library_service_rejects_invalid_pagination(play, offset, limit):
    with pytest.raises(PackagePlayError, match='PACKAGE_PLAY_LIBRARY_PAGE_INVALID') as exc:
        play.play.list_library(1, offset, limit)
    assert exc.value.status_code == 422


@pytest.mark.parametrize('token,status', [(None, 401), ('disabled', 403)])
def test_home_library_http_requires_active_actor(play_http, token, status):
    client, service = play_http
    response = client.get('/api/fusion/package-play-library',
                          headers={'Authorization': 'Bearer ' + token} if token else {})
    assert response.status_code == status and response.headers['Cache-Control'] == 'no-store'
    assert service.mock_calls == []


def test_home_library_http_dispatch_identity_paging_no_store_and_old_lookup(play_http):
    client, service = play_http
    service.list_library.return_value = {'items': [], 'has_more': False}
    response = client.get('/api/fusion/package-play-library?offset=3&limit=2&owner_user_id=1',
                          headers={'Authorization': 'Bearer player'})
    assert response.status_code == 200 and response.headers['Cache-Control'] == 'no-store'
    assert response.json() == {'success': True, 'data': {'items': [], 'has_more': False}}
    service.list_library.assert_called_once_with(2, 3, 2)
    service.find_for_opening.assert_not_called()


@pytest.mark.parametrize('query', ['offset=-1', 'limit=0', 'limit=51', 'limit=true',
                                   'offset=1.5', 'offset=private-sentinel', 'limit=', 'offset=' + '9' * 100])
def test_home_library_http_invalid_page_is_private_and_does_not_dispatch(play_http, query):
    client, service = play_http
    response = client.get('/api/fusion/package-play-library?' + query,
                          headers={'Authorization': 'Bearer player'})
    assert response.status_code == 422 and response.headers['Cache-Control'] == 'no-store'
    assert response.json() == {'detail': 'PACKAGE_PLAY_LIBRARY_PAGE_INVALID'}
    service.list_library.assert_not_called()


def test_home_library_http_replay_failure_has_no_private_details(play_http):
    client, service = play_http
    service.list_library.side_effect = PackagePlayError('PACKAGE_PLAY_STATE_CORRUPT')
    response = client.get('/api/fusion/package-play-library', headers={'Authorization': 'Bearer player'})
    assert response.status_code == 409 and response.headers['Cache-Control'] == 'no-store'
    assert response.json() == {'detail': 'PACKAGE_PLAY_STATE_CORRUPT'}
