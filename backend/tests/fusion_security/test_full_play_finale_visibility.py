"""Fictional name hypotheses use acquired or actually heard information only."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.fusion.package_play_engine import play_engine
from src.fusion.package_validation import validate_package, canonical_json
from src.fusion.structured_finale import StructuredFinale, StructuredFinaleError
from src.fusion.table_decisions import TableDecisionError
from src.schemas.finale_rules import FinaleOption
from tests.fusion_security.test_package_full_play import full_package, advance, action, investigate
from tests.fusion_security.test_structured_finale import plan, submission


def gated_plan():
    value = plan()
    gate = {'available_when': [[{'collection': 'knowledge', 'id': 'initial-b'}]],
            'heard_terms': ['远航者', '遠航者']}
    value['votes']['identities'][0].update(deepcopy(gate))
    for q in value['questions']:
        q['options'][0].update(deepcopy(gate))
    return value


def test_finale_hypotheses_are_per_seat_material_or_heard_with_atomic_vote_rejection():
    value = gated_plan()
    game = StructuredFinale(value, {'fiction'}, {}, {'truth-solved', 'truth-unsolved'}, set(),
        {'b': {('knowledge', 'initial-b')}}, {'c': {'遠航者'}})
    for actor in ['b', 'c']:
        view = game.view(actor)
        assert 'red' in {o['id'] for o in view['questions'][0]['options']}
        assert view['votes']['accusation_options'] == [{'id': 'visitor', 'label': '访客'}]
    assert game.view('a')['votes']['accusation_options'] == []
    before = game.state()
    with pytest.raises(TableDecisionError, match='ACCUSATION_INVALID'):
        game.seal('a', submission('a', [], accusation='visitor'))
    with pytest.raises(StructuredFinaleError, match='ANSWER_INVALID'):
        game.seal('a', submission('a'))
    assert game.state() == before
    for actor in ['a', 'd', 'e']: game.seal(actor, submission(actor, []))
    for actor in ['b', 'c']: game.seal(actor, submission(actor, accusation='visitor'))
    assert game.result()['facts']['b-correct'] and game.result()['facts']['c-correct']
    visible = canonical_json(game.view('c'))
    assert all(s not in visible for s in ['heard_terms', 'available_when', 'initial-b', 'hidden-group'])


def test_heard_gate_collects_only_actual_audience_before_finale_and_replays():
    package = full_package(); package['full_play']['finale'] = gated_plan()
    def run():
        game = play_engine(package, 'a'); advance(game)
        # c is the only new listener; a does not hear the private name.
        action(game, 'START_CALL', 'b', {'peer_character_id': 'c'})
        action(game, 'PRIVATE_SPEAK', 'b', {'text': '我遇到了遠航者。'})
        action(game, 'STOP_CALL', 'b')
        investigate(game); advance(game); advance(game); investigate(game); advance(game)
        return game
    game = run()
    assert game.state() == run().state()
    assert game.decision_context('c', 'SEAL_FINALE')['accusation_options']
    assert not game.view()['full_game']['finale']['votes']['accusation_options']
    assert '遠航者' not in canonical_json(game.view())
    before = game.state()['full_play']['heard_terms']
    game.observe_event(game.state()['memory_observed_sequence'] + 1,
        {'speaker': 'b', 'text': '远航者', 'phase_id': 'finale'})
    assert game.state()['full_play']['heard_terms'] == before
    assert not game.view()['full_game']['finale']['votes']['accusation_options']


def test_public_name_is_a_hypothesis_not_a_material_grant():
    package = full_package(); package['full_play']['finale'] = gated_plan()
    game = play_engine(package, 'a'); advance(game)
    game.observe_event(game.state()['memory_observed_sequence'] + 1,
        {'speaker': 'd', 'text': '我只听到了远航者这个名字，不认识此人。', 'phase_id': 'investigate-one'})
    investigate(game); advance(game); advance(game); investigate(game); advance(game)
    assert game.view()['full_game']['finale']['votes']['accusation_options']
    assert 'PRIVATE_BOOK_b' not in canonical_json(game.view())
    assert not game.view()['memories']['entries']


@pytest.mark.parametrize('terms', [['， 。'], ['远航者', '远 航 者'], [' '], ['x' * 101], ['\n'], ['\t'], ['\r\n'], ['\u200b'], ['远\t航者']])
def test_heard_gate_rejects_empty_or_duplicate_normalized_terms(terms):
    with pytest.raises(ValidationError): FinaleOption(id='x', label='x', heard_terms=terms)


def test_vote_visibility_rejects_nonexistent_material_sources():
    package = full_package(); package['full_play']['finale'] = gated_plan()
    assert validate_package(package)['valid']
    package['full_play']['finale']['votes']['identities'][0]['available_when'][0][0]['id'] = 'absent'
    report = validate_package(package)
    assert not report['valid']
    assert any(i['code'] == 'FULL_PLAY_FINALE_OPTION_REFERENCE_INVALID' for i in report['issues'])
