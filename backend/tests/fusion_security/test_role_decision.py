"""Role behavior probe wiring, privacy projection, and one-attempt budget tests."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.fusion import role_decision_probe as probe
from src.fusion.role_decision import project_role_view, parse_decision
from src.fusion.package_validation import content_hash
from src.services.llm_service import LLMResponse
from tests.fusion_security.test_authoring_model import fixture_config


def test_other_role_and_future_secrets_are_excluded_before_prompt():
    suite=probe.prepare_suite(fixture_config())
    for packet in suite['packets']:
        messages=json.dumps(packet['messages'])
        assert 'OTHER_PRIVATE_792' not in messages and 'FUTURE_613' not in messages
        assert 'criterion' not in messages and 'expected_option' not in messages
    assert 'OWN_PRIVATE_481' in json.dumps(suite['packets'][-1]['messages'])
    assert suite['packets'][2]['view']['events'][-1]['kind']=='FACT'
    assert suite['packets'][1]['view']['events'][-1]['kind']=='CLAIM'


@pytest.mark.parametrize('mutation',['option','reference','duplicate','extra','blank','reasoning','hash'])
def test_decision_output_is_bound_and_cannot_invent_identifiers(mutation):
    view=probe.cases()[0]['view']; digest=content_hash(view)
    result={'option_id':'archive','evidence_ids':['e1'],'statement':'我想先核对档案室的记录。'}
    if mutation=='option':result['option_id']='secret-room'
    elif mutation=='reference':result['evidence_ids']=['other-secret']
    elif mutation=='duplicate':result['evidence_ids']=['e1','e1']
    elif mutation=='extra':result['approved']=True
    elif mutation=='blank':result['statement']='   '
    elif mutation=='reasoning':result['statement']='<think>PRIVATE</think>'
    else:digest='0'*64
    with pytest.raises(ValueError,match='ROLE_DECISION_INVALID'):
        parse_decision(json.dumps(result),view,digest)


def test_view_cannot_relabel_other_role_events_as_visible():
    view=probe.cases()[0]['view']
    from src.fusion.role_decision import RoleView
    with pytest.raises(ValueError):RoleView.model_validate(view|{'role_id':'outsider'})
    with pytest.raises(ValueError):RoleView.model_validate(view|{'revision':0})


def stub(monkeypatch,mode='ok'):
    async def reply(messages,**kwargs):
        view=json.loads(messages[1].content)
        if mode=='timeout':raise TimeoutError('PRIVATE_EXCEPTION')
        result={'option_id':'archive','evidence_ids':['e1'],'statement':'先检查登记册记录。'}
        return LLMResponse(json.dumps(result),model=fixture_config().model,
            usage=None if mode=='unknown' else {'prompt_tokens':100,'completion_tokens':20},
            finish_reason='length' if mode=='length' else 'stop')
    sdk=SimpleNamespace(chat_completion=AsyncMock(side_effect=reply),_client=None)
    factory=Mock(return_value=sdk);monkeypatch.setattr(probe,'OpenAILLMService',factory)
    return factory,sdk


def test_preview_and_changed_suite_never_construct_sdk(tmp_path,monkeypatch):
    factory,_=stub(monkeypatch)
    preview=asyncio.run(probe.run_probe(fixture_config(),tmp_path))
    assert preview['status']=='PREVIEW' and preview['model_requests']==0
    with pytest.raises(ValueError,match='SUITE_CHANGED'):
        asyncio.run(probe.run_probe(fixture_config(),tmp_path,execute=True,expected_hash='0'*64))
    factory.assert_not_called()


@pytest.mark.parametrize('mode',['ok','timeout','unknown','length'])
def test_probe_claim_budget_and_results_survive_without_retry(tmp_path,monkeypatch,mode):
    factory,sdk=stub(monkeypatch,mode)
    config=fixture_config();digest=content_hash(probe.prepare_suite(config))
    result=asyncio.run(probe.run_probe(config,tmp_path,execute=True,expected_hash=digest))
    unknown=mode in ('timeout','unknown')
    assert sdk.chat_completion.await_count==(1 if unknown else 6)
    assert result['status']==('STOPPED' if unknown else 'COLLECTED')
    assert result['semantic_status']=='UNREVIEWED'  # Even our mock votes wrongly on two cases.
    assert Decimal(result['accounted_cost_cny'])>0
    assert all(r['output'] is None for r in result['results']) if mode!='ok' else all(r['status']=='VALID_FORMAT' for r in result['results'])
    assert 'PRIVATE_EXCEPTION' not in json.dumps(result)
    with pytest.raises(FileExistsError):asyncio.run(probe.run_probe(config,tmp_path,execute=True,expected_hash=digest))
    assert factory.call_count==(1 if unknown else 6)


def test_all_budget_checks_precede_first_call(tmp_path,monkeypatch):
    factory,_=stub(monkeypatch)
    monkeypatch.setattr(probe,'MAX_TOTAL',Decimal('0.000001'))
    with pytest.raises(ValueError,match='BUDGET_EXCEEDED'):asyncio.run(probe.run_probe(fixture_config(),tmp_path,execute=True))
    factory.assert_not_called()


def test_request_has_both_context_and_schema_in_input_budget():
    suite=probe.prepare_suite(fixture_config())
    from src.fusion.package_validation import canonical_json
    for p in suite['packets']:
        assert p['input_bound']==sum(len(m['content'].encode()) for m in p['messages'])+4096+len(canonical_json(p['params']['response_format']).encode())
        assert p['params']['extra_body']=={'enable_thinking':False,'preserve_thinking':False}
        assert p['params']['response_format']['json_schema']['schema']['properties']['option_id']['enum']==['archive','workshop']
        assert 'pattern' not in canonical_json(p['params']['response_format'])
