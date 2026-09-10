"""Sovereign seat ballots. A missing receipt is never an abstention.

Pure domain components; callers authenticate each seat, freeze the source-bound
plan, and commit a resolved investigation and its cost in one transaction.
"""
from collections import Counter
from copy import deepcopy
from typing import Literal

from pydantic import Field, model_validator

from src.schemas.package_primitives import PackageModel, StableId
from src.schemas.table_decisions import FinaleVotePlan, FinaleVote, Seat, InvestigationChoice, InvestigationVote
from src.fusion.package_validation import content_hash


class TableDecisionError(ValueError):
    pass


def _seats(values):
    if (type(values) is not list or not 2 <= len(values) <= 8
            or any(type(x) is not str for x in values) or len(set(values)) != len(values)):
        raise TableDecisionError('TABLE_SEATS_INVALID')
    for value in values:
        Seat.model_validate({'id': value})
    return tuple(values)


class CollectiveRound:
    """Frozen legal options and one explicit vote from every seat."""
    def __init__(self, seats: list[str], choices: list[dict], decider: str):
        self._seats = _seats(seats)
        self._actor(decider)
        if type(choices) is not list or not 1 <= len(choices) <= 5000:
            raise TableDecisionError('TABLE_CHOICES_INVALID')
        parsed = [InvestigationChoice.model_validate(c).model_dump() for c in choices]
        if (len({c['id'] for c in parsed}) != len(parsed)
                or len({c['order'] for c in parsed}) != len(parsed)):
            raise TableDecisionError('TABLE_CHOICES_INVALID')
        self._choices = sorted(parsed, key=lambda c: c['order'])
        self._decider = decider
        self._votes = {}
        self._tie_choice = None

    def _actor(self, actor):
        if type(actor) is not str or actor not in self._seats:
            raise TableDecisionError('TABLE_ACTOR_INVALID')

    def cast(self, actor: str, ballot: dict) -> bool:
        self._actor(actor)
        vote = InvestigationVote.model_validate(ballot).model_dump()
        if vote['kind'] == 'CHOOSE' and vote['choice_id'] not in {x['id'] for x in self._choices}:
            raise TableDecisionError('TABLE_CHOICE_UNAVAILABLE')
        if actor in self._votes:
            if self._votes[actor] != vote:
                raise TableDecisionError('TABLE_VOTE_ALREADY_SEALED')
            return False
        self._votes[actor] = deepcopy(vote)
        return True

    def outcome(self) -> dict:
        if len(self._votes) != len(self._seats):
            return {'status': 'WAITING', 'choice_id': None, 'tied_choice_ids': []}
        if all(v['kind'] == 'SKIP' for v in self._votes.values()):
            return {'status': 'SKIPPED', 'choice_id': None, 'tied_choice_ids': []}
        counts = Counter(v['choice_id'] for v in self._votes.values() if v['kind'] == 'CHOOSE')
        if not counts:
            return {'status': 'CHOSEN', 'choice_id': self._choices[0]['id'], 'tied_choice_ids': []}
        leaders = [c['id'] for c in self._choices if counts[c['id']] == max(counts.values())]
        if len(leaders) == 1 or self._tie_choice is not None:
            return {'status': 'CHOSEN', 'choice_id': self._tie_choice or leaders[0], 'tied_choice_ids': []}
        return {'status': 'TIE', 'choice_id': None, 'tied_choice_ids': leaders}

    def break_tie(self, actor: str, choice_id: str) -> bool:
        self._actor(actor)
        if actor != self._decider or type(choice_id) is not str:
            raise TableDecisionError('TABLE_TIE_DECIDER_INVALID')
        if self._tie_choice is not None:
            if self._tie_choice != choice_id:
                raise TableDecisionError('TABLE_TIE_ALREADY_SEALED')
            return False
        result = self.outcome()
        if result['status'] != 'TIE' or choice_id not in result['tied_choice_ids']:
            raise TableDecisionError('TABLE_TIE_CHOICE_INVALID')
        self._tie_choice = choice_id
        return True

    def view(self, actor: str) -> dict:
        self._actor(actor)
        return {'schema_version': 'collective-round-view/1.0', 'decider': self._decider,
                'sealed_count': len(self._votes), 'required_count': len(self._seats),
                'ballot': deepcopy(self._votes.get(actor)), **self.outcome()}

    def state(self) -> dict:
        return {'schema_version': 'collective-round-state/1.0', 'seats': list(self._seats),
                'choices': deepcopy(self._choices), 'decider': self._decider,
                'votes': deepcopy(self._votes), 'tie_choice': self._tie_choice}


class FinaleVotes:
    """Five-seat revised ballot policy; identity grouping remains server-only."""
    def __init__(self, plan: dict, source_ids: set[str], allowed_identities: dict[str, set[str]] | None = None):
        parsed = FinaleVotePlan.model_validate(plan)
        if any(r.source_id not in source_ids for r in [*parsed.sources, *(r for i in parsed.identities for r in i.sources)]):
            raise TableDecisionError('FINALE_VOTE_SOURCE_UNKNOWN')
        self._plan = parsed.model_dump()
        self._seats = _seats(self._plan['character_ids'])
        self._votes = {}
        ids = {i['id'] for i in self._plan['identities']}
        if allowed_identities is not None and (set(allowed_identities) != set(self._seats)
                or any(not values <= ids for values in allowed_identities.values())):
            raise TableDecisionError('FINALE_IDENTITY_SNAPSHOT_INVALID')
        self._allowed = {s: set(ids if allowed_identities is None else allowed_identities[s]) for s in self._seats}

    @property
    def plan_hash(self):
        return content_hash(self._plan)

    def _actor(self, actor):
        if type(actor) is not str or actor not in self._seats:
            raise TableDecisionError('TABLE_ACTOR_INVALID')

    def seal(self, actor: str, ballot: dict) -> bool:
        self._actor(actor)
        parsed = FinaleVote.model_validate(ballot).model_dump()
        if (parsed['accusation_id'] is not None
                and parsed['accusation_id'] not in self._allowed[actor]):
            raise TableDecisionError('FINALE_ACCUSATION_INVALID')
        if parsed['trust_character_id'] is not None and (parsed['trust_character_id'] not in self._seats
                                                       or parsed['trust_character_id'] == actor):
            raise TableDecisionError('FINALE_TRUST_INVALID')
        if actor in self._votes:
            if self._votes[actor] != parsed:
                raise TableDecisionError('FINALE_VOTE_ALREADY_SEALED')
            return False
        self._votes[actor] = deepcopy(parsed)
        return True

    def view(self, actor: str) -> dict:
        self._actor(actor)
        return {'schema_version': 'finale-vote-view/1.0', 'sealed_count': len(self._votes),
                'required_count': 5, 'ballot': deepcopy(self._votes.get(actor)),
                'accusation_options': [{k: i[k] for k in ('id', 'label')} for i in self._plan['identities'] if i['id'] in self._allowed[actor]],
                'trust_character_ids': [s for s in self._seats if s != actor]}

    def state(self) -> dict:
        return {'schema_version': 'finale-vote-state/1.0', 'plan_hash': self.plan_hash,
                'votes': deepcopy(self._votes)}

    def result(self) -> dict:
        if len(self._votes) != 5:
            raise TableDecisionError('FINALE_VOTES_NOT_ALL_SEALED')
        grouping = {x['id']: x['group_id'] for x in self._plan['identities']}
        raw = Counter(v['accusation_id'] for v in self._votes.values() if v['accusation_id'] is not None)
        groups = Counter(grouping[v['accusation_id']] for v in self._votes.values() if v['accusation_id'] is not None)
        trust = Counter(v['trust_character_id'] for v in self._votes.values() if v['trust_character_id'] is not None)
        # These aggregates feed host rules only; they are not a pre-seal player view.
        return {'schema_version': 'finale-vote-result/1.0', 'votes': deepcopy(self._votes),
                'identity_counts': {i['id']: raw[i['id']] for i in self._plan['identities']},
                'group_counts': {g: groups[g] for g in sorted(set(grouping.values()))},
                'trust_counts': {s: trust[s] for s in self._seats},
                'majority_group_ids': sorted(g for g, n in groups.items() if n >= 3)}

    def external_accusation_count(self, actor: str, group_id: str) -> int:
        """Zero external votes differs from not receiving a three-vote majority."""
        self._actor(actor)
        self.result()
        grouping = {x['id']: x['group_id'] for x in self._plan['identities']}
        if type(group_id) is not str or group_id not in set(grouping.values()):
            raise TableDecisionError('FINALE_GROUP_INVALID')
        return sum(voter != actor and vote['accusation_id'] is not None
                   and grouping[vote['accusation_id']] == group_id for voter, vote in self._votes.items())
