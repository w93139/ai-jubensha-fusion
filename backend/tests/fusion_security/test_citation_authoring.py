"""Explicit 1.12 wire/ledger integration; all SDK responses are synthetic."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from pathlib import Path

import pytest

from src.fusion import authoring_model
from src.fusion.authoring_model import AuthoringModel, AuthoringModelError
from src.fusion.authoring_jobs import _prepared, AuthoringJobError
from src.fusion.citation_audit import prepare_citation_contract
from src.fusion.frozen_rule_plan_smoke import run_smoke
from src.fusion.package_validation import canonical_json, content_hash
from src.schemas.authoring import AuthoringRequestV112, parse_authoring_request
from tests.fusion_security.test_audit_runtime import FLAGS, runtime_fixture
from tests.fusion_security.test_authoring_model import fixture_config, stub_sdk
from tests.fusion_security.test_frozen_rule_plan_smoke import sdk_fixture


OPTIONS = FLAGS | {'runtime_audit_context': True, 'citation_audit': True}


def fixture():
    context, package, _, old = runtime_fixture()
    context['audit_mode'] = 'CITATION_CATALOG'
    return context, package, AuthoringModel(fixture_config(), 'script-package/1.2', **OPTIONS), old


def metadata(p):
    return {key:getattr(p,key) for key in ('step','prompt_hash','contract_hash','input_tokens',
            'max_completion_tokens','request_contract')} | {'reservation':p.reservation.to_metadata()}


def test_dynamic_contract_and_catalog_are_frozen_and_fully_budgeted():
    context, package, model, old = fixture()
    before = deepcopy((context, package))
    p = model.prepare('AUDIT', context, package)
    contract = prepare_citation_contract(p.package)
    user = json.loads(p.messages[1].content)
    assert user['citation_catalog'] == contract['catalog']
    assert 'audit_source_catalog' not in user
    assert user['candidate'] == context['rule_plan']
    assert json.loads(p.messages[0].content.split('\nJSON Schema：\n')[1]) == contract['local_schema']
    assert p.request_contract['schema_hash'] == content_hash(contract['local_schema'])
    assert p.request_contract['citation_binding'] == {k:contract[k] for k in ('schema_version','package_hash','catalog_hash')}
    assert p.request_contract['response_format']['json_schema']['schema'] == contract['provider_schema_candidate']
    assert p.input_tokens == sum(len(m.content.encode('utf-8')) for m in p.messages) + 512 + len(canonical_json(p.request_contract['response_format']).encode('utf-8'))
    assert p.reservation.prompt_tokens == p.input_tokens
    assert _prepared(metadata(p), 'AUDIT', model.snapshot(), context) == metadata(p)
    assert _prepared(metadata(model.prepare('COMPILE', context)), 'COMPILE', model.snapshot(), context)
    assert model.snapshot()['schema_hashes']['AUDIT'] != p.request_contract['schema_hash']
    assert old.snapshot()['prompt_hashes']['COMPILE'] == model.snapshot()['prompt_hashes']['COMPILE']
    assert (context,package) == before


@pytest.mark.parametrize('change', ['catalog','package','schema','upper-bound','context-order','context-missing'])
def test_ledger_rederives_binding_even_if_envelope_hash_is_recomputed(change):
    context, package, model, _ = fixture()
    raw = deepcopy(metadata(model.prepare('AUDIT',context,package)))
    if change in ('catalog','package'):
        raw['request_contract']['citation_binding'][change+'_hash'] = '0'*64
    elif change == 'schema': raw['request_contract']['schema_hash'] = '0'*64
    elif change == 'upper-bound':
        raw['request_contract']['response_format']['json_schema']['schema']['$defs']['CitationFinding']['properties']['citation_indexes']['items']['maximum'] += 1
    elif change == 'context-order': context['rule_plan']['characters'].reverse()
    else: context = None
    raw['contract_hash'] = content_hash({'request':raw['request_contract'],'configuration':model.snapshot()})
    with pytest.raises(AuthoringJobError,match='PREPARATION_INVALID'):
        _prepared(raw,'AUDIT',model.snapshot(),context)


@pytest.mark.parametrize('change', ['catalog','schema','message','context'])
def test_tampering_before_dispatch_cannot_create_sdk_or_spend(change, monkeypatch):
    context, package, model, _ = fixture()
    p = model.prepare('AUDIT', context, package)
    factory, _ = stub_sdk(monkeypatch)
    if change in ('catalog','schema'):
        contract = deepcopy(p.request_contract)
        if change == 'catalog': contract['citation_binding']['catalog_hash'] = '0'*64
        else: contract['schema_hash'] = '0'*64
        p = replace(p, request_contract=contract)
    elif change == 'message':
        p = replace(p,messages=(p.messages[0],replace(p.messages[1],content='{}')))
    else: context['notes'].append('changed after preparation')
    with pytest.raises(AuthoringModelError, match='PREPARATION_CHANGED'):
        asyncio.run(model.call('AUDIT',context,package,prepared=p))
    factory.assert_not_called()


def test_budget_refuses_before_sdk_when_full_catalog_and_schema_exceed_limit(monkeypatch):
    context, package, model, _ = fixture()
    p = model.prepare('AUDIT',context,package)
    factory,_ = stub_sdk(monkeypatch)
    monkeypatch.setattr(authoring_model,'MAX_INPUT_TOKENS',p.input_tokens-1)
    with pytest.raises(AuthoringModelError, match='INPUT_TOO_LARGE'):
        asyncio.run(model.call('AUDIT',context,package))
    factory.assert_not_called()


def test_explicit_request_schema_no_override_or_cross_version():
    context, package, model, old = fixture()
    body = {k:v for k,v in context.items() if k in AuthoringRequestV112.model_fields} | {'idempotency_key':'citation-test'}
    assert type(parse_authoring_request(body)) is AuthoringRequestV112
    schema_path = Path(__file__).resolve().parents[3] / 'docs/contracts/authoring-request.v1.12.schema.json'
    assert json.loads(schema_path.read_text()) == AuthoringRequestV112.model_json_schema()
    for key in ('citation_catalog','citation_binding','response_format'):
        with pytest.raises(ValueError): parse_authoring_request(body | {key:{}})
        with pytest.raises(AuthoringModelError): model.prepare('AUDIT',context | {key:{}},package)
    with pytest.raises(AuthoringModelError,match='CONTRACT_MISMATCH'): old.prepare('AUDIT',context,package)
    context['audit_mode'] = 'RUNTIME_CONTEXT_SOURCE_INDEXES'
    old_before = old.prepare('AUDIT',context,package)
    with pytest.raises(AuthoringModelError,match='CONTRACT_MISMATCH'): model.prepare('AUDIT',context,package)
    assert old.prepare('AUDIT',context,package) == old_before
    assert 'citation_catalog' not in json.loads(old_before.messages[1].content)
    assert 'citation_binding' not in old_before.request_contract
    with pytest.raises(AuthoringModelError): AuthoringModel(fixture_config(),citation_audit=True)


@pytest.mark.parametrize('kind', ['blocker','outside','mixed'])
def test_durable_reports_keep_blockers_or_reject_bad_citations_without_retry(tmp_path,monkeypatch,kind):
    mutation = (lambda r:r['findings'][0].update(citation_indexes=[99])) if kind == 'outside' else (lambda r:r['findings'][0].update(citation_indexes=[0,1])) if kind == 'mixed' else None
    _,sdk = sdk_fixture(monkeypatch,audit_blocker=kind == 'blocker',audit_mutation=mutation)
    code, receipt = asyncio.run(run_smoke(fixture_config(),output_root=tmp_path/'private',execute=True,
        paid_authorized=True,**{k:v for k,v in OPTIONS.items() if k != 'rule_plan_enabled'}))
    assert code == 3 and sdk.chat_completion.await_count == 2
    assert not receipt['publication_ready'] and not receipt['runtime_ready']
    assert receipt['job_reload_verified']
    root = Path(receipt['artifact_directory'])
    with sqlite3.connect(f'file:{root / "synthetic-authoring.sqlite3"}?mode=ro',uri=True) as db:
        stored = db.execute('SELECT package_json FROM script_package_versions WHERE id=?',(receipt['candidate_version_id'],)).fetchone()
    assert json.loads(stored[0]) == json.loads((root/'rule-plan-input.json').read_text())
    if kind == 'blocker':
        assert receipt['job_state'] == 'COMPLETED' and receipt['quality']['audit_blockers'] == 1
        assert receipt['result_code'] == 'SMOKE_AUDIT_BLOCKERS_FOUND'
    else:
        assert receipt['job_state'] == 'BLOCKED' and receipt['result_code'] == 'AUDIT_CITATION_OUTPUT_INVALID'
        assert receipt['attempts'][1]['output_hash'] is None
        assert receipt['attempts'][1]['receipt']['output_diagnostics'] == [{'code':'AUDIT_CITATION_REFERENCE_INVALID',
            'entity_path':'/findings/0/citation_indexes/0' if kind == 'outside' else '/findings/0/citation_indexes'}]
