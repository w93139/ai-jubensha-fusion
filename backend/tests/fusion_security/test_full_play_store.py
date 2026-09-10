"""Persistent human full-game commands; fictional publisher and no model calls."""
import json
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sqlalchemy import update

from src.db.models.package_play import ScriptPackagePlayEvent
from src.fusion.package_play import PackagePlayError
from src.fusion.package_play import PackagePlayService
from src.fusion.package_play_engine import play_engine
from src.fusion.package_validation import canonical_json
from tests.fusion_security.test_package_play_store import play, service, start, events, action_body
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_full_play import full_package
from tests.fusion_security.test_package_memories import memory_package


def command(revision, action, payload=None, key=None):
    value = {'schema_version': 'package-full-play-command/1.0', 'expected_revision': revision,
             'idempotency_key': key or f'full-{revision}', 'action': action}
    if payload is not None: value['payload'] = payload
    return value


def test_full_play_private_human_event_recovers_and_never_becomes_public(play):
    opening, initial = start(play, full_package())
    assert opening['supports_rules_preview'] is False
    identifier = initial['play_id']
    view = play.play.act(identifier, action_body(0, 'advance', 'ADVANCE_PHASE'), 1)
    view = play.play.table(identifier, command(1, 'START_CALL', {'peer_character_id': 'b'}), 1)
    request = command(2, 'PRIVATE_SPEAK', {'text': '私密三角木片'})
    view = play.play.table(identifier, request, 1)
    assert view['revision'] == 3 and view['discussion']['entries'] == []
    assert view['full_game']['private_discussion'][0]['text'] == '私密三角木片'
    assert view['memories']['entries'] == []  # speaker never triggers their own memory
    play.db.commit()
    with play.factory() as db: assert service(play, db).get(identifier, 1) == view
    assert play.play.table(identifier, request, 1) == view and len(events(play)) == 3
    assert {json.loads(e.event_json)['schema_version'] for e in events(play)} == {'package-text-play-event/1.5'}
    assert play.sdk.chat_completion.await_count == 0


def test_full_play_human_ballot_preserves_explicit_abstention_and_cannot_spoof_ai(play):
    _, initial = start(play, full_package()); identifier = initial['play_id']
    play.play.act(identifier, action_body(0, 'advance', 'ADVANCE_PHASE'), 1)
    play.play.table(identifier, command(1, 'OPEN_BALLOT'), 1)
    value = command(2, 'CAST_BALLOT', {'kind': 'ABSTAIN', 'choice_id': None})
    view = play.play.table(identifier, value, 1)
    assert view['full_game']['ballot']['ballot'] == value['payload']
    assert view['full_game']['ballot']['status'] == 'WAITING' and view['mechanics']['spent_points'] == 0
    play.db.commit()
    with play.factory() as db: assert service(play, db).get(identifier, 1) == view
    forged = command(3, 'CAST_BALLOT', {'kind': 'SKIP', 'choice_id': None}); forged['character_id'] = 'b'
    with pytest.raises(PackagePlayError, match='REQUEST_INVALID'): play.play.table(identifier, forged, 1)
    with pytest.raises(PackagePlayError, match='ALREADY_SEALED'): play.play.table(identifier, command(3, 'CAST_BALLOT', {'kind': 'SKIP', 'choice_id': None}), 1)
    assert len(events(play)) == 3


def test_full_play_foreign_owner_old_package_and_stale_revision_are_rejected(play):
    _, initial = start(play, full_package()); identifier = initial['play_id']
    with pytest.raises(PackagePlayError, match='NOT_FOUND'): play.play.table(identifier, command(0, 'OPEN_BALLOT'), 2)
    with pytest.raises(PackagePlayError, match='REVISION_CONFLICT'): play.play.table(identifier, command(1, 'OPEN_BALLOT'), 1)
    assert events(play) == []


def test_full_play_old_version_cannot_receive_new_actions(play):
    _, initial = start(play, memory_package())
    with pytest.raises(PackagePlayError, match='UNSUPPORTED'): play.play.table(initial['play_id'], command(0, 'OPEN_BALLOT'), 1)
    assert events(play) == []


def test_full_play_tampered_private_speech_is_not_a_recoverable_public_claim(play):
    _, initial = start(play, full_package()); identifier = initial['play_id']
    play.play.act(identifier, action_body(0, 'advance', 'ADVANCE_PHASE'), 1)
    play.play.table(identifier, command(1, 'START_CALL', {'peer_character_id': 'b'}), 1)
    play.play.table(identifier, command(2, 'PRIVATE_SPEAK', {'text': '原私聊'}), 1)
    play.db.commit(); event = events(play)[-1]
    request = json.loads(event.request_json); request['payload']['text'] = '篡改成公开'
    play.db.execute(update(ScriptPackagePlayEvent).where(ScriptPackagePlayEvent.id == event.id).values(request_json=canonical_json(request)))
    play.db.commit()
    with pytest.raises(PackagePlayError, match='HISTORY_INVALID'): play.play.get(identifier, 1)


@pytest.mark.parametrize('revision,pending,kind,allowed', [
    (1998, False, 'AI_REQUEST', True), (1999, False, 'AI_REQUEST', False),
    (1998, True, 'ACTION', True), (1999, True, 'ACTION', False),
    (1999, True, 'AI_RESULT', True), (2000, True, 'AI_RESULT', False),
    (1999, False, 'ACTION', True), (2000, False, 'ACTION', False),
])
def test_full_play_event_limit_reserves_result_slot(revision, pending, kind, allowed):
    instance = object.__new__(PackagePlayService)
    instance.db = Mock(); instance.db.begin_nested.side_effect = lambda: nullcontext()
    instance._consume = Mock()
    state = SimpleNamespace(engine=play_engine(full_package(), 'a'), revision=revision,
        pending={'reserved': {}} if pending else {}, previous='0' * 64, state=lambda: {}, phone_model=None)
    row = SimpleNamespace(play_id='play-' + '1' * 32)
    if allowed:
        instance._append(row, {}, state, kind, {'idempotency_key': 'boundary'}, {})
        assert state.revision == revision + 1 and instance._consume.call_count == 1
    else:
        with pytest.raises(PackagePlayError, match='EVENT_LIMIT'):
            instance._append(row, {}, state, kind, {'idempotency_key': 'boundary'}, {})
        assert state.revision == revision and instance._consume.call_count == 0


@pytest.mark.parametrize('status', ['OK', 'UNKNOWN', 'EXPIRED'])
def test_full_play_last_slot_can_record_any_terminal_receipt(status):
    instance = object.__new__(PackagePlayService)
    instance.db = Mock(); instance.db.begin_nested.side_effect = lambda: nullcontext()
    state = SimpleNamespace(engine=play_engine(full_package(), 'a'), revision=1998, pending={},
                            previous='0' * 64, state=lambda: {}, phone_model=None)
    def consume(current, kind, request, data, binding):
        if kind == 'AI_REQUEST': current.pending['request'] = {}
        elif kind == 'AI_RESULT': current.pending.clear()
    instance._consume = consume
    row = SimpleNamespace(play_id='play-' + '1' * 32)
    instance._append(row, {}, state, 'AI_REQUEST', {'idempotency_key': 'request'}, {})
    with pytest.raises(PackagePlayError, match='EVENT_LIMIT'):
        instance._append(row, {}, state, 'ACTION', {'idempotency_key': 'competing'}, {})
    instance._append(row, {}, state, 'AI_RESULT', {'idempotency_key': 'request'}, {'status': status})
    assert state.revision == 2000 and not state.pending
