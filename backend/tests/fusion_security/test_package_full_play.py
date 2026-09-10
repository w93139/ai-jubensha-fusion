"""Five-seat, two-investigation full-rule fixture; no real script or model."""
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator

from src.fusion.package_play_engine import play_engine
from src.fusion.package_play_rules import PlayRulesError
from src.fusion.package_validation import validate_package, canonical_json
from src.schemas.script_package import package_json_schema
from tests.fusion_security.test_structured_finale import plan as finale_plan, submission


SEATS = ['a', 'b', 'c', 'd', 'e']
REFS = [{'source_id': 'fiction', 'anchor': 'L1'}]


def full_package():
    phase_ids = ['read-one', 'investigate-one', 'read-two', 'investigate-two', 'finale']
    package = {'schema_version': 'script-package/1.4', 'script_key': 'fictional-full-game', 'content_version': 'v1',
        'title': '虚构五席完整规则验证', 'player_count': 5,
        'sources': [{'id': 'fiction', 'relative_path': 'fiction.txt', 'sha256': '1' * 64,
                     'kind': 'original', 'media_type': 'text/plain', 'original_source_ids': []}],
        'introduction': {'text': '虚构规则测试，不含商业正文。', 'sources': deepcopy(REFS)},
        'characters': [{'id': s, 'name': s, 'sources': deepcopy(REFS)} for s in SEATS],
        'initial_phase_id': phase_ids[0], 'phases': [{'id': p, 'title': p,
            'next_phase_id': phase_ids[i + 1] if i + 1 < len(phase_ids) else None, 'sources': deepcopy(REFS)} for i, p in enumerate(phase_ids)],
        'knowledge': [{'id': f'initial-{s}', 'text': f'PRIVATE_BOOK_{s}', 'kind': 'FACT',
                       'visibility': 'CHARACTER_PRIVATE', 'character_id': s, 'disclosure': 'KEEP_PRIVATE',
                       'release': {'phase_id': 'read-one'}, 'sources': deepcopy(REFS)} for s in SEATS],
        'evidence': [], 'truth': [{'id': t, 'text': f'SYSTEM_TRUTH_{t}', 'visibility': 'SYSTEM_TRUTH',
                                  'sources': deepcopy(REFS)} for t in ['truth-main', 'truth-solved', 'truth-unsolved']],
        'settlement': {'phase_id': 'finale', 'truth_ids': ['truth-main'],
                       'instructions': {'text': '现在揭晓。', 'sources': deepcopy(REFS)}},
        'mechanics': {'phase_budgets': [{'phase_id': p, 'points': points, 'advance_policy': 'ALLOW_REMAINING',
            'origin': 'SOURCE_EXPLICIT', 'sources': deepcopy(REFS)} for p, points in zip(phase_ids, [0, 2, 0, 1, 0])], 'actions': []},
        'memories': [{'id': f'memory-{s}', 'character_id': s, 'phase_id': 'read-one', 'title': '私人回忆',
            'text': f'PRIVATE_MEMORY_{s}', 'kind': 'CLAIM', 'card_disclosure': 'KEEP_PRIVATE', 'retelling': 'MAY_RETELL',
            'triggers': [{'kind': 'OTHER_HEARD_SPEECH', 'keywords': ['三角木片']}],
            'origin': 'SOURCE_EXPLICIT', 'sources': deepcopy(REFS)} for s in SEATS],
        'full_play': {'phases': [{'phase_id': p, 'kind': kind, 'sources': deepcopy(REFS)} for p, kind in zip(phase_ids,
                       ['READING', 'INVESTIGATION', 'READING', 'INVESTIGATION', 'FINALE'])],
                      'action_order': [], 'finale': finale_plan(), 'sources': deepcopy(REFS)}}
    for i, (identifier, phase, required) in enumerate([('find-key', 'investigate-one', []),
        ('open-box', 'investigate-one', ['find-key']), ('look-note', 'investigate-two', [])]):
        package['mechanics']['actions'].append({'id': identifier, 'label': identifier, 'cost': 1,
            'phase_ids': [phase], 'allowed_character_ids': SEATS[:], 'required_action_ids': required,
            'origin': 'SOURCE_EXPLICIT', 'sources': deepcopy(REFS)})
        package['full_play']['action_order'].append({'action_id': identifier, 'order': i})
        package['evidence'].append({'id': f'evidence-{identifier}', 'text': identifier, 'visibility': 'PUBLIC',
            'character_id': None, 'disclosure': 'PUBLIC', 'release': {'phase_id': phase, 'required_action_ids': [identifier]},
            'sources': deepcopy(REFS)})
    return package


def advance(game):
    game.apply('ADVANCE_PHASE'); game.observe_event(game.state()['memory_observed_sequence'] + 1)


def action(game, kind, actor='a', payload=None):
    sequence = game.state()['memory_observed_sequence'] + 1
    game.table_apply(kind, actor, payload, sequence); game.observe_event(sequence)


def investigate(game, identifier=None):
    action(game, 'OPEN_BALLOT')
    for s in SEATS:
        action(game, 'CAST_BALLOT', s, {'kind': 'CHOOSE' if identifier else 'SKIP', 'choice_id': identifier})


def test_full_package_explicit_contract_and_five_phase_rule_validation():
    package = full_package(); report = validate_package(package)
    assert report['valid'], report
    assert report['contract_version'] == 'script-package/1.4'
    assert report['validator_version'] == 'script-package-validator/1.4'
    assert not report['publication_ready']
    Draft202012Validator(package_json_schema('script-package/1.4')).validate(package)
    assert play_engine(package, 'a').view()['full_game']['phase_kind'] == 'READING'
    with pytest.raises(PlayRulesError): play_engine(package, 'a', 'package-play-rules/1.2')
    package['schema_version'] = 'script-package/1.3'
    assert not validate_package(package)['valid']


def test_full_collective_cost_grants_skip_reading_and_atomic_finale_to_ending():
    game = play_engine(full_package(), 'a')
    assert game.view()['can_advance']
    advance(game)
    assert not game.view()['can_advance']
    with pytest.raises(PlayRulesError, match='COLLECTIVE'): game.apply('PERFORM_ACTION', {'action_id': 'find-key'})
    action(game, 'OPEN_BALLOT')
    for s in SEATS[:-1]: action(game, 'CAST_BALLOT', s, {'kind': 'CHOOSE', 'choice_id': 'find-key'})
    assert game.view()['mechanics']['spent_points'] == 0
    action(game, 'CAST_BALLOT', 'e', {'kind': 'CHOOSE', 'choice_id': 'find-key'})
    assert game.view()['mechanics']['spent_points'] == 1
    assert game.state()['completed_action_ids'] == ['find-key']
    investigate(game, 'open-box')
    assert game.view()['can_advance'] and game.state()['full_play']['turn'] == 2
    advance(game)
    assert game.view()['full_game']['phase_kind'] == 'READING'
    advance(game); investigate(game)
    assert game.view()['mechanics']['spent_points'] == 0
    assert game.state()['full_play']['turn'] == 2  # skip is not a newly resolved investigation
    advance(game)
    assert game.view()['full_game']['finale']['questions']
    with pytest.raises(PlayRulesError, match='FINALE_INCOMPLETE'): game.apply('SETTLE')
    for s in SEATS: action(game, 'SEAL_FINALE', s, submission(s))
    game.apply('SETTLE')
    result = game.view()
    assert result['settled'] and result['full_game']['result']['endings'][0]['ending_id'] == 'solved'
    assert [t['total_points'] for t in result['full_game']['result']['totals']] == [1, 2, 3, 4, 5]
    assert 'evidence-look-note' not in game.state()['public_evidence_ids']
    with pytest.raises(PlayRulesError): action(game, 'PRIVATE_SPEAK', payload={'text': '三角木片'})


def test_full_phone_only_participants_hear_and_gain_memories_no_self_trigger():
    game = play_engine(full_package(), 'a'); advance(game)
    action(game, 'START_CALL', 'a', {'peer_character_id': 'b'})
    before = game.state()
    with pytest.raises(PlayRulesError): action(game, 'START_CALL', 'c', {'peer_character_id': 'd'})
    with pytest.raises(PlayRulesError): action(game, 'PRIVATE_SPEAK', 'c', {'text': '三角木片'})
    with pytest.raises(PlayRulesError): advance(game)
    assert game.state() == before
    action(game, 'PRIVATE_SPEAK', 'b', {'text': '我听说三角木片。'})
    assert {g['id'] for g in game.state()['memory_grants']} == {'memory-a'}
    assert not game.personal_discussion('c')
    assert '三角木片' in game.view()['full_game']['private_discussion'][0]['text']
    action(game, 'STOP_CALL')
    seq = game.state()['memory_observed_sequence'] + 1
    game.observe_event(seq, {'speaker': 'b', 'text': '三角木片', 'phase_id': 'investigate-one'})
    assert {g['id'] for g in game.state()['memory_grants']} == {'memory-a', 'memory-c', 'memory-d', 'memory-e'}


def test_full_ai_pair_is_not_visible_to_human_and_no_finale_memory_backfill():
    game = play_engine(full_package(), 'a'); advance(game)
    action(game, 'START_CALL', 'b', {'peer_character_id': 'c'})
    action(game, 'PRIVATE_SPEAK', 'b', {'text': '私密的三角木片'})
    view = game.view()
    assert view['full_game']['phone_busy'] and view['full_game']['call'] is None
    assert view['full_game']['private_discussion'] == [] and view['memories']['entries'] == []
    assert '私密的三角木片' not in canonical_json(view)
    action(game, 'STOP_CALL', 'c'); investigate(game); advance(game); advance(game); investigate(game); advance(game)
    old = deepcopy(game.state()['memory_grants'])
    seq = game.state()['memory_observed_sequence'] + 1
    game.observe_event(seq, {'speaker': 'b', 'text': '三角木片', 'phase_id': 'finale'})
    assert game.state()['memory_grants'] == old
    with pytest.raises(PlayRulesError): game.require_discussion()


@pytest.mark.parametrize('bad', ['phase-order', 'no-final', 'different-seats', 'duplicate-order', 'missing-order',
    'private-action', 'action-in-reading', 'budget-in-reading', 'foreign-memory', 'unknown-source'])
def test_full_invalid_version_binding_is_rejected(bad):
    value = full_package()
    if bad == 'phase-order': value['full_play']['phases'].reverse()
    elif bad == 'no-final': value['full_play']['phases'][-1]['kind'] = 'READING'
    elif bad == 'different-seats': value['full_play']['finale']['votes']['character_ids'][-1] = 'z'
    elif bad == 'duplicate-order': value['full_play']['action_order'][1]['order'] = 0
    elif bad == 'missing-order': value['full_play']['action_order'].pop()
    elif bad == 'private-action': value['mechanics']['actions'][0]['allowed_character_ids'] = ['a']
    elif bad == 'action-in-reading': value['mechanics']['actions'][0]['phase_ids'] = ['read-one']
    elif bad == 'budget-in-reading': value['mechanics']['phase_budgets'][0]['points'] = 1
    elif bad == 'foreign-memory': value['full_play']['finale']['facts'][0]['when'] = [[{'kind': 'ALL_MEMORIES', 'character_id': 'a', 'memory_ids': ['memory-b']}]]
    else: value['full_play']['sources'] = [{'source_id': 'missing', 'anchor': 'L1'}]
    assert not validate_package(value)['valid']


@pytest.mark.parametrize('keywords', [['。'], ['　'], ['三角木片', '三角 木片'], ['ABC', 'ＡＢＣ']])
def test_full_heard_keywords_cannot_be_empty_or_duplicate_after_normalization(keywords):
    value = full_package(); value['memories'][0]['triggers'][0]['keywords'] = keywords
    assert not validate_package(value)['valid']


@pytest.mark.parametrize('direction', ['original-to-editorial', 'editorial-to-original'])
def test_full_fact_origin_must_match_its_sources(direction):
    value = full_package()
    if direction == 'original-to-editorial':
        value['full_play']['finale']['facts'][0]['origin'] = 'EDITORIAL'
    else:
        value['sources'].append({'id': 'added', 'relative_path': 'added.md', 'sha256': '2' * 64, 'kind': 'supplement',
            'media_type': 'text/markdown', 'original_source_ids': [], 'provenance_note': '虚构编辑补充'})
        value['full_play']['finale']['facts'][0]['sources'] = [{'source_id': 'added', 'anchor': 'L1'}]
    assert any(i['code'] == 'RULE_ORIGIN_MISMATCH' for i in validate_package(value)['issues'])


def test_full_play_ballot_choices_remain_frozen_after_sharing_unlocks_an_action():
    package = full_package()
    package['evidence'].append({'id':'shared-gate', 'text':'虚构入口条件', 'visibility':'CHARACTER_PRIVATE',
        'character_id':'a', 'disclosure':'MAY_SHARE', 'release':{'phase_id':'read-one'}, 'sources':deepcopy(REFS)})
    extra = deepcopy(package['mechanics']['actions'][0])
    extra.update(id='extra-search',label='刚解锁调查', required_public_evidence_ids=['shared-gate'])
    package['mechanics']['actions'].append(extra)
    package['full_play']['action_order'].append({'action_id':'extra-search','order':3})
    game = play_engine(package, 'a'); advance(game); action(game,'OPEN_BALLOT')
    game.apply('SHARE_MATERIAL', {'collection':'evidence','id':'shared-gate'})
    game.observe_event(game.state()['memory_observed_sequence']+1)
    view=game.view()
    assert 'extra-search' in {o['id'] for o in view['mechanics']['available_actions']}
    assert {o['id'] for o in view['full_game']['ballot']['choices']} == {'find-key'}
    assert {o['id'] for o in game.decision_context('b','CAST_BALLOT')['options']} == {'find-key'}
    for actor in SEATS: action(game,'CAST_BALLOT',actor,{'kind':'CHOOSE','choice_id':'find-key'})
    action(game,'OPEN_BALLOT')
    assert 'extra-search' in {o['id'] for o in game.view()['full_game']['ballot']['choices']}


def test_full_play_private_book_can_be_retold_without_raw_share_or_goal_exposure():
    from src.fusion.package_dialogue_model import validate_speech
    package=full_package(); package['knowledge'][1]['retelling']='MAY_RETELL'
    package['knowledge'][1]['text']='我当时独自在楼梯旁边等候，却听见门后有两个人短暂交谈，然后其中一人迅速离开。'
    goal=deepcopy(package['knowledge'][1]); goal.update(id='private-goal-b',text='PRIVATE_GOAL_SENTINEL',retelling=None)
    package['knowledge'].append(goal)
    game=play_engine(package,'a')
    context=game.dialogue_context('b')
    context['discussion']=[{'id':'statement-1','sequence':1,'phase_id':'read-one',
        'speaker':'a','kind':'CLAIM','text':'当时听到了什么？'}]
    context['reply_to']='statement-1'
    assert 'initial-b' in {m['id'] for m in context['materials']}
    assert 'PRIVATE_GOAL_SENTINEL' not in canonical_json(context)
    assert not game.dialogue_context('c')['materials']
    with pytest.raises(PlayRulesError): game.apply_reply('b',[{'collection':'knowledge','id':'initial-b'}])
    with pytest.raises(ValueError,match='RAW_CARD_COPY'):
        validate_speech({'segments':[{'text':package['knowledge'][1]['text'],'mode':'REPORT',
            'basis':[{'collection':'knowledge','id':'initial-b'}]}]},context,'package-dialogue-model/1.3')
    game_b=play_engine(package,'b')
    assert game_b.view()['private_knowledge'][0]['retelling']=='MAY_RETELL'
    assert game_b.view()['private_knowledge'][0]['can_share'] is False
