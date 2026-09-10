"""Four frozen synthetic Audit cases; no Compiler, import, or approval.

Preview freezes the suite fingerprint. Execute requires that fingerprint and
claims it once before any SDK call. Reports are collected, never auto-scored.
"""
from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
import json
import re

from src.fusion.authoring_model import AuthoringModel, AuthoringModelError
from src.fusion.authoring_sources import prepare_authoring_sources
from src.fusion.frozen_rule_plan_smoke import prepared_rule_plan
from src.fusion.investigation_authoring_smoke import (
    FIXTURE_TEXT, FIXTURE_TITLE, _json, _write, _require, new_directory,
    read_paid_authorization, InvestigationSmokeError, REPOSITORY,
)
from src.fusion.package_investigation_rules import PackageInvestigationRules
from src.fusion.package_play_rules import PlayRulesError
from src.fusion.package_validation import content_hash
from src.fusion.provider_smoke import load_selected_config, SmokeConfigurationError
from src.fusion.source_bundles import SourceBundleStore
from src.schemas.authoring import AuthoringRequestV12, AuthoringRequestV111


DEFAULT_OUTPUT_ROOT = REPOSITORY.parent / 'private-data/import-jobs/audit-semantic-smoke'
CONFIRM_PAID = 'FOUR_SYNTHETIC_AUDITS'
MAX_TOTAL_CNY = Decimal('0.05')
MAX_SINGLE_CNY = Decimal('0.015')
CASE_IDS = ('baseline', 'unspendable-budget', 'role-dead-end', 'allow-remaining-holdout')
FLAGS = dict(rule_plan_enabled=True, confirm_frozen_text=True, indexed_audit=True,
             bounded_audit=True, direct_audit_schema=True, strict_audit_schema=True,
             portable_audit_patterns=True, typed_audit_schema=True, runtime_audit_context=True)
CRITERIA = {
    'version': 'audit-semantic-criteria/1.0',
    'false_claims_to_check': ['EMPTY_TRUTH_IDS', 'MISSING_ACTION_SINGLE_USE', 'MULTIPLAYER_INVESTIGATION_REQUIRED'],
    'required_problems_to_check': {
        'unspendable-budget': ['UNSPENDABLE_POINT_PREVENTS_ADVANCE', 'BUDGET_DIFFERS_FROM_SOURCE'],
        'role-dead-end': ['SECOND_HUMAN_CANNOT_COMPLETE_ACTION_PATH', 'ACTION_SCOPE_DIFFERS_FROM_SOURCE'],
    },
    'holdout_to_check': 'ALLOW_REMAINING must not be treated as REQUIRE_EXHAUSTED; costs sum to 5 of 7.',
    'other_checks': ['SOURCE_SUPPORTS_SPECIFIC_CLAIM', 'NO_SPECULATION_AS_FACT', 'COVERAGE_AND_COMPLETENESS'],
    'scoring': 'INDEPENDENT_READ_ONLY_REVIEW_REQUIRED_NO_KEYWORD_OR_FINDING_COUNT_SCORE',
    'publication_ready': False,
}


def make_case(root: Path, case_id: str) -> tuple[dict, dict, dict]:
    _require(case_id in CASE_IDS, 'SEMANTIC_CASE_INVALID')
    root.mkdir(mode=0o700)
    inputs = root / 'synthetic-inputs'; inputs.mkdir(mode=0o700)
    text = FIXTURE_TEXT
    if case_id == 'allow-remaining-holdout':
        text = text.replace('共享行动预算恰好3点，advance_policy为REQUIRE_EXHAUSTED，剩余0点才能由当前真人推进',
                            '共享行动预算恰好7点，advance_policy为ALLOW_REMAINING，允许有剩余点数时由当前真人推进；调查可选，不要求搜齐或用尽点数，结算也不以完成调查为门槛')
        text = text.replace('消耗2点，仅在入场阶段可执行', '消耗3点，仅在入场阶段可执行')
        text = text.replace('消耗1点，仅在入场阶段可执行', '消耗2点，仅在入场阶段可执行')
    _write(inputs / 'fixture.md', text)
    store = SourceBundleStore(root / 'source-bundles')
    bundle = store.freeze(inputs, {'schema_version':'source-plan/1.0', 'script_key':'synthetic-audit-semantics-v1',
        'edition':'synthetic-v1', 'notes':['独立虚构审核验收资料，无商业正文。'],
        'sources':[{'relative_path':'fixture.md', 'kind':'original', 'material_type':'host'}]})
    base_request = AuthoringRequestV12(idempotency_key='synthetic-audit-input', bundle_hash=bundle['bundle_hash'],
        source_ids=[bundle['sources'][0]['id']], title=FIXTURE_TITLE, content_version='synthetic-v1',
        player_count=2, package_contract='script-package/1.2')
    context = prepare_authoring_sources(store, base_request)
    package = prepared_rule_plan(context)
    if case_id == 'unspendable-budget':
        package['mechanics']['phase_budgets'][0]['points'] = 4
    elif case_id == 'role-dead-end':
        package['mechanics']['actions'][1]['allowed_character_ids'] = ['a']
    elif case_id == 'allow-remaining-holdout':
        package['mechanics']['phase_budgets'][0].update(points=7, advance_policy='ALLOW_REMAINING')
        package['mechanics']['actions'][0]['cost'] = 2
        package['mechanics']['actions'][1]['cost'] = 3
    request = AuthoringRequestV111.model_validate(base_request.model_dump() | {
        'rule_plan':package, 'compiler_mode':'CONFIRM_FROZEN_TEXT', 'audit_mode':'RUNTIME_CONTEXT_SOURCE_INDEXES'})
    context = prepare_authoring_sources(store, request)
    verified = store.verify(bundle['bundle_hash'], document=package, persist=False)
    _require(verified['valid'] and not verified['issues'] and not verified['issues_truncated'], 'SEMANTIC_SOURCE_INVALID')
    traces = []
    for human in ('a', 'b'):
        engine = PackageInvestigationRules(package, human)
        events, error, before_advance = [], None, None
        for action, target in [('PERFORM_ACTION', {'action_id':'find'}), ('PERFORM_ACTION', {'action_id':'unlock'}),
                               ('ADVANCE_PHASE', None), ('SETTLE', None)]:
            try:
                if action == 'ADVANCE_PHASE':
                    before_advance = engine.view()['mechanics']['remaining_points']
                engine.apply(action, target); events.append(action)
            except PlayRulesError as exc:
                error = str(exc); break
        expected = ('PACKAGE_PLAY_PHASE_BUDGET_REMAINS' if case_id == 'unspendable-budget' else
                    'PACKAGE_PLAY_ACTION_NOT_AVAILABLE' if case_id == 'role-dead-end' and human == 'b' else None)
        _require(error == expected, 'SEMANTIC_ORACLE_MISMATCH')
        if case_id == 'allow-remaining-holdout':
            _require(before_advance == 2, 'SEMANTIC_ORACLE_MISMATCH')
        traces.append({'human':human, 'events':events, 'error':error,
                       'before_advance_remaining_points':before_advance,
                       'settled':engine.view()['settlement'] is not None})
    return context, package, {'case':case_id, 'engine_traces':traces, 'source_structure_valid':True,
                              'semantic_status':'UNREVIEWED'}


async def run_suite(config, *, output_root=DEFAULT_OUTPUT_ROOT, execute=False,
                    paid_authorized=False, expected_suite_hash=None):
    root = new_directory(output_root)
    receipt = {'schema_version':'audit-semantic-smoke/1.0', 'directory':str(root), 'status':'FAILED',
               'result_code':'SEMANTIC_LOCAL_FAILURE', 'mode':'execute' if execute else 'preview',
               'model_requests':0, 'maximum_model_requests':4 if execute else 0,
               'maximum_total_cny':str(MAX_TOTAL_CNY), 'maximum_single_cny':str(MAX_SINGLE_CNY),
               'charged_cost_cny':'0', 'reserved_cny':'0', 'results':[],
               'semantic_status':'UNREVIEWED', 'publication_ready':False, 'runtime_ready':False}
    try:
        if execute:
            _require(paid_authorized is True, 'SEMANTIC_PAID_CALLS_DISABLED')
            _require(isinstance(expected_suite_hash, str) and re.fullmatch('[0-9a-f]{64}', expected_suite_hash),
                     'SEMANTIC_EXPECTED_SUITE_REQUIRED')
        model = AuthoringModel(config, 'script-package/1.2', **FLAGS)
        packets, calls, oracles = [], [], []
        for case_id in CASE_IDS:
            context, package, oracle = make_case(root / case_id, case_id)
            p = model.prepare('AUDIT', context, package)
            _require(p.reservation.cost_cny <= MAX_SINGLE_CNY, 'SEMANTIC_CALL_BUDGET_EXCEEDED')
            packets.append({'case':case_id, 'context':context, 'candidate':package,
                'messages':[{'role':m.role, 'content':m.content} for m in p.messages],
                'request_contract':p.request_contract, 'contract_hash':p.contract_hash,
                'input_tokens':p.input_tokens, 'reservation':p.reservation.to_metadata()})
            calls.append(p); oracles.append(oracle)
        reserved = sum((p.reservation.cost_cny for p in calls), Decimal(0))
        _require(reserved <= MAX_TOTAL_CNY, 'SEMANTIC_SUITE_BUDGET_EXCEEDED')
        suite = {'schema_version':'audit-semantic-suite/1.0', 'data_class':'synthetic_noncommercial',
                 'model_snapshot':model.snapshot(), 'packets':packets, 'criteria':CRITERIA, 'oracles':oracles}
        digest = content_hash(suite)
        _json(root / 'suite.json', suite)
        receipt.update(suite_hash=digest, planned_reservation_cny=str(reserved))
        if not execute:
            receipt.update(status='PREVIEW', result_code='SEMANTIC_PREVIEW_READY')
        else:
            _require(digest == expected_suite_hash, 'SEMANTIC_SUITE_CHANGED')
            # Exclusive durable claim prevents duplicate dispatch of a suite,
            # including after a crash with no final receipt. No resume option.
            try:
                _json(root.parent / f'executed-{digest}.json', {'suite_hash':digest, 'directory':str(root)})
            except FileExistsError:
                raise InvestigationSmokeError('SEMANTIC_SUITE_ALREADY_CLAIMED') from None
            for index, (packet, p) in enumerate(zip(packets, calls)):
                _require(content_hash(json.loads((root / 'suite.json').read_text())) == digest,
                         'SEMANTIC_FROZEN_SUITE_CHANGED')
                verified = SourceBundleStore(root / packet['case'] / 'source-bundles').verify(
                    p.context['bundle_hash'], document=p.package, persist=False)
                _require(verified['valid'] and not verified['issues'] and not verified['issues_truncated'],
                         'SEMANTIC_SOURCE_INVALID')
                _json(root / f'dispatch-{index}.json', {'case':packet['case'], 'suite_hash':digest,
                    'reservation':p.reservation.to_metadata(), 'status':'IN_FLIGHT'})
                receipt['model_requests'] += 1
                receipt['reserved_cny'] = str(Decimal(receipt['reserved_cny']) + p.reservation.cost_cny)
                receipt['charged_cost_cny'] = str(Decimal(receipt['charged_cost_cny']) + p.reservation.cost_cny)
                result = {'case':packet['case'], 'status':'UNKNOWN', 'output':None,
                          'receipt':None, 'charged_cost_cny':str(p.reservation.cost_cny)}
                try:
                    output = await model.call('AUDIT', p.context, p.package, prepared=p)
                    result.update(status='COLLECTED', output=output['output'], receipt=output['receipt'])
                except AuthoringModelError as exc:
                    result.update(status='REJECTED', error_code=exc.code, receipt=exc.receipt)
                except (Exception, asyncio.CancelledError, KeyboardInterrupt):
                    # Never serialize arbitrary exception values; unknown results
                    # consume their reservation and stop all later cases.
                    result['error_code'] = 'SEMANTIC_CALL_RESULT_UNKNOWN'
                known = bool(result['receipt'] and result['receipt'].get('usage_known'))
                if known:
                    result['charged_cost_cny'] = result['receipt']['charged_cost_cny']
                receipt['charged_cost_cny'] = str(Decimal(receipt['charged_cost_cny']) - p.reservation.cost_cny + Decimal(result['charged_cost_cny']))
                receipt['results'].append(result)
                _json(root / f'result-{index}.json', result)
                if (not known or Decimal(result['charged_cost_cny']) > p.reservation.cost_cny
                        or result.get('error_code') == 'AUTHORING_USAGE_EXCEEDS_RESERVATION'):
                    receipt['result_code'] = 'SEMANTIC_UNKNOWN_OR_OVER_BUDGET_STOPPED'
                    break
            else:
                receipt.update(status='COLLECTED', result_code='SEMANTIC_REVIEW_REQUIRED')
    except (AuthoringModelError, InvestigationSmokeError, SmokeConfigurationError) as exc:
        receipt['result_code'] = exc.code
    except (Exception, asyncio.CancelledError, KeyboardInterrupt):
        # A dispatch may remain IN_FLIGHT if local persistence fails. The suite
        # claim remains; never retry, resume, or assume its cost was zero.
        receipt['result_code'] = 'SEMANTIC_LOCAL_FAILURE'
    _json(root / 'receipt.json', receipt)
    return (0 if receipt['status'] in ('PREVIEW', 'COLLECTED') else 3), receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description='四组固定虚构 Audit 验收；默认预览、无自动重试或自动评分。')
    parser.add_argument('--output-root', type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-paid', choices=(CONFIRM_PAID,))
    parser.add_argument('--expected-suite-hash')
    args = parser.parse_args(argv)
    try:
        _require(not args.execute or args.confirm_paid == CONFIRM_PAID, 'SEMANTIC_CONFIRMATION_REQUIRED')
        authorized = read_paid_authorization() if args.execute else False
        _require(not args.execute or authorized, 'SEMANTIC_PAID_CALLS_DISABLED')
        config = load_selected_config('aliyun_bailian')
        code, receipt = asyncio.run(run_suite(config, output_root=args.output_root, execute=args.execute,
            paid_authorized=authorized, expected_suite_hash=args.expected_suite_hash))
    except (AuthoringModelError, InvestigationSmokeError, SmokeConfigurationError) as exc:
        code, receipt = 2, {'status':'REFUSED', 'result_code':exc.code, 'model_requests':0}
    # Raw model prose stays in private artifacts; stdout exposes only metadata.
    print(json.dumps({k:v for k,v in receipt.items() if k != 'results'}, ensure_ascii=False))
    return code
