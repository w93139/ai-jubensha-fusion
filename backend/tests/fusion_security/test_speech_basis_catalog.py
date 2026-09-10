"""Provider schema binds authorized source pairs; local checks remain authoritative."""
import asyncio
from copy import deepcopy
import json

import pytest
from jsonschema import Draft202012Validator

from src.fusion.package_dialogue_model import (speech_schema, dialogue_context_window,
    CatalogFullPackageDialogueModel, StrategyFullPackageDialogueModel, dialogue_metadata)
from src.fusion.package_call_model import response_format
from src.fusion.package_play import PackagePlayService
from src.fusion.package_validation import canonical_json
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_play_store import play, start, action_body
from tests.fusion_security.test_full_play_speech_strategy import package, speech
from tests.fusion_security.test_full_play_decisions import output
from tests.fusion_security.test_full_play_phone import phone, invite, say


def context():
    return dict(materials=[dict(collection='knowledge',id='own'),dict(collection='memory',id='memory')],
                discussion=[dict(id='response-4')],strategy_materials=[dict(collection='knowledge',id='secret')],
                planning_materials=[dict(collection='knowledge',id='plan-only')])


@pytest.mark.parametrize('collection,identifier,valid',[('knowledge','own',True),('memory','memory',True),
    ('discussion','response-4',True),('knowledge','response-4',False),('discussion','own',False),
    ('knowledge','secret',False),('knowledge','plan-only',False),('knowledge','future',False)])
def test_catalog_pairs_not_independent_enums(collection,identifier,valid):
    c=context();new=speech_schema('package-dialogue-model/1.5',c);old=speech_schema('package-dialogue-model/1.4',c)
    body=speech([dict(collection=collection,id=identifier)])
    assert Draft202012Validator(old).is_valid(body)
    assert Draft202012Validator(new).is_valid(body)==valid
    call=dict(kind='SPEAK',peer_character_id=None,speech=body)
    assert Draft202012Validator(response_format('package-call-model/1.2',c)['json_schema']['schema']).is_valid(call)==valid
    assert 'secret' not in canonical_json(new) and 'plan-only' not in canonical_json(new)


def test_catalog_overlapping_ids_respect_real_collection_not_name_prefix():
    c=context();c['materials'].append(dict(collection='knowledge',id='response-4'))
    v=Draft202012Validator(speech_schema('package-dialogue-model/1.5',c))
    assert v.is_valid(speech([dict(collection='knowledge',id='response-4')]))
    assert v.is_valid(speech([dict(collection='discussion',id='response-4')]))
    assert not v.is_valid(speech([dict(collection='memory',id='response-4')]))


def test_catalog_empty_directory_retains_unknown_without_invalid_empty_enum():
    c=dict(materials=[],discussion=[],strategy_materials=[])
    schema=speech_schema('package-dialogue-model/1.5',c);Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(speech([], 'UNCERTAIN',''))
    assert not Draft202012Validator(schema).is_valid(speech())
    schema=response_format('package-call-model/1.2',c)['json_schema']['schema'];Draft202012Validator.check_schema(schema)
    assert Draft202012Validator(schema).is_valid(dict(kind='PASS',peer_character_id=None,speech=None))
    assert Draft202012Validator(schema).is_valid(dict(kind='SPEAK',peer_character_id=None,speech=speech([], 'UNCERTAIN','')))


def modern(play,db=None):
    return PackagePlayService(db if db is not None else play.db,play.publisher,play.model,play.policy,
                              lambda:play.clock[0],speech_policy='role-speech/1.2')


def begin(play):
    play.play=modern(play);_,v=start(play,package())
    return play.play.act(v['play_id'],action_body(0,'advance','ADVANCE_PHASE'),1)


@pytest.mark.parametrize('wrong',[False,True])
def test_catalog_public_reply_real_service_dispatch_and_restore(play,wrong):
    v=begin(play);pid=v['play_id']
    v=play.play.speak(pid,dict(schema_version='package-discussion-command/1.0',action='SPEAK',text='请核对一下。',expected_revision=1,idempotency_key='statement'),1)
    req=dict(schema_version='package-dialogue-command/1.0',action='RESPOND',character_id='b',reply_to='statement-2',expected_revision=2,idempotency_key='reply')
    async def complete(messages,**params):
        c=json.loads(messages[1].content)['context'];schema=params['response_format']['json_schema']['schema']
        value=speech([dict(collection='knowledge' if wrong else 'discussion',id=c['reply_to'])])
        assert Draft202012Validator(schema).is_valid(value)==(not wrong)
        return output(value)
    play.sdk.chat_completion.side_effect=complete
    v=asyncio.run(play.play.respond(pid,req,1));assert v['last_ai_status']==('INVALID' if wrong else 'OK')
    assert asyncio.run(play.play.respond(pid,req,1))==v and play.sdk.chat_completion.await_count==1
    with play.factory() as db:assert modern(play,db).get(pid,1)==v


def test_catalog_phone_pair_success_uses_dynamic_schema_and_restores(play):
    v=begin(play);pid=v['play_id'];count=0
    async def complete(messages,**params):
        nonlocal count
        count+=1;c=json.loads(messages[1].content)['context'];value=invite() if count==1 else say(c)
        Draft202012Validator(params['response_format']['json_schema']['schema']).validate(value)
        return output(value)
    play.sdk.chat_completion.side_effect=complete
    for _ in range(2):
        req=phone(v['revision']);v=asyncio.run(play.play.phone_step(pid,req,1));assert v['last_ai_status']=='OK'
        assert asyncio.run(play.play.phone_step(pid,req,1))==v
        with play.factory() as db:assert modern(play,db).get(pid,1)==v
    assert count==2


def test_catalog_final_window_only_and_budget_includes_schema(play):
    v=begin(play);row=play.play._row(v['play_id'],1);p,b=play.play._resolve(row);state=play.play._replay(row,p,b)
    c=dict(schema_version='package-dialogue-context/1.2',play_id=v['play_id'],package_hash=b['package_hash'],revision=100,channel='PUBLIC',
           **state.engine.dialogue_context('b'),strategy_materials=state.engine.speech_strategy('b'),
           discussion=[dict(id=f'statement-{i}',sequence=i,speaker='a',text='核对资料。'*100,kind='CLAIM') for i in range(1,101)],reply_to='statement-1')
    c=dialogue_context_window(c,12000,'package-dialogue-model/1.5')
    assert c['history_window']['omitted_count']>0
    retained={x['id'] for x in c['discussion']};schema=speech_schema('package-dialogue-model/1.5',c)
    branch=next(x for x in schema['$defs']['DialogueBasis']['anyOf'] if x['properties']['collection']['enum']==['discussion'])
    assert set(branch['properties']['id']['enum'])==retained and 'statement-1' in retained
    prepared=play.play.full_dialogue_model.prepare(c)
    size=len(canonical_json(prepared['messages']).encode())+len(canonical_json(prepared['params']['response_format']).encode())
    assert size<=12000 and prepared['input_tokens']==size+4096


def test_catalog_keeps_old_schema_prompt_and_metadata_distinct(play):
    from src.fusion.package_dialogue_model import PROMPTS
    before=speech_schema('package-dialogue-model/1.4',context());snapshot=deepcopy(before)
    speech_schema('package-dialogue-model/1.5',context());assert before==snapshot
    assert PROMPTS['package-dialogue-model/1.4']==PROMPTS['package-dialogue-model/1.5']
    base=play.model.metadata();old=dialogue_metadata(base,'package-dialogue-model/1.4');new=dialogue_metadata(base,'package-dialogue-model/1.5')
    assert old['prompt_hash']==new['prompt_hash'] and 'basis_policy_hash' not in old and 'basis_policy_hash' in new


def test_catalog_service_restores_strategy_version_before_update_without_upgrade(play):
    from tests.fusion_security.test_full_play_speech_strategy import begin as legacy_begin
    v=legacy_begin(play);pid=v['play_id']
    v=play.play.speak(pid,dict(schema_version='package-discussion-command/1.0',action='SPEAK',text='请核对一下。',expected_revision=1,idempotency_key='statement'),1)
    req=dict(schema_version='package-dialogue-command/1.0',action='RESPOND',character_id='b',reply_to='statement-2',expected_revision=2,idempotency_key='reply')
    play.sdk.chat_completion.return_value=output(speech())
    v=asyncio.run(play.play.respond(pid,req,1));assert v['last_ai_status']=='OK'
    row=play.play._row(pid,1);p,b=play.play._resolve(row);before=play.play._replay(row,p,b).state()
    upgraded=modern(play);after=upgraded.get(pid,1)
    assert upgraded._replay(row,p,b).state()==before
    assert after['budget']==v['budget'] and after['role_responses']['entries']==v['role_responses']['entries']
    assert asyncio.run(upgraded.respond(pid,req,1))==after and play.sdk.chat_completion.await_count==1
    from src.fusion.package_play import PackagePlayError
    with pytest.raises(PackagePlayError):asyncio.run(upgraded.respond(pid,{**req,'expected_revision':v['revision'],'idempotency_key':'new'},1))
