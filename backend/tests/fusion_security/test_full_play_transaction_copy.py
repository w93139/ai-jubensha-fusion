"""Transaction optimization preserves rollback and historical state transitions."""
from copy import deepcopy

import pytest

from src.fusion.package_full_play_rules import PackageFullPlayRules
from src.fusion.package_investigation_rules import PackageInvestigationRules
from src.fusion.package_play_engine import play_engine
from src.fusion.package_play_rules import PlayRulesError
from tests.fusion_security.test_package_full_play import full_package, advance, action, investigate, SEATS, submission


def test_complete_sequence_matches_original_deepcopy(monkeypatch):
    original_copy = PackageFullPlayRules._transaction_copy
    def trajectory(legacy):
        monkeypatch.setattr(PackageFullPlayRules, '_transaction_copy', deepcopy if legacy else original_copy)
        package = full_package(); game = play_engine(package, 'a'); states = []
        def record(): states.append((game.state(), game.view(), [game.personal_discussion(s) for s in SEATS]))
        package['characters'][0]['name'] = 'changed after construction'
        advance(game); record()
        game.apply_phone('b', {'kind': 'INVITE', 'peer_character_id': 'a', 'speech': None}, 2)
        game.observe_event(2); record()
        action(game, 'PRIVATE_SPEAK', payload={'text': '我想问三角木片。'}); record()
        action(game, 'STOP_CALL', 'b'); record()
        investigate(game, 'find-key'); record(); investigate(game, 'open-box'); record()
        advance(game); advance(game); investigate(game); advance(game); record()
        for seat in SEATS: action(game, 'SEAL_FINALE', seat, submission(seat)); record()
        game.apply('SETTLE'); record()
        assert game._package['characters'][0]['name'] == 'a'
        return states
    assert trajectory(False) == trajectory(True)


def test_last_vote_failure_rolls_back_cost_grants_and_ballot(monkeypatch):
    game = play_engine(full_package(), 'a'); advance(game); action(game, 'OPEN_BALLOT')
    for seat in SEATS[:-1]: action(game, 'CAST_BALLOT', seat, {'kind': 'CHOOSE', 'choice_id': 'find-key'})
    before = deepcopy(game.__dict__)
    original = PackageInvestigationRules.apply
    def fail_after_grant(self, *args, **kwargs):
        original(self, *args, **kwargs)
        raise PlayRulesError('TEST_FAILURE_AFTER_GRANT')
    monkeypatch.setattr(PackageInvestigationRules, 'apply', fail_after_grant)
    with pytest.raises(PlayRulesError, match='TEST_FAILURE_AFTER_GRANT'):
        action(game, 'CAST_BALLOT', 'e', {'kind': 'CHOOSE', 'choice_id': 'find-key'})
    assert game.state()['completed_action_ids'] == []
    assert game._round.state() == before['_round'].state()
    for key in ('_public', '_remaining', '_remaining_actions', '_spent_by_phase', '_unlocked', '_memory_grants'):
        assert game.__dict__[key] == before[key]


def test_phone_invitation_failure_rolls_back_open_line(monkeypatch):
    game = play_engine(full_package(), 'a'); advance(game); before = game.state()
    original = PackageFullPlayRules._table_apply
    def fail_on_greeting(self, kind, *args):
        original(self, kind, *args)
        if kind == 'PRIVATE_SPEAK': raise PlayRulesError('TEST_GREETING_FAILURE')
    monkeypatch.setattr(PackageFullPlayRules, '_table_apply', fail_on_greeting)
    with pytest.raises(PlayRulesError, match='TEST_GREETING_FAILURE'):
        game.apply_phone('b', {'kind': 'INVITE', 'peer_character_id': 'c', 'speech': None}, 2)
    assert game.state() == before
    assert game.personal_discussion('b') == []


def test_invalid_finale_seal_preserves_every_seat():
    game = play_engine(full_package(), 'a'); advance(game); investigate(game); advance(game)
    advance(game); investigate(game); advance(game)
    action(game, 'SEAL_FINALE', 'a', submission('a')); before = game.state()
    bad = submission('b'); bad['answers'][0]['option_ids'] = ['not-an-option']
    with pytest.raises(ValueError): action(game, 'SEAL_FINALE', 'b', bad)
    assert game.state() == before
