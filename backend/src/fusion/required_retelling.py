"""Bounded public speaking assignments, never a semantic completion ledger."""
from typing import Literal

from pydantic import Field

from src.schemas.script_package import PackageModel, StableId


RETELLING_POLICY = 'required-public-retelling/1.0'
MAX_ASSIGNMENTS = 2


class RetellingTarget(PackageModel):
    collection: Literal['memory']
    id: StableId


class PriorRetellingAttempt(PackageModel):
    request_sequence: int = Field(ge=1)
    response_id: str | None = None
    text: str | None = Field(default=None, max_length=1000)
    result_status: Literal['OK', 'INVALID', 'UNKNOWN', 'EXPIRED', 'STALE']
    status: Literal['ATTEMPTED_UNVERIFIED', 'FAILED_UNVERIFIED']


class RetellingTask(PackageModel):
    kind: Literal['RETELL_REQUIRED_MEMORY']
    scope: Literal['CURRENT_PUBLIC_TURN']
    target: RetellingTarget
    prior_attempts: list[PriorRetellingAttempt] = Field(max_length=1)


def required_retelling_task(context, attempts):
    """Use the already authorized projection; legacy citations do not retire work."""
    if context['channel'] != 'PUBLIC':
        return None
    assigned = [a for a in attempts if a.get('attempt_kind') == 'ASSIGNED_PUBLIC_TASK'
                and a['character_id'] == context['character']['id'] and a['channel'] == 'PUBLIC']
    candidates = []
    for material in context['materials']:
        if material['collection'] == 'memory' and material.get('retelling') == 'MUST_RETELL':
            previous = [a for a in assigned if a['memory_id'] == material['id']]
            if len(previous) < MAX_ASSIGNMENTS:
                candidates.append((len(previous), material['id'], previous))
    if not candidates:
        return None
    _, identifier, previous = min(candidates, key=lambda row: row[:2])
    keys = ('request_sequence', 'response_id', 'text', 'result_status', 'status')
    return {'kind': 'RETELL_REQUIRED_MEMORY', 'scope': 'CURRENT_PUBLIC_TURN',
            'target': {'collection': 'memory', 'id': identifier},
            'prior_attempts': [{k: a[k] for k in keys} for a in previous[-1:]]}
