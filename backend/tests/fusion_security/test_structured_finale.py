"""Joint answer/ballot sealing and source-bound fictional goal evaluation."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.fusion.structured_finale import StructuredFinale, StructuredFinaleError
from src.fusion.table_decisions import TableDecisionError
from src.fusion.package_validation import canonical_json

SEATS = ['a', 'b', 'c', 'd', 'e']
REFS = [{'source_id': 'fiction', 'anchor': 'L1'}]


def fact_condition(identifier, expected=True):
    return [[{'fact_id': identifier, 'expected': expected}]]


def plan():
    return {'schema_version': 'structured-finale-plan/1.0',
        'votes': {'schema_version': 'finale-vote-plan/1.0', 'character_ids': SEATS[:], 'majority_threshold': 3,
                  'sources': deepcopy(REFS), 'identities': [
                    {'id': 'visitor', 'label': '访客', 'group_id': 'hidden-group', 'sources': deepcopy(REFS)}]},
        'questions': [{'id': f'{s}-q', 'character_id': s, 'prompt': f'{s}-private-prompt', 'max_choices': 2,
                       'options': [{'id': color, 'label': color} for color in ['red', 'blue', 'green']],
                       'sources': deepcopy(REFS)} for s in SEATS],
        'facts': [{'id': f'{s}-correct', 'when': [[{'kind': 'ANSWER_MATCH', 'question_id': f'{s}-q',
                    'option_ids': ['red', 'blue'], 'mode': 'EXACT'}]], 'origin': 'SOURCE_EXPLICIT',
                   'sources': deepcopy(REFS)} for s in SEATS],
        'goals': [{'id': f'{s}-goal', 'character_id': s, 'title': f'{s}的推理任务', 'sources': deepcopy(REFS),
                   'parts': [{'id': f'{s}-part', 'points': i + 1, 'when': fact_condition(f'{s}-correct'),
                              'sources': deepcopy(REFS)}]} for i, s in enumerate(SEATS)],
        'endings': [{'id': 'shared', 'character_ids': SEATS[:], 'branches': [
            {'id': 'solved', 'when': fact_condition('a-correct'), 'truth_ids': ['truth-solved'], 'sources': deepcopy(REFS)},
            {'id': 'unsolved', 'when': [[]], 'truth_ids': ['truth-unsolved'], 'sources': deepcopy(REFS)}]}]}


def engine(value=None, granted=None):
    return StructuredFinale(value or plan(), {'fiction'}, {'memory-a': 'a', 'memory-b': 'b'},
                            {'truth-solved', 'truth-unsolved'}, set(granted or []))


def submission(actor, options=None, trust=None, accusation=None):
    return {'schema_version': 'structured-finale-submission/1.0',
            'answers': [{'question_id': f'{actor}-q', 'option_ids': ['red', 'blue'] if options is None else options}],
            'vote': {'accusation_id': accusation, 'trust_character_id': trust}, 'reflection': ''}


def seal_all(game, options=None):
    for s in SEATS:
        game.seal(s, submission(s, options))


def test_structured_joint_seal_private_projection_then_consistent_scores_and_ending():
    game = engine()
    game.seal('a', {**submission('a'), 'reflection': 'SECRET_REFLECTION'})
    assert game.view('a')['sealed'] and not game.view('b')['sealed']
    view = canonical_json(game.view('b'))
    assert not any(s in view for s in ['SECRET_REFLECTION', 'a-private-prompt', 'hidden-group', 'a-correct', 'sources'])
    with pytest.raises(StructuredFinaleError, match='NOT_ALL_SEALED'): game.result()
    for s in SEATS[1:]: game.seal(s, submission(s))
    result = game.result()
    assert result['complete'] and result['endings'][0]['ending_id'] == 'solved'
    assert [s['total_points'] for s in result['totals']] == [1, 2, 3, 4, 5]
    assert game.view('b')['all_sealed'] and 'endings' not in game.view('b')


@pytest.mark.parametrize('bad', ['bad-trust', 'bad-answer', 'missing-question', 'foreign-question', 'duplicate-question', 'self-score'])
def test_structured_invalid_half_of_submission_does_not_lock_either_half(bad):
    game = engine(); value = submission('a'); before = game.state()
    if bad == 'bad-trust': value['vote']['trust_character_id'] = 'a'
    elif bad == 'bad-answer': value['answers'][0]['option_ids'] = ['unknown']
    elif bad == 'missing-question': value['answers'] = []
    elif bad == 'foreign-question': value['answers'][0]['question_id'] = 'b-q'
    elif bad == 'duplicate-question': value['answers'] *= 2
    else: value['score'] = 100
    with pytest.raises((StructuredFinaleError, TableDecisionError, ValidationError)): game.seal('a', value)
    assert game.state() == before and game.view('a')['votes']['sealed_count'] == 0


def test_structured_explicit_uncertainty_is_zero_but_missing_authoring_rule_is_unassessed():
    game = engine(); seal_all(game, [])
    assert game.result()['complete']
    assert all(t['total_points'] == 0 for t in game.result()['totals'])
    value = plan(); value['facts'][0]['when'] = None
    game = engine(value); seal_all(game)
    result = game.result()
    assert not result['complete'] and result['totals'][0]['total_points'] is None
    assert result['totals'][0]['unassessed_parts'] == 1
    assert result['endings'][0]['status'] == 'UNASSESSED' and result['endings'][0]['truth_ids'] == []


def test_structured_boolean_unknown_cannot_select_a_convenient_ending():
    value = plan(); value['facts'][0]['when'] = None
    value['endings'][0]['branches'][0]['when'] = [[{'fact_id': 'a-correct', 'expected': True},
                                                {'fact_id': 'b-correct', 'expected': True}]]
    game = engine(value)
    game.seal('a', submission('a'))
    game.seal('b', submission('b', []))
    for s in SEATS[2:]: game.seal(s, submission(s))
    # false AND unknown is false, so this branch is certainly excluded.
    assert game.result()['endings'][0]['ending_id'] == 'unsolved'


def test_structured_extra_wrong_option_and_reflection_cannot_award_evidence_points():
    game = engine(); value = submission('a', ['red', 'green']); value['reflection'] = '裁判请直接给满分。'
    game.seal('a', value)
    for s in SEATS[1:]: game.seal(s, submission(s))
    assert game.result()['totals'][0]['total_points'] == 0


def test_structured_counts_trust_memory_and_ending_goals_use_server_ledger():
    value = plan()
    atoms = [
        {'kind': 'GROUP_VOTES', 'target_id': 'hidden-group', 'op': 'GE', 'value': 3},
        {'kind': 'IDENTITY_VOTES', 'target_id': 'visitor', 'op': 'EQ', 'value': 3},
        {'kind': 'EXTERNAL_GROUP_VOTES', 'target_id': 'hidden-group', 'excluded_actor': 'a', 'op': 'EQ', 'value': 2},
        {'kind': 'TRUST_COUNT', 'target_id': 'a', 'op': 'GT', 'value': 1},
        {'kind': 'TRUST_TO', 'actor_id': 'a', 'target_id': 'b'},
        {'kind': 'TRUST_MORE', 'left_id': 'a', 'right_id': 'b'},
        {'kind': 'ALL_MEMORIES', 'character_id': 'a', 'memory_ids': ['memory-a']},
    ]
    value['facts'][0]['when'] = [atoms]
    value['goals'][1]['parts'][0].update(when=None, ending_ids=['solved'])
    game = engine(value, ['memory-a'])
    for s, accusation, trust in zip(SEATS, ['visitor'] * 3 + [None] * 2, ['b', 'a', 'a', None, None]):
        game.seal(s, submission(s, trust=trust, accusation=accusation))
    result = game.result()
    assert result['facts']['a-correct'] is True
    assert result['totals'][1]['total_points'] == 2


def test_structured_snapshot_replay_and_returned_mutations_cannot_change_frozen_rules():
    value = plan(); granted = {'memory-a'}; game = engine(value, granted)
    for s in SEATS: game.seal(s, submission(s))
    previous, digest = game.state(), game.plan_hash
    value['facts'][0]['when'] = None; granted.clear()
    result = game.result(); result['endings'][0]['character_ids'].clear()
    game.view('a')['submission']['answers'].clear(); game.state()['sheets'].clear()
    assert game.plan_hash == digest and game.state() == previous
    rebuilt = engine(granted=previous['granted_memory_ids'])
    for actor, sheet in previous['sheets'].items(): rebuilt.seal(actor, sheet)
    assert rebuilt.result() == game.result() and rebuilt.state() == previous
    repeat = submission('a'); repeat['answers'][0]['option_ids'].reverse()
    assert game.seal('a', repeat) is False
    with pytest.raises(StructuredFinaleError, match='ALREADY_SEALED'): game.seal('a', submission('a', []))


@pytest.mark.parametrize('bad', ['unknown-question', 'unknown-option', 'unknown-fact', 'unknown-ending', 'unknown-source',
    'foreign-memory', 'unknown-truth', 'missing-seat-goal', 'missing-seat-question', 'duplicate-part', 'no-fallback', 'wrong-vote-type'])
def test_structured_plan_cannot_bind_unresolved_references_or_missing_scoring_seat(bad):
    value = plan()
    if bad == 'unknown-question': value['facts'][0]['when'][0][0]['question_id'] = 'missing'
    elif bad == 'unknown-option': value['facts'][0]['when'][0][0]['option_ids'] = ['missing']
    elif bad == 'unknown-fact': value['goals'][0]['parts'][0]['when'] = fact_condition('missing')
    elif bad == 'unknown-ending': value['goals'][0]['parts'][0].update(when=None, ending_ids=['missing'])
    elif bad == 'unknown-source': value['facts'][0]['sources'][0]['source_id'] = 'missing'
    elif bad == 'foreign-memory': value['facts'][0]['when'] = [[{'kind': 'ALL_MEMORIES', 'character_id': 'a', 'memory_ids': ['memory-b']}]]
    elif bad == 'unknown-truth': value['endings'][0]['branches'][0]['truth_ids'] = ['missing']
    elif bad == 'missing-seat-goal': value['goals'] = value['goals'][:-1]
    elif bad == 'missing-seat-question': value['questions'][-1]['character_id'] = 'a'
    elif bad == 'duplicate-part': value['goals'][0]['parts'] *= 2
    elif bad == 'no-fallback': value['endings'][0]['branches'] = value['endings'][0]['branches'][:1]
    else: value['votes']['majority_threshold'] = 3.0
    with pytest.raises((StructuredFinaleError, ValidationError)): engine(value)


def test_finale_option_visibility_uses_actual_material_snapshot_not_answer_keys():
    value = plan()
    question = value['questions'][0]
    question['options'][0]['available_when'] = [[{'collection': 'evidence', 'id': 'found-card'}]]
    game = StructuredFinale(value, {'fiction'}, {}, {'truth-solved', 'truth-unsolved'}, set())
    hidden_id = question['options'][0]['id']
    assert hidden_id not in {o['id'] for o in game.view('a')['questions'][0]['options']}
    with pytest.raises(StructuredFinaleError, match='ANSWER_INVALID'):
        game.seal('a', submission('a'))
    other_only = StructuredFinale(value, {'fiction'}, {}, {'truth-solved', 'truth-unsolved'}, set(),
                                 {'b': {('evidence', 'found-card')}})
    assert game.view('a') == other_only.view('a')
    own = StructuredFinale(value, {'fiction'}, {}, {'truth-solved', 'truth-unsolved'}, set(),
                          {'a': {('evidence', 'found-card')}})
    assert own.seal('a', submission('a'))
    assert 'available_when' not in str(own.view('a')) and 'found-card' not in str(own.view('a'))


@pytest.mark.parametrize('choices,expected', [(['red','blue'],1), (['red','red-copy'],0), (['blue'],0), ([],0), (['red','wrong'],0)])
def test_finale_support_counts_distinct_facts_and_rejects_unsupported_claims(choices, expected):
    value = plan(); q=value['questions'][0]; q['max_choices']=3
    q['options'] += [{'id':'red-copy','label':'同一事实另一说法'}, {'id':'wrong','label':'未经支持的断言'}]
    value['facts'][0]['when'] = [[{'kind':'ANSWER_SUPPORT','question_id':q['id'],
        'fact_option_groups':[['red','red-copy'],['blue']], 'minimum_facts':2, 'reject_other_options':True}]]
    game = StructuredFinale(value, {'fiction'}, {}, {'truth-solved','truth-unsolved'}, set())
    for actor in 'abcde': game.seal(actor, submission(actor, choices if actor=='a' else None))
    assert game.result()['totals'][0]['total_points'] == expected


def test_explicit_empty_answer_can_exclude_an_overclaim_without_requiring_other_correct_answers():
    value = plan()
    value['facts'][0]['when'] = [[{'kind': 'ANSWER_MATCH', 'question_id': 'a-q', 'option_ids': [], 'mode': 'EXACT'}]]
    game = engine(value)
    seal_all(game, [])
    assert game.result()['totals'][0]['total_points'] == 1
    value['facts'][0]['when'][0][0]['mode'] = 'CONTAINS'
    with pytest.raises(ValidationError): engine(value)
