"""Fictional seat ballots, with failure and secrecy checks."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.fusion.table_decisions import CollectiveRound, FinaleVotes, TableDecisionError
from src.fusion.package_validation import canonical_json


SEATS = ['a', 'b', 'c', 'd', 'e']
REFS = [{'source_id': 'fiction', 'anchor': 'L1'}]


def vote(kind='CHOOSE', choice='room-10'):
    return {'kind': kind, 'choice_id': choice if kind == 'CHOOSE' else None}


def round_():
    return CollectiveRound(SEATS, [{'id': 'room-10', 'order': 10}, {'id': 'room-2', 'order': 2}], 'c')


def plan():
    return {'schema_version': 'finale-vote-plan/1.0', 'character_ids': SEATS[:],
            'identities': [{'id': identity, 'label': identity, 'group_id': group, 'sources': deepcopy(REFS)}
                           for identity, group in [('a-name', 'private-group-a'), ('a-alias', 'private-group-a'),
                                                  ('b-name', 'private-group-b'), ('outsider', 'private-group-out')]],
            'majority_threshold': 3, 'sources': deepcopy(REFS)}


def finale():
    return FinaleVotes(plan(), {'fiction'})


def final_vote(accusation=None, trust=None):
    return {'accusation_id': accusation, 'trust_character_id': trust}


def test_collective_missing_receipt_never_implicitly_abstains_or_resolves():
    game = round_()
    for actor in SEATS[:-1]:
        game.cast(actor, vote())
    assert game.outcome()['status'] == 'WAITING'
    assert game.view('e')['ballot'] is None
    game.cast('e', vote('ABSTAIN'))
    assert game.outcome()['choice_id'] == 'room-10'


def test_collective_only_all_explicit_skip_finishes_without_investigation():
    game = round_()
    for actor in SEATS:
        game.cast(actor, vote('SKIP'))
    assert game.outcome() == {'status': 'SKIPPED', 'choice_id': None, 'tied_choice_ids': []}


@pytest.mark.parametrize('kinds', [['ABSTAIN'] * 5, ['SKIP'] * 4 + ['ABSTAIN']])
def test_collective_no_chosen_action_falls_back_to_authored_numeric_order(kinds):
    game = round_()
    for actor, kind in zip(SEATS, kinds):
        game.cast(actor, vote(kind))
    assert game.outcome()['choice_id'] == 'room-2'


def test_collective_tie_requires_rotating_seat_to_choose_a_tied_option():
    game = CollectiveRound(SEATS, [{'id': 'x', 'order': 1}, {'id': 'y', 'order': 2}, {'id': 'z', 'order': 3}], 'c')
    for actor, choice in zip(SEATS, ['x', 'x', 'y', 'y', 'z']):
        game.cast(actor, vote(choice=choice))
    assert game.outcome()['tied_choice_ids'] == ['x', 'y']
    before = game.state()
    with pytest.raises(TableDecisionError):
        game.break_tie('a', 'x')
    with pytest.raises(TableDecisionError):
        game.break_tie('c', 'z')
    assert game.state() == before
    assert game.break_tie('c', 'y') is True
    assert game.break_tie('c', 'y') is False
    with pytest.raises(TableDecisionError):
        game.break_tie('c', 'x')
    assert game.outcome()['choice_id'] == 'y'


def test_collective_committed_votes_are_private_immutable_and_replayable():
    game = round_(); ballot = vote()
    assert game.cast('a', ballot) is True
    assert game.cast('a', ballot) is False
    assert 'room-10' not in canonical_json(game.view('b'))
    with pytest.raises(TableDecisionError):
        game.cast('a', vote('SKIP'))
    before = game.state()
    ballot['choice_id'] = 'room-2'
    game.view('a')['ballot']['choice_id'] = 'room-2'
    game.state()['votes'].clear()
    assert game.state() == before
    rebuilt = CollectiveRound(before['seats'], before['choices'], before['decider'])
    for actor, value in before['votes'].items():
        rebuilt.cast(actor, value)
    assert rebuilt.state() == before


@pytest.mark.parametrize('ballot', [vote(choice='locked'), {'kind': 'SKIP', 'choice_id': 'room-10'},
    {'kind': 'CHOOSE', 'choice_id': None}, {'kind': 'ERROR', 'choice_id': None},
    {'kind': 'CHOOSE', 'choice_id': True}, {**vote(), 'cost': 0}])
def test_collective_invalid_or_failed_response_does_not_become_a_vote(ballot):
    game = round_(); before = game.state()
    with pytest.raises((TableDecisionError, ValidationError)):
        game.cast('a', ballot)
    assert game.state() == before


def test_finale_five_actual_ballots_before_any_counts_or_hidden_identity_mapping():
    game = finale()
    for actor in SEATS[:-1]:
        game.seal(actor, final_vote('a-name', 'e'))
    with pytest.raises(TableDecisionError, match='NOT_ALL_SEALED'):
        game.result()
    with pytest.raises(TableDecisionError, match='NOT_ALL_SEALED'):
        game.external_accusation_count('a', 'private-group-a')
    view = canonical_json(game.view('e'))
    assert not any(x in view for x in ['private-group', 'group_id', 'sources', 'identity_counts'])
    assert game.view('e')['ballot'] is None


def test_finale_alias_votes_group_only_accusations_and_abstention_keeps_three_threshold():
    game = finale()
    ballots = [('a-name', 'b'), ('a-alias', 'a'), (None, 'a'), (None, None), (None, None)]
    for actor, (accusation, trust) in zip(SEATS, ballots):
        game.seal(actor, final_vote(accusation, trust))
    result = game.result()
    assert result['group_counts']['private-group-a'] == 2
    assert result['majority_group_ids'] == []
    assert result['trust_counts'] == {'a': 2, 'b': 1, 'c': 0, 'd': 0, 'e': 0}


def test_finale_self_vote_and_nonplayer_legal_external_zero_differs_from_majority():
    game = finale()
    for actor, accusation in zip(SEATS, ['a-name', 'outsider', 'outsider', 'outsider', None]):
        game.seal(actor, final_vote(accusation))
    assert game.external_accusation_count('a', 'private-group-a') == 0
    assert game.external_accusation_count('b', 'private-group-a') == 1
    assert game.result()['majority_group_ids'] == ['private-group-out']


@pytest.mark.parametrize('ballot', [final_vote('unknown'), final_vote(trust='a'), final_vote(trust='outsider'),
    {'accusation_id': 'a-name'}, {**final_vote(), 'score': 100}, final_vote(True), final_vote(trust=['b', 'c'])])
def test_finale_bad_ballot_never_partly_seals(ballot):
    game = finale(); before = game.state()
    with pytest.raises((TableDecisionError, ValidationError)):
        game.seal('a', ballot)
    assert game.state() == before


def test_finale_freezes_plan_and_votes_and_does_not_allow_revised_submission():
    document = plan(); game = FinaleVotes(document, {'fiction'}); digest = game.plan_hash
    document['identities'][0]['group_id'] = 'changed'
    ballot = final_vote('a-name', 'b')
    assert game.seal('a', ballot)
    assert not game.seal('a', ballot)
    before = game.state()
    with pytest.raises(TableDecisionError):
        game.seal('a', final_vote())
    ballot['accusation_id'] = None
    game.view('a')['ballot']['accusation_id'] = None
    assert game.state() == before and game.plan_hash == digest


@pytest.mark.parametrize('operation', ['seal', 'view'])
def test_finale_unknown_actor_has_no_access(operation):
    game = finale()
    with pytest.raises(TableDecisionError):
        getattr(game, operation)('unknown', *([final_vote()] if operation == 'seal' else []))


@pytest.mark.parametrize('bad', ['duplicate-seat', 'duplicate-identity', 'unknown-source', 'wrong-majority'])
def test_finale_invalid_source_bound_plan_rejected(bad):
    value = plan()
    if bad == 'duplicate-seat': value['character_ids'][-1] = 'a'
    elif bad == 'duplicate-identity': value['identities'] *= 2
    elif bad == 'unknown-source': value['identities'][0]['sources'][0]['source_id'] = 'missing'
    else: value['majority_threshold'] = 2
    with pytest.raises((TableDecisionError, ValidationError)):
        FinaleVotes(value, {'fiction'})
