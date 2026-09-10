"""Automatic remaining searches and authorized round records, no live services."""
from copy import deepcopy
import asyncio

import pytest

from src.fusion.package_play import PackagePlayError
from src.fusion.package_guided_flow import GuidedFlowContent
from src.fusion.package_validation import canonical_json, content_hash
from tests.fusion_security.test_package_play_store import play, service, start, events
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_guided_flow import setup, enter, run, guided, content
from tests.fusion_security.test_package_full_play import full_package
from tests.fusion_security.test_full_play_store import command


def test_batch_selected_then_auto_unlock_and_no_automatic_phase_advance(play):
    _, view = setup(play, True)
    view = enter(play, view)
    previous = [(e.event_json, e.request_json) for e in events(play)]
    request = guided(view, 'INVESTIGATE_ROUND', {'action_ids': ['find-key']})
    view = play.play.guided(view['play_id'], request, 1)
    assert view['current_phase']['id'] == 'investigate-one'
    assert view['mechanics']['remaining_points'] == 0
    assert not view['guided_play']['can_investigate_round']
    assert view['guided_play']['can_finish_investigation']
    phase = view['round_workspace']['phases'][-1]
    assert [(i['action_id'], i['character_id'], i['mode'], i['cost']) for i in phase['investigations']] == [
        ('find-key', 'a', 'PLAYER', 1), ('open-box', 'b', 'AUTO', 1)]
    assert {m['id'] for m in phase['materials']} >= {'evidence-find-key', 'evidence-open-box', 'memory-a'}
    assert not phase['private_message_ids']
    assert view['full_game']['ballot'] is None
    assert [(e.event_json, e.request_json) for e in events(play)[:len(previous)]] == previous
    assert play.play.guided(view['play_id'], request, 1) == view
    assert play.sdk.chat_completion.await_count == 0
    play.db.commit()
    restored = service(play, play.db).get(view['play_id'], 1)
    assert restored['round_workspace'] == view['round_workspace']
    assert restored['mechanics'] == view['mechanics']
    assert restored['guided_play']['available'] is False  # missing catalog doesn't erase history


def test_empty_selection_delegates_every_action_and_rotates_roles(play):
    _, view = setup(play)
    view = run(play, enter(play, view), 'INVESTIGATE_ROUND', {'action_ids': []})
    assert [(i['character_id'], i['mode']) for i in view['round_workspace']['phases'][-1]['investigations']] == [('b', 'AUTO'), ('c', 'AUTO')]
    assert view['mechanics']['spent_points'] == 2


@pytest.mark.parametrize('selection', [['find-key', 'find-key'], ['find-key', 'open-box'], ['look-note'], ['missing']])
def test_invalid_batch_is_atomic_and_does_not_spend_or_unlock(play, selection):
    _, view = setup(play)
    view = enter(play, view)
    before = [(e.event_json, e.request_json) for e in events(play)]
    with pytest.raises(PackagePlayError):
        run(play, view, 'INVESTIGATE_ROUND', {'action_ids': selection})
    assert [(e.event_json, e.request_json) for e in events(play)] == before
    assert play.play.get(view['play_id'], 1) == view
    assert play.sdk.chat_completion.await_count == 0


def test_batch_over_budget_rejects_entire_selection(play):
    package = full_package()
    package['mechanics']['phase_budgets'][1]['points'] = 1
    package['mechanics']['actions'][1]['required_action_ids'] = []
    play.play.guided_content = {content_hash(package): GuidedFlowContent(package, content(package))}
    _, view = start(play, package)
    view = enter(play, view)
    with pytest.raises(PackagePlayError, match='ROUND_SELECTION_INVALID'):
        run(play, view, 'INVESTIGATE_ROUND', {'action_ids': ['find-key', 'open-box']})
    assert play.play.get(view['play_id'], 1)['mechanics']['spent_points'] == 0


def test_batch_keeps_unspendable_balance_and_honors_authored_order(play):
    package = full_package()
    package['mechanics']['phase_budgets'][1]['points'] = 3
    play.play.guided_content = {content_hash(package): GuidedFlowContent(package, content(package))}
    _, view = start(play, package)
    view = run(play, enter(play, view), 'INVESTIGATE_ROUND', {'action_ids': []})
    assert view['mechanics']['remaining_points'] == 1
    assert view['mechanics']['available_actions'] == []
    assert view['current_phase']['id'] == 'investigate-one'
    assert run(play, view, 'FINISH_INVESTIGATION')['current_phase']['id'] == 'read-two'


def test_batch_does_not_use_acquired_card_as_heard_speech(play):
    _, view = setup(play)
    view = run(play, enter(play, view), 'INVESTIGATE_ROUND', {'action_ids': []})
    assert view['memories']['entries'] == []
    assert view['discussion']['entries'] == []


def test_batch_rejects_out_of_phase_private_call_stale_and_wrong_owner(play):
    _, view = setup(play)
    with pytest.raises(PackagePlayError): run(play, view, 'INVESTIGATE_ROUND', {'action_ids': []})
    view = enter(play, view)
    request = guided(view, 'INVESTIGATE_ROUND', {'action_ids': []})
    with pytest.raises(PackagePlayError, match='NOT_FOUND'): play.play.guided(view['play_id'], request, 2)
    view = play.play.table(view['play_id'], command(view['revision'], 'START_CALL', {'peer_character_id': 'b'}), 1)
    with pytest.raises(PackagePlayError, match='REVISION_CONFLICT'): play.play.guided(view['play_id'], request, 1)
    with pytest.raises(PackagePlayError): run(play, view, 'INVESTIGATE_ROUND', {'action_ids': []})


def test_round_projection_only_visited_and_authorized_refs_with_private_channel(play):
    _, view = setup(play)
    assert [p['phase_id'] for p in view['round_workspace']['phases']] == ['read-one']
    assert view['round_workspace']['phases'][0]['materials'] == [{'collection': 'knowledge', 'id': 'initial-a', 'sequence': 0}]
    assert 'initial-b' not in canonical_json(view['round_workspace'])
    view = enter(play, view)
    view = play.play.table(view['play_id'], command(view['revision'], 'START_CALL', {'peer_character_id': 'b'}), 1)
    view = play.play.table(view['play_id'], command(view['revision'], 'PRIVATE_SPEAK', {'text': '我的判断尚未确定。'}), 1)
    phase = view['round_workspace']['phases'][-1]
    assert phase['statement_ids'] == [] and len(phase['private_message_ids']) == 1
    assert '我的判断' not in canonical_json(view['round_workspace'])  # references, not duplicated copies
    play.db.commit()
    restored = service(play, play.db)
    restored.guided_content = play.play.guided_content
    assert restored.get(view['play_id'], 1)['round_workspace'] == view['round_workspace']


def test_previous_single_search_has_honest_legacy_attribution(play):
    _, view = setup(play)
    view = run(play, enter(play, view), 'INVESTIGATE', {'action_id': 'find-key'})
    assert view['round_workspace']['phases'][-1]['investigations'][0]['mode'] == 'LEGACY'
    old = deepcopy(view['round_workspace']['phases'])
    view = run(play, view, 'FINISH_INVESTIGATION')
    assert view['round_workspace']['phases'][:-1] == old


def test_failed_retelling_rolls_back_entire_batch(play, monkeypatch):
    _, view = setup(play)
    view = enter(play, view)
    previous = [e.event_json for e in events(play)]
    def fail(*args):
        raise PackagePlayError('FULL_PLAY_EVENT_LIMIT')
    monkeypatch.setattr(play.play, '_present_required', fail)
    with pytest.raises(PackagePlayError): run(play, view, 'INVESTIGATE_ROUND', {'action_ids': []})
    assert [e.event_json for e in events(play)] == previous
    assert play.play.get(view['play_id'], 1) == view


def test_one_point_can_grant_two_cards_and_frozen_receipt_records_both(play):
    package = full_package()
    extra = deepcopy(package['evidence'][0]); extra['id'] = 'evidence-supplement'
    package['evidence'].append(extra)
    play.play.guided_content = {content_hash(package): GuidedFlowContent(package, content(package))}
    _, view = start(play, package)
    view = run(play, enter(play, view), 'INVESTIGATE_ROUND', {'action_ids': ['find-key']})
    step = view['round_workspace']['phases'][-1]['investigations'][0]
    assert step['cost'] == 1
    assert {r['id'] for r in step['materials']} == {'evidence-find-key', 'evidence-supplement'}


def test_recap_includes_normal_model_answer_and_explicit_fallback_in_order(play):
    from tests.fusion_security.test_single_player_topics import setup as single_setup, ask_topic, fallback
    from tests.fusion_security.test_full_play_decisions import output
    _, _, view = single_setup(play)
    view = enter(play, view)
    _, view = ask_topic(play, view)
    play.sdk.chat_completion.return_value = output({'segments': [{'text': '我见过三角木片，其他还不能确定。',
        'mode': 'REPORT', 'basis': [{'collection': 'knowledge', 'id': 'initial-b'}]}]})
    view = asyncio.run(play.play.respond(view['play_id'], view['single_player']['turns'][-1]['reply_request'], 1))
    assert view['last_ai_status'] == 'OK'
    answer = view['role_responses']['entries'][-1]
    assert answer['id'] in view['round_workspace']['phases'][-1]['statement_ids']
    _, view = ask_topic(play, view, 'clarify')
    play.sdk.chat_completion.return_value = output({'invalid': True})
    view = asyncio.run(play.play.respond(view['play_id'], view['single_player']['turns'][-1]['reply_request'], 1))
    _, view = fallback(play, view)
    identifiers = view['round_workspace']['phases'][-1]['statement_ids']
    assert len(identifiers) == 4
    assert identifiers.index(answer['id']) < identifiers.index(view['discussion']['entries'][-1]['id'])
    play.db.commit()
    assert service(play, play.db).get(view['play_id'], 1)['round_workspace'] == view['round_workspace']


def test_raw_legacy_full_view_unchanged_until_workspace_presentation_opt_in(play):
    package = full_package()
    _, view = start(play, package)
    assert 'round_workspace' not in view
    play.play.include_interactions = True
    unchanged = play.play.get(view['play_id'], 1)
    assert {k: v for k, v in unchanged.items() if k != 'ai_interactions'} == view
    play.play.single_player_required = {content_hash(package): 'a'}
    current = play.play.get(view['play_id'], 1)
    assert current['round_workspace']['phases'][0]['phase_id'] == 'read-one'
    assert {k: v for k, v in current.items() if k not in ('round_workspace', 'ai_interactions', 'single_player')} == view
