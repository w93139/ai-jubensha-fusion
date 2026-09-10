"""Fictional multi-role registries: exact selection and new-version isolation."""
import asyncio
from copy import deepcopy

import pytest

from src.db.models.package_play import ScriptPackagePlay
from src.fusion.package_guided_flow import GuidedFlowContent
from src.fusion.package_play import PackagePlayError
from src.fusion.package_role_content import resolve_role_content, role_content_required
from src.fusion.package_runtime import PackageRuntimeError, PackageRuntimeService
from src.fusion.package_single_player import SinglePlayerContent
from src.fusion.package_validation import content_hash
from tests.fusion_security.test_opening_single_player import manual_package
from tests.fusion_security.test_package_full_play import REFS
from tests.fusion_security.test_package_guided_flow import content, enter, run
from tests.fusion_security.test_package_play_store import play, service, events, ask_body
from tests.fusion_security.test_package_runtime import runtime, publish, request


SEATS = ('a', 'b', 'c', 'd', 'e')


def plans(package):
    singles, guides = {}, {}
    for role in SEATS:
        singles[role] = SinglePlayerContent(package, {
            'schema_version': 'single-player-content/1.0', 'package_hash': content_hash(package),
            'selected_character_id': role, 'operation_rules': f'ROLE_{role}_MANUAL',
            'replaces_materials': [{'collection': 'knowledge', 'id': identifier}
                                  for identifier in ('old-rules-one', 'old-rules-two')],
            'phase_guides': [{'phase_id': phase['id'], 'goal': f'ROLE_{role}_GOAL',
                             'instructions': ['核对本人的材料。'], 'completion': '可以推进。',
                             'source_refs': deepcopy(REFS)} for phase in package['phases']],
            'topics': [],
        })
        doc = content(package)
        doc['selected_character_id'] = role
        for topic in doc['host_topics']:
            topic['title'] = f'ROLE_{role}_HINT'
            for level in topic['levels']:
                level['text'] = f"ROLE_{role}_ANSWER_{level['level']}"
        guides[role] = GuidedFlowContent(package, doc)
    return singles, guides


def create_role(play, release, role, prefix='new'):
    opening = play.service.create(request(release['id'], role, f'{prefix}-opening-{role}'), 1)
    view = play.play.create({'opening_session_id': opening['session_id'],
                             'idempotency_key': f'{prefix}-play-{role}'}, 1)
    return opening, view


def test_all_roles_get_exact_opening_manual_game_goal_and_hint_and_restore(play):
    package = manual_package()
    single, guided = plans(package)
    key = content_hash(package)
    play.play.single_player_content = {key: single}
    play.play.guided_content = {key: guided}
    play.play.single_player_required = {key: set(SEATS)}
    play.service = PackageRuntimeService(play.db, play.publisher,
        single_player_content={key: single}, single_player_required={key: frozenset(SEATS)})
    release = publish(play, package)
    views = []
    for role in SEATS:
        opening, view = create_role(play, release, role)
        assert [item['text'] for item in opening['public_knowledge']] == [single[role].rules]
        assert [item['text'] for item in opening['private_knowledge']] == [f'PRIVATE_BOOK_{role}']
        assert view['single_player']['stage']['goal'] == f'ROLE_{role}_GOAL'
        assert view['guided_play']['available']
        assert view['host_hints']['topics'][0]['title'] == f'ROLE_{role}_HINT'
        view = run(play, view, 'REQUEST_HINT', {'topic_id': 'hint-read-one', 'level': 1})
        assert view['host_hints']['entries'][-1]['text'] == f'ROLE_{role}_ANSWER_1'
        assert view['selected_character_id'] == role
        views.append(view)
    play.db.commit()
    restored = service(play, play.db)
    restored.single_player_content = {key: single}
    restored.guided_content = {key: guided}
    restored.single_player_required = {key: set(SEATS)}
    for view in views:
        assert restored.get(view['play_id'], 1) == view
    assert play.sdk.chat_completion.await_count == 0


@pytest.mark.parametrize('nested', [False, True])
@pytest.mark.parametrize('invalid', ['missing', 'wrong-role', 'wrong-hash', 'malformed'])
def test_invalid_catalogues_do_not_cross_binding_and_keep_required_gate(play, nested, invalid):
    package = manual_package()
    key = content_hash(package)
    release = publish(play, package)
    opening, view = create_role(play, release, 'a')
    other = deepcopy(package)
    other['content_version'] = 'unrelated-v2'
    if invalid == 'missing':
        single = guided = None
    elif invalid == 'malformed':
        single = guided = object()
    else:
        target = other if invalid == 'wrong-hash' else package
        role = 'b' if invalid == 'wrong-role' else 'a'
        singles, guides = plans(target)
        single, guided = singles[role], guides[role]
    play.play.single_player_content = {key: {'a': single} if nested else single}
    play.play.guided_content = {key: {'a': guided} if nested else guided}
    play.play.single_player_required = {key: {'a', 'b'}}
    opening_service = PackageRuntimeService(play.db, play.publisher,
        single_player_content=play.play.single_player_content, single_player_required={key: {'a', 'b'}})
    with pytest.raises(PackageRuntimeError, match='PACKAGE_OPENING_SINGLE_PLAYER_UNAVAILABLE'):
        opening_service.get(opening['session_id'], 1)
    view = enter(play, view)
    assert view['single_player']['available'] is False
    assert view['single_player']['stage'] is None
    assert view.get('host_hints', {}).get('topics', []) == []
    assert not view.get('guided_play', {}).get('available')
    with pytest.raises(PackagePlayError, match='SINGLE_TOPIC_REQUIRED'):
        asyncio.run(play.play.ask(view['play_id'], ask_body(view['revision']), 1))
    with pytest.raises(PackagePlayError, match='GUIDED_PLAY_NOT_AVAILABLE'):
        run(play, view, 'REQUEST_HINT', {'topic_id': 'hint-investigate-one', 'level': 1})
    assert play.sdk.chat_completion.await_count == 0


def test_adding_new_version_role_maps_preserves_old_registry_bindings_views_and_events(play):
    old = manual_package()
    old_key = content_hash(old)
    singles, guides = plans(old)
    play.play.single_player_content = {old_key: singles['a']}
    play.play.guided_content = {old_key: guides['a']}
    play.play.single_player_required = {old_key: 'a'}
    old_release = publish(play, old)
    _, old_a = create_role(play, old_release, 'a', 'old')
    old_a = run(play, old_a, 'REQUEST_HINT', {'topic_id': 'hint-read-one', 'level': 1})
    _, old_b = create_role(play, old_release, 'b', 'old')
    assert 'single_player' not in old_b and 'guided_play' not in old_b
    play.db.commit()
    prior_rows = [(row.play_id, row.binding_hash, row.binding_json) for row in play.db.query(ScriptPackagePlay).all()]
    prior_events = [(row.id, row.event_json, row.request_json, row.state_hash) for row in events(play)]
    new = deepcopy(old)
    new['content_version'] = 'm4-role-adaptation-v2'
    new_key = content_hash(new)
    new_single, new_guided = plans(new)
    play.play.single_player_content[new_key] = new_single
    play.play.guided_content[new_key] = new_guided
    play.play.single_player_required[new_key] = set(SEATS)
    new_release = publish(play, new)
    _, new_b = create_role(play, new_release, 'b')
    assert new_b['single_player']['stage']['goal'] == 'ROLE_b_GOAL'
    assert new_b['guided_play']['available']
    assert new_b['package_hash'] != old_b['package_hash']
    for view in (old_a, old_b):
        assert play.play.get(view['play_id'], 1) == view
    assert play.play.single_player_content[old_key] is singles['a']
    assert play.play.guided_content[old_key] is guides['a']
    assert play.play.single_player_required[old_key] == 'a'
    current_rows = {row.play_id: (row.play_id, row.binding_hash, row.binding_json) for row in play.db.query(ScriptPackagePlay).all()}
    assert [current_rows[row[0]] for row in prior_rows] == prior_rows
    assert [(row.id, row.event_json, row.request_json, row.state_hash) for row in events(play)] == prior_events
    assert play.sdk.chat_completion.await_count == 0


def test_role_map_never_falls_back_to_other_role_or_another_package():
    package = manual_package()
    singles, _ = plans(package)
    key = content_hash(package)
    for registry in ({key: singles['b']}, {key: {'b': singles['b']}}, {key: {'a': singles['b']}}):
        assert resolve_role_content(registry, key, 'a') is None
    assert resolve_role_content({key: singles}, 'unregistered-hash', 'a') is None
    assert resolve_role_content({key: singles['a']}, key, 'a') is singles['a']
    for required in ('a', {'a', 'b'}, frozenset({'a', 'b'})):
        assert role_content_required({key: required}, key, 'a')
        assert not role_content_required({key: required}, key, 'c')
        assert not role_content_required({key: required}, 'other-hash', 'a')
    assert not role_content_required({key: 'aa'}, key, 'a')
