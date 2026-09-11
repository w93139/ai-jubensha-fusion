"""Atomic final sheets and deterministic, three-valued source-bound scoring."""
from copy import deepcopy

from src.fusion.package_validation import content_hash
from src.fusion.table_decisions import FinaleVotes
from src.schemas.finale_rules import StructuredFinalePlan, StructuredSubmission
from src.schemas.package_primitives import normalize_heard_text


class StructuredFinaleError(ValueError):
    pass


def _all(values):
    values = list(values)
    return False if False in values else None if None in values else True


def _any(values):
    values = list(values)
    return True if True in values else None if None in values else False


class StructuredFinale:
    def __init__(self, plan: dict, source_ids: set[str], memories: dict[str, str],
                 truth_ids: set[str], granted_memory_ids: set[str], available_materials: dict[str, set[tuple[str, str]]] | None = None,
                 heard_terms: dict[str, set[str]] | None = None):
        self._plan = StructuredFinalePlan.model_validate(plan).model_dump()
        self._sources = set(source_ids)
        self._seats = self._plan['votes']['character_ids']
        self._sheets = {}
        available_materials = available_materials or {}
        heard_terms = heard_terms or {}
        if (set(available_materials) | set(heard_terms)) - set(self._seats):
            raise StructuredFinaleError('FINALE_MATERIAL_SNAPSHOT_INVALID')
        held = {s: set(available_materials.get(s, set())) | {('memory', m) for m in granted_memory_ids if memories.get(m) == s}
                for s in self._seats}
        heard = {s: {normalize_heard_text(t) for t in heard_terms.get(s, set())} for s in self._seats}

        def allowed(option, actor):
            return (not option['available_when'] and not option['heard_terms']
                    or any(all((r['collection'], r['id']) in held[actor] for r in clause) for clause in option['available_when'])
                    or any(normalize_heard_text(term) in heard[actor] for term in option['heard_terms']))

        self._allowed_identities = {s: {i['id'] for i in self._plan['votes']['identities'] if allowed(i, s)} for s in self._seats}
        self._votes = FinaleVotes(self._plan['votes'], self._sources, self._allowed_identities)
        self._allowed = {}
        for q in self._plan['questions']:
            self._allowed[q['id']] = {o['id'] for o in q['options'] if allowed(o, q['character_id'])}
        if not set(granted_memory_ids) <= set(memories):
            raise StructuredFinaleError('FINALE_MEMORY_SNAPSHOT_INVALID')
        self._granted = frozenset(granted_memory_ids)
        pending = [self._plan]
        while pending:
            item = pending.pop()
            if isinstance(item, dict):
                if 'sources' in item and any(r['source_id'] not in self._sources for r in item['sources']):
                    raise StructuredFinaleError('FINALE_SOURCE_UNKNOWN')
                if item.get('kind') == 'ALL_MEMORIES' and any(memories.get(m) != item['character_id'] for m in item['memory_ids']):
                    raise StructuredFinaleError('FINALE_MEMORY_REFERENCE_INVALID')
                if 'truth_ids' in item and not set(item['truth_ids']) <= truth_ids:
                    raise StructuredFinaleError('FINALE_TRUTH_REFERENCE_INVALID')
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        self._plan_hash = content_hash(self._plan)

    @property
    def plan_hash(self):
        return self._plan_hash

    def _questions(self, actor):
        if type(actor) is not str or actor not in self._seats:
            raise StructuredFinaleError('FINALE_ACTOR_INVALID')
        return [q for q in self._plan['questions'] if q['character_id'] == actor]

    def seal(self, actor: str, submission: dict) -> bool:
        questions = self._questions(actor)
        value = StructuredSubmission.model_validate(submission).model_dump()
        answers = {a['question_id']: a for a in value['answers']}
        if len(answers) != len(value['answers']) or set(answers) != {q['id'] for q in questions}:
            raise StructuredFinaleError('FINALE_ANSWERS_INCOMPLETE')
        for q in questions:
            selected = answers[q['id']]['option_ids']
            if (len(set(selected)) != len(selected) or len(selected) > q['max_choices']
                    or not set(selected) <= self._allowed[q['id']]):
                raise StructuredFinaleError('FINALE_ANSWER_INVALID')
            answers[q['id']]['option_ids'] = sorted(selected)
        value['answers'] = [answers[q['id']] for q in questions]
        if actor in self._sheets:
            if self._sheets[actor] != value:
                raise StructuredFinaleError('FINALE_ALREADY_SEALED')
            return False
        # Build a candidate vote ledger first; any bad trust/identity leaves both
        # the prior votes and sheets untouched, even for in-memory callers.
        votes = FinaleVotes(self._plan['votes'], self._sources, self._allowed_identities)
        for voter, ballot in self._votes.state()['votes'].items():
            votes.seal(voter, ballot)
        votes.seal(actor, value['vote'])
        self._votes = votes
        self._sheets[actor] = deepcopy(value)
        return True

    def state(self) -> dict:
        return {'schema_version': 'structured-finale-state/1.0', 'plan_hash': self.plan_hash,
                'granted_memory_ids': sorted(self._granted), 'sheets': deepcopy(self._sheets),
                'allowed_option_ids': {q: sorted(ids) for q, ids in self._allowed.items()},
                'allowed_identity_ids': {s: sorted(ids) for s, ids in self._allowed_identities.items()}}

    def view(self, actor: str) -> dict:
        questions = self._questions(actor)
        return {'schema_version': 'structured-finale-view/1.0',
                'questions': [{'id': q['id'], 'prompt': q['prompt'],
                    'options': [{k: o[k] for k in ('id', 'label')} for o in q['options'] if o['id'] in self._allowed[q['id']]],
                    'max_choices': min(q['max_choices'], len(self._allowed[q['id']]))} for q in questions],
                'votes': self._votes.view(actor), 'sealed': actor in self._sheets,
                'all_sealed': len(self._sheets) == 5, 'submission': deepcopy(self._sheets.get(actor))}

    @staticmethod
    def _condition(condition, facts):
        if condition is None:
            return None
        return _any(_all(None if facts[t['fact_id']] is None else facts[t['fact_id']] == t['expected']
                         for t in clause) for clause in condition)

    def vote_disclosure(self, finale_speeches=()) -> list[dict]:
        if len(self._sheets) != 5:
            raise StructuredFinaleError('FINALE_NOT_ALL_SEALED')
        labels = {i['id']: i['label'] for i in self._plan['votes']['identities']}
        statements = {entry['character_id']: entry['text'] for entry in finale_speeches}
        return [{'character_id': actor, 'voted_for': self._sheets[actor]['vote']['accusation_id'],
                 'voted_for_label': labels.get(self._sheets[actor]['vote']['accusation_id'], '弃权'),
                 'motivation': statements.get(actor, '')} for actor in self._seats]

    def result(self, finale_speeches=()) -> dict:
        """Host-only evaluation. Public reveal is a separate, authorized action."""
        if len(self._sheets) != 5:
            raise StructuredFinaleError('FINALE_NOT_ALL_SEALED')
        votes = self._votes.result()
        answers = {a['question_id']: set(a['option_ids']) for sheet in self._sheets.values() for a in sheet['answers']}

        def atom(a):
            kind = a['kind']
            if kind == 'ANSWER_MATCH':
                selected, key = answers[a['question_id']], set(a['option_ids'])
                return selected == key if a['mode'] == 'EXACT' else key <= selected
            if kind == 'ANSWER_SUPPORT':
                selected = answers[a['question_id']]
                groups = [set(group) for group in a['fact_option_groups']]
                if a['reject_other_options'] and not selected <= set().union(*groups):
                    return False
                return sum(bool(selected & group) for group in groups) >= a['minimum_facts']
            if kind == 'ALL_MEMORIES':
                return set(a['memory_ids']) <= self._granted
            if kind == 'TRUST_TO':
                return votes['votes'][a['actor_id']]['trust_character_id'] == a['target_id']
            if kind in ('TRUST_MORE', 'IDENTITY_MORE'):
                counts = votes['trust_counts' if kind == 'TRUST_MORE' else 'identity_counts']
                return counts[a['left_id']] > counts[a['right_id']]
            if kind == 'EXTERNAL_GROUP_VOTES':
                count = self._votes.external_accusation_count(a['excluded_actor'], a['target_id'])
            else:
                counts = votes[{'GROUP_VOTES': 'group_counts', 'IDENTITY_VOTES': 'identity_counts',
                                'TRUST_COUNT': 'trust_counts'}[kind]]
                count = counts[a['target_id']]
            return {'EQ': count == a['value'], 'GE': count >= a['value'], 'GT': count > a['value']}[a['op']]

        facts = {f['id']: None if f['when'] is None else _any(_all(atom(a) for a in clause) for clause in f['when'])
                 for f in self._plan['facts']}
        endings = []
        selected_ends, possible_ends = set(), set()
        for group in self._plan['endings']:
            possible = []
            for branch in group['branches']:
                outcome = self._condition(branch['when'], facts)
                if outcome is not False:
                    possible.append(branch)
                if outcome is True:
                    break
            certain = len(possible) == 1
            if certain:
                selected_ends.add(possible[0]['id'])
            possible_ends.update(e['id'] for e in possible)
            endings.append({'group_id': group['id'], 'character_ids': deepcopy(group['character_ids']),
                            'status': 'DETERMINED' if certain else 'UNASSESSED',
                            'ending_id': possible[0]['id'] if certain else None,
                            'truth_ids': deepcopy(possible[0]['truth_ids']) if certain else []})
        rows = []
        for goal in self._plan['goals']:
            parts = []
            for p in goal['parts']:
                if p['ending_ids'] is None:
                    verdict = self._condition(p['when'], facts)
                else:
                    candidates = set(p['ending_ids'])
                    verdict = True if candidates & selected_ends else None if candidates & possible_ends else False
                parts.append({'id': p['id'], 'max_points': p['points'],
                              'explanation': p['explanation'],
                              'points': None if verdict is None else p['points'] if verdict else 0,
                              'status': 'UNASSESSED' if verdict is None else 'ASSESSED'})
            complete = all(p['points'] is not None for p in parts)
            rows.append({'id': goal['id'], 'character_id': goal['character_id'], 'title': goal['title'],
                         'parts': parts, 'max_points': sum(p['max_points'] for p in parts),
                         'points': sum(p['points'] for p in parts) if complete else None})
        totals = []
        for actor in self._seats:
            parts = [p for g in rows if g['character_id'] == actor for p in g['parts']]
            complete = all(p['points'] is not None for p in parts)
            totals.append({'character_id': actor, 'max_points': sum(p['max_points'] for p in parts),
                           'known_points': sum(p['points'] for p in parts if p['points'] is not None),
                           'total_points': sum(p['points'] for p in parts) if complete else None,
                           'unassessed_parts': sum(p['points'] is None for p in parts)})
        complete = all(t['unassessed_parts'] == 0 for t in totals) and all(e['status'] == 'DETERMINED' for e in endings)
        return {'schema_version': 'structured-finale-result/1.0', 'plan_hash': self.plan_hash,
                'complete': complete, 'facts': facts, 'votes': votes, 'goals': rows, 'totals': totals, 'endings': endings,
                'vote_disclosure': self.vote_disclosure(finale_speeches)}
