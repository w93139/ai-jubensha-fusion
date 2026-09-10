"""Versioned automatic search and read-only, player-visible round records.

Automatic search follows the package's authored action order. It does not use
LLM votes, invent role reasoning, or change any material's ownership/disclosure.
Workspace indexes are rebuilt from events and excluded from legacy state hashes.
"""
from copy import deepcopy

from src.fusion.package_play_rules import PlayRulesError

ROUND_POLICY = 'package-auto-investigation/1.0'


def plan_round(engine, action_ids):
    candidate = engine._transaction_copy()
    view = candidate.view()
    phase = view['current_phase']['id']
    if (view['settled'] or view['full_game']['phase_kind'] != 'INVESTIGATION'
            or view['full_game']['phone_busy'] or phase in candidate._closed_investigations):
        raise PlayRulesError('GUIDED_INVESTIGATION_NOT_AVAILABLE')
    initial = {a['id']: a for a in view['mechanics']['available_actions']}
    if (not initial or len(action_ids) != len(set(action_ids)) or any(a not in initial for a in action_ids)
            or sum(initial[a]['cost'] for a in action_ids) > view['mechanics']['remaining_points']):
        raise PlayRulesError('GUIDED_ROUND_SELECTION_INVALID')
    roles = [c['id'] for c in candidate._package['characters'] if c['id'] != candidate._human]
    order = {item['action_id']: item['order'] for item in candidate._package['full_play']['action_order']}
    steps = []

    def take(identifier, actor, mode):
        action = candidate._actions[identifier]
        if not candidate._available(action, actor):
            raise PlayRulesError('GUIDED_ROUND_SELECTION_INVALID')
        before = visible_refs(candidate.view())
        candidate.guided_investigate(identifier)
        granted = visible_refs(candidate.view()) - before
        steps.append({'action_id': identifier, 'character_id': actor, 'mode': mode, 'cost': action['cost'],
                      'materials': [{'collection': ref[0], 'id': ref[1]} for ref in sorted(granted)]})

    for identifier in action_ids:
        take(identifier, candidate._human, 'PLAYER')
    turn = 0
    while True:
        choices = sorted(candidate._options(), key=lambda a: (order[a['id']], a['id']))
        found = None
        for offset in range(len(roles)):
            actor = roles[(turn + offset) % len(roles)]
            action = next((a for a in choices if candidate._available(a, actor)), None)
            if action:
                found = (action['id'], actor, offset)
                break
        if not found:
            break
        take(found[0], found[1], 'AUTO')
        turn = (turn + found[2] + 1) % len(roles)
    remaining = candidate.view()['mechanics']['remaining_points']
    return {'policy': ROUND_POLICY, 'phase_id': phase, 'steps': steps,
            'initial_remaining_points': view['mechanics']['remaining_points'], 'remaining_points': remaining,
            'end_reason': 'POINTS_EXHAUSTED' if remaining == 0 else 'NO_LEGAL_AFFORDABLE_ACTION'}


def visible_refs(projection):
    return {(collection, item['id']) for collection in ('knowledge', 'evidence')
            for key in ('public_' + collection, 'private_' + collection) for item in projection.get(key, [])} | {
                ('memory', item['id']) for item in projection.get('memories', {}).get('entries', [])}


def capture_workspace(state):
    """Observe only a successful transition, retaining first visibility to human."""
    if not hasattr(state, 'workspace_index') or not hasattr(state.engine, '_phase_kind'):
        return
    projection = state.engine.view()
    phase = projection['current_phase']['id']
    index = state.workspace_index
    index.setdefault('phases', {}).setdefault(phase, state.revision)
    materials = index.setdefault('materials', {})
    for ref in sorted(visible_refs(projection)):
        materials.setdefault(ref, {'phase_id': phase, 'sequence': state.revision})
    investigations = index.setdefault('investigations', {})
    for command in state.guided_actions:
        batch = command.get('batch')
        if batch:
            for step in batch['steps']:
                investigations.setdefault(step['action_id'], {**step, 'phase_id': batch['phase_id'],
                                                               'sequence': command['sequence']})
    for identifier in sorted(state.engine._completed_actions):
        investigations.setdefault(identifier, {'action_id': identifier, 'phase_id': phase,
            'sequence': state.revision, 'character_id': state.engine._human, 'mode': 'LEGACY'})


def project_workspace(state, projection):
    # Final presentation repairs may replace/remove visible materials. A stale
    # index never grants its original text; only surviving authorized refs leave.
    visible = visible_refs(projection)
    index = state.workspace_index
    phases = []
    round_number = 0
    for phase in state.engine._package['full_play']['phases']:
        identifier, kind = phase['phase_id'], phase['kind']
        if kind == 'READING':
            round_number += 1
        if identifier not in index.get('phases', {}):
            continue
        numeral = {1: '一', 2: '二'}.get(round_number, str(round_number))
        title = '结局 · 答卷' if kind == 'FINALE' else f"第{numeral}轮 · {'阅读材料' if kind == 'READING' else '调查'}"
        investigations = []
        for item in index.get('investigations', {}).values():
            if item['phase_id'] == identifier:
                action = state.engine._actions[item['action_id']]
                investigations.append({k: item[k] for k in ('action_id', 'character_id', 'mode', 'sequence')}
                                      | {'label': action['label'], 'cost': action['cost'],
                                         'materials': [deepcopy(ref) for ref in item.get('materials', [])
                                                       if (ref['collection'], ref['id']) in visible]})
        statements = sorted([*projection.get('discussion', {}).get('entries', []),
                             *projection.get('role_responses', {}).get('entries', []),
                             *projection.get('investigation_proposals', {}).get('entries', [])], key=lambda item: item['sequence'])
        phases.append({'phase_id': identifier, 'title': title, 'kind': kind,
            'materials': [{'collection': ref[0], 'id': ref[1], 'sequence': item['sequence']}
                          for ref, item in index.get('materials', {}).items()
                          if item['phase_id'] == identifier and ref in visible],
            'investigations': investigations,
            'statement_ids': [item['id'] for item in statements if item['phase_id'] == identifier],
            'private_message_ids': [item['id'] for item in projection['full_game']['private_discussion']
                                    if item['phase_id'] == identifier]})
    return {'schema_version': 'package-round-workspace/1.0', 'phases': deepcopy(phases)}
