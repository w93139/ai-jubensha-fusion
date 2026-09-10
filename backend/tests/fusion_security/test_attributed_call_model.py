"""Speaker labels describe actual identities, without semantic acceptance claims."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from unittest.mock import AsyncMock

import pytest

from src.fusion.agents import PlayerModelSettings
from src.fusion.package_call_model import (
    ATTRIBUTED_CALL_CONTRACT, AttributedPackageCallModel, CompactPassagePackageCallModel,
    StrategyCallContext, attributed_call_wire_context, call_window, response_format, validate_call,
)
from src.fusion.package_role_model import PackageRoleModelError
from src.fusion.package_validation import canonical_json, content_hash
from src.services.llm_service import LLMResponse


def settings(**changes):
    return replace(PlayerModelSettings(provider='volcengine_ark', model='doubao-seed-character-260628',
        paid_calls_enabled=True, thinking_mode='disabled', retries=0, max_output_tokens=1000,
        max_input_bytes=65536, temperature=0, timeout_seconds=60), **changes)


def context():
    return dict(schema_version='package-call-context/1.1', play_id='play-'+'a'*32, package_hash='b'*64,
        revision=4, character={'id':'b','name':'乙'}, current_phase={'id':'one','title':'调查'},
        stage='CONNECTED', peers=[{'id':'c','name':'丙'}], planning_materials=[], strategy_materials=[],
        materials=[{'collection':'knowledge','id':'scene','kind':'FACT','text':'庭院里有一把空椅子。','retelling':None}],
        discussion=[{'id':'claim-1','sequence':1,'speaker':'b','kind':'CLAIM','text':'我在庭院等候。'},
                    {'id':'claim-2','sequence':2,'speaker':'d','kind':'CLAIM','text':'我在河边见过船夫。'},
                    {'id':'claim-3','sequence':3,'speaker':'c','kind':'CLAIM','text':'你能说明刚才的经过吗？'}],
        reply_to='claim-3', history_window={'policy':'full-play-context-window/1.0','omitted_count':0})


def speech(text='我先前提到过在院子等候，另一位说过见到船夫。想听听你后来看到的情况。'):
    return {'kind':'SPEAK','peer_character_id':None,'speech':{'segments':[{
        'basis':[{'collection':'discussion','id':f'claim-{i}','passage_ids':['p0001']} for i in (1,2,3)],
        'mode':'REPORT','text':text}]}}


def response(value):
    return LLMResponse(content=canonical_json(value), usage={'prompt_tokens':100,'completion_tokens':10},
                       model=settings().model, finish_reason='stop')


def test_legacy_17_metadata_schema_and_prepared_match_pre_change_snapshots():
    old = CompactPassagePackageCallModel(object(), settings()); c = context()
    assert content_hash(old.metadata()) == 'fce414aedf1e14878786e2b6593f26f477087377dadb3429bf1fd2a1ac05f082'
    assert content_hash(old.prepare(c)) == '8b2c1eb7724e50693ab2d4b83ca1370af5c22508a2fc7b02ed99969da58220e3'
    assert content_hash(response_format('package-call-model/1.7', c)) == 'dc951282c35c6411a40d4074b78ffde733322599835317e5bbf8072638e96c2a'
    new = AttributedPackageCallModel(object(), settings())
    assert new.metadata()['schema_version'] == ATTRIBUTED_CALL_CONTRACT
    assert new.metadata()['speaker_attribution_policy'] == 'phone-current-peer-third-party/1.0'
    assert new.metadata()['prompt_hash'] != old.metadata()['prompt_hash']
    assert new.metadata()['schema_hash'] == old.metadata()['schema_hash']
    assert new.metadata()['speech_policy_hash'] == old.metadata()['speech_policy_hash']
    assert 'speaker_attribution_policy' not in old.metadata()
    assert response_format(ATTRIBUTED_CALL_CONTRACT, c) == response_format('package-call-model/1.7', c)


def test_wire_labels_preserve_speaker_ids_original_claims_and_authorized_materials():
    c = context(); before = deepcopy(c)
    prepared = AttributedPackageCallModel(object(), settings()).prepare(c)
    wire = json.loads(prepared['messages'][1]['content'])['context']
    assert [d['speaker_relation'] for d in wire['discussion']] == ['CURRENT','THIRD_PARTY','PEER']
    for source, message in zip(c['discussion'], wire['discussion']):
        assert {k:message[k] for k in ('id','sequence','speaker','kind')} == {k:source[k] for k in ('id','sequence','speaker','kind')}
        assert message['passages'] == [{'id':'p0001','text':source['text']}]
    assert c == before and prepared['source_context'] == c
    assert wire['materials'][0]['passages'] == [{'id':'p0001','text':c['materials'][0]['text']}]
    assert all('speaker_relation' not in d for d in prepared['source_context']['discussion'])
    assert 'source_context' not in wire and len(wire['materials']) == len(c['materials'])


def test_relation_uses_current_ids_not_names_or_position_in_the_conversation():
    c = context(); c['character']['name'] = c['peers'][0]['name'] = '同名人物'
    wire = attributed_call_wire_context(c)
    assert [d['speaker_relation'] for d in wire['discussion']] == ['CURRENT','THIRD_PARTY','PEER']
    c['character'], c['peers'][0] = c['peers'][0], c['character']; c['reply_to'] = 'claim-1'
    wire = AttributedPackageCallModel(object(), settings()).prepare(c)
    messages = json.loads(wire['messages'][1]['content'])['context']['discussion']
    assert [d['speaker_relation'] for d in messages] == ['PEER','THIRD_PARTY','CURRENT']
    assert messages[-1]['speaker'] == 'c'  # The newest message is now the acting seat's own claim.


def test_idle_candidates_are_not_mislabeled_as_a_current_call_peer():
    c = context(); c.update(stage='IDLE', peers=[{'id':x,'name':x} for x in ('a','c','d','e')],
        reply_to=None, materials=[], planning_materials=[{'collection':'knowledge','id':'own',
        'kind':'FACT','public':False,'text':'本人可用于决定邀请的经历。'}])
    prepared = AttributedPackageCallModel(object(), settings()).prepare(c)
    wire = json.loads(prepared['messages'][1]['content'])['context']
    assert [d['speaker_relation'] for d in wire['discussion']] == ['CURRENT','THIRD_PARTY','THIRD_PARTY']
    assert wire['planning_materials'] == c['planning_materials']
    assert validate_call({'kind':'PASS','peer_character_id':None,'speech':None}, c, ATTRIBUTED_CALL_CONTRACT)


@pytest.mark.parametrize('tamper', ['wire_relation','wire_speaker','source_speaker','source_relation','reservation'])
def test_tampered_derived_relation_or_source_is_rejected_before_sdk(tamper):
    sdk = AsyncMock(); model = AttributedPackageCallModel(sdk, settings()); frozen = model.prepare(context()); bad = deepcopy(frozen)
    if tamper.startswith('wire_'):
        payload = json.loads(bad['messages'][1]['content'])
        if tamper == 'wire_relation': payload['context']['discussion'][0]['speaker_relation'] = 'PEER'
        else: payload['context']['discussion'][0]['speaker'] = 'c'
        bad['messages'][1]['content'] = canonical_json(payload)
    elif tamper == 'source_speaker': bad['source_context']['discussion'][0]['speaker'] = 'c'
    elif tamper == 'source_relation': bad['source_context']['discussion'][0]['speaker_relation'] = 'PEER'
    else: bad['input_tokens'] -= 1
    result = asyncio.run(model.call(bad))
    assert result['status'] == 'INVALID' and result['model_attempted'] is False
    sdk.chat_completion.assert_not_awaited(); assert model.prepare(context()) == frozen


def test_mock_dispatch_receives_exact_attributed_wire_and_original_speech_shape():
    sdk = AsyncMock(); model = AttributedPackageCallModel(sdk, settings()); prepared = model.prepare(context())
    async def complete(messages, **params):
        assert [dict(role=m.role,content=m.content) for m in messages] == prepared['messages']
        assert params == prepared['params']
        wire = json.loads(messages[1].content)['context']
        assert wire['discussion'][0]['speaker_relation'] == 'CURRENT'
        assert wire['discussion'][1]['speaker_relation'] == 'THIRD_PARTY'
        assert wire['discussion'][2]['speaker_relation'] == 'PEER'
        return response(speech())
    sdk.chat_completion.side_effect = complete
    result = asyncio.run(model.call(prepared))
    assert result['status'] == 'OK' and result['decision'] == speech()
    assert result['usage']['prompt_tokens'] == 100 and sdk.chat_completion.await_count == 1
    assert 'speaker_relation' not in canonical_json(result['decision'])


def test_legal_source_reference_alone_is_not_an_attribution_semantic_checker():
    value = speech('你刚才说你在庭院等候。')
    value['speech']['segments'][0]['basis'] = [{'collection':'discussion','id':'claim-1','passage_ids':['p0001']}]
    # This is intentionally a counterexample: mechanical acceptance cannot
    # prove that the natural language assigned the cited speaker correctly.
    assert validate_call(value, context(), ATTRIBUTED_CALL_CONTRACT) == value
    assert 'semantic_pass' not in canonical_json(value)


def test_attribution_and_default_fields_are_counted_at_exact_input_boundary():
    c = context(); c.pop('history_window'); c['revision'] = 220
    c['materials'] = [dict(collection='knowledge',id=f'fact-{i}',kind='FACT',text='本人已获准的场景。') for i in range(30)]
    c['discussion'] = [dict(id=f'claim-{i}',sequence=i,speaker='c' if i==220 else ('b','c','d')[i%3],
        kind='CLAIM',text='实际听到但尚待核对的说法。'*15) for i in range(1,221)]
    c['reply_to'] = 'claim-220'; before = deepcopy(c); model = AttributedPackageCallModel(object(), settings())
    full = call_window(c, 65536, ATTRIBUTED_CALL_CONTRACT)
    assert len(full['discussion']) == 60 and full['history_window']['omitted_count'] == 160
    prepared = model.prepare(full); limit = prepared['input_tokens'] - 4096 - 1
    selected = call_window(c, limit, ATTRIBUTED_CALL_CONTRACT)
    bounded = AttributedPackageCallModel(object(), settings(max_input_bytes=limit)); prepared = bounded.prepare(selected)
    size = len(canonical_json(prepared['messages']).encode()) + len(canonical_json(prepared['params']['response_format']).encode())
    assert size == prepared['input_tokens'] - 4096 <= limit
    assert len(selected['discussion']) < 60 and selected['discussion'][-1]['id'] == 'claim-220'
    assert selected['materials'] == c['materials'] and c == before
    assert prepared['source_context'] == StrategyCallContext.model_validate(selected).model_dump()
    wire = json.loads(prepared['messages'][1]['content'])['context']
    assert wire['discussion'][-1]['speaker_relation'] == 'PEER'
    assert all(m['retelling'] is None for m in prepared['source_context']['materials'])
    with pytest.raises(PackageRoleModelError, match='PACKAGE_CALL_INPUT_TOO_LARGE'): bounded.prepare(full)


def test_required_sources_cannot_be_removed_to_fit_the_byte_limit():
    c = context(); c['materials'][0]['text'] = '必须完整保留的本人经历。' * 2000
    sdk = AsyncMock(); model = AttributedPackageCallModel(sdk, settings()); before = deepcopy(c)
    with pytest.raises(PackageRoleModelError, match='PACKAGE_CALL_INPUT_TOO_LARGE'): model.prepare(c)
    with pytest.raises(PackageRoleModelError, match='REQUIRED_CONTEXT_TOO_LARGE'):
        call_window(c, 65536, ATTRIBUTED_CALL_CONTRACT)
    sdk.chat_completion.assert_not_awaited(); assert c == before


@pytest.mark.parametrize('kind', ['END','UNKNOWN'])
def test_end_and_explicit_uncertain_remain_available(kind):
    value = {'kind':'END','peer_character_id':None,'speech':None}
    if kind == 'UNKNOWN': value.update(kind='SPEAK',speech={'segments':[{'text':'','mode':'UNCERTAIN','basis':[]}]})
    assert validate_call(value, context(), ATTRIBUTED_CALL_CONTRACT) == value
