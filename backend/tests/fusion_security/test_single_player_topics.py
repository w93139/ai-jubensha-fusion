"""Fictional finite topics: admission, knowledge, privacy, receipts and replay."""
import asyncio
from copy import deepcopy
import json

import pytest

from src.fusion.package_single_player import SinglePlayerContent
from src.fusion.package_guided_flow import digest
from src.fusion.package_play import PackagePlayError
from src.fusion.package_validation import content_hash, canonical_json
from tests.fusion_security.test_package_play_store import play, start, events, service, ask_body
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_full_play import full_package, REFS
from tests.fusion_security.test_package_guided_flow import enter
from tests.fusion_security.test_full_play_store import command


def document(package):
    return {'schema_version':'single-player-content/1.0','package_hash':content_hash(package),
        'selected_character_id':'a','operation_rules':'## 单人操作\n由你选择调查地点。',
        'replaces_materials':[{'collection':'knowledge','id':'initial-a'}],
        'phase_guides':[{'phase_id':p['id'],'goal':'核对本轮见闻','instructions':['阅读材料','可以选择议题交流'],
            'completion':'可以记未知，不要求穷尽议题。','source_refs':REFS} for p in package['phases']],
        'topics':[{'id':'scene','phase_id':'investigate-one','title':'现场见闻','display_gate':[],
            'intents':[{'id':i,'label':i,'question':'你能说明三角木片的见闻吗？' if i=='initial' else '哪些还不能确定？'}
                       for i in ('initial','clarify','unknown')],
            'responders':[{'character_id':'b','channels':['PUBLIC','PRIVATE'],'disclosure':{'policy':'MAY_RETELL','unknown':'不可编造'},
                'basis':[{'collection':'knowledge','id':'initial-b','text_sha256':digest(package['knowledge'][1]['text']),'source_refs':REFS}],
                'answers':[{'intent_id':i,'fixed_fallback':'我只能说见过三角木片，其他经过目前无法确定。'} for i in ('initial','clarify','unknown')]}],
            'source_refs':REFS}]}


def setup(play):
    package = full_package()
    package['knowledge'][1]['retelling'] = 'MAY_RETELL'
    doc = document(package)
    play.play.single_player_content = {content_hash(package):SinglePlayerContent(package,doc)}
    _, view = start(play,package)
    return package, doc, view


def ask_topic(play,view,intent='initial',channel='PUBLIC',**changes):
    body = {'schema_version':'package-topic-command/1.0','expected_revision':view['revision'],
            'idempotency_key':f'topic-{view["revision"]}-{intent}', 'action':'ASK_TOPIC',
            'payload':{'topic_id':'scene','character_id':'b','intent_id':intent,'channel':channel}}
    body.update(changes)
    return body, play.play.topic(view['play_id'],body,1)


def fallback(play,view,turn=None):
    body={'schema_version':'package-topic-command/1.0','expected_revision':view['revision'],
          'idempotency_key':f'fallback-{view["revision"]}','action':'USE_FALLBACK',
          'payload':{'turn_id':turn or view['single_player']['turns'][-1]['id']}}
    return body, play.play.topic(view['play_id'],body,1)


def test_stage_rules_no_future_answers_and_strict_gate(play):
    package,doc,view=setup(play)
    assert view['single_player']['topics']==[]
    assert view['private_knowledge'][0]['text']==doc['operation_rules']
    assert 'fixed_fallback' not in canonical_json(view) and '我只能说' not in canonical_json(view)
    with pytest.raises(PackagePlayError): ask_topic(play,view)
    view=enter(play,view)
    with pytest.raises(PackagePlayError): ask_topic(play,view,'clarify')
    assert len(view['single_player']['topics'])==1
    doc['topics'][0]['display_gate']=[[{'collection':'evidence','id':'evidence-find-key'}]]
    play.play.single_player_content={content_hash(package):SinglePlayerContent(package,doc)}
    assert play.play.get(view['play_id'],1)['single_player']['topics']==[]
    with pytest.raises(PackagePlayError): ask_topic(play,view)
    assert play.sdk.chat_completion.await_count==0


def test_question_then_one_model_receipt_and_explicit_fallback_no_duplicate(play):
    _,_,view=setup(play);view=enter(play,view)
    body,view=ask_topic(play,view)
    turn=view['single_player']['turns'][-1]
    assert turn['status']=='READY' and not turn['can_fallback']
    assert play.play.topic(view['play_id'],body,1)==view
    with pytest.raises(PackagePlayError): fallback(play,view)
    with pytest.raises(PackagePlayError): ask_topic(play,view,'clarify')
    req=turn['reply_request']
    # Fixture returns a material reply, deliberately invalid for role speech.
    view=asyncio.run(play.play.respond(view['play_id'],req,1))
    assert view['single_player']['turns'][-1]['status']=='FAILED'
    before=view['budget']['used_tokens']
    assert before>0 and play.sdk.chat_completion.await_count==1
    assert asyncio.run(play.play.respond(view['play_id'],req,1))==view
    fb,view=fallback(play,view)
    assert view['single_player']['turns'][-1]['status']=='FALLBACK'
    assert len(view['memories']['entries'])==1
    assert view['budget']['used_tokens']==before
    assert play.play.topic(view['play_id'],fb,1)==view
    assert asyncio.run(play.play.respond(view['play_id'],req,1))==view
    with pytest.raises(PackagePlayError): ask_topic(play,view)
    _,view=ask_topic(play,view,'clarify')
    assert len(view['single_player']['turns'])==2
    play.db.commit()
    old=[(e.event_json,e.request_json,e.state_hash) for e in events(play)]
    replay=service(play,play.db).get(view['play_id'],1)
    assert not replay['single_player']['available']
    assert replay['single_player']['turns'][0]==view['single_player']['turns'][0]
    assert replay['single_player']['turns'][1]['reply_request'] is None
    assert replay['single_player']['turns'][1]['can_fallback']
    assert [(e.event_json,e.request_json,e.state_hash) for e in events(play)]==old


def test_free_or_forged_requests_rejected_before_charge_but_old_repeat_allowed(play):
    _,_,view=setup(play);view=enter(play,view)
    with pytest.raises(PackagePlayError,match='SINGLE_TOPIC_REQUIRED'):
        asyncio.run(play.play.ask(view['play_id'],ask_body(view['revision']),1))
    _,view=ask_topic(play,view)
    original=view['single_player']['turns'][-1]['reply_request']
    for changes in ({'character_id':'c'},{'idempotency_key':'arbitrary'},{'reply_to':'statement-1'}):
        with pytest.raises(PackagePlayError): asyncio.run(play.play.respond(view['play_id'],{**original,**changes},1))
    assert play.sdk.chat_completion.await_count==0


def test_required_binding_without_catalog_fails_closed_before_first_topic(play):
    package,_,view=setup(play);view=enter(play,view)
    play.play.single_player_required={content_hash(package):'a'}
    play.play.single_player_content.clear()
    view=play.play.get(view['play_id'],1)
    assert not view['single_player']['available'] and view['single_player']['stage'] is None
    assert view['single_player']['topics']==[]
    with pytest.raises(PackagePlayError,match='SINGLE_TOPIC_REQUIRED'):
        asyncio.run(play.play.ask(view['play_id'],ask_body(view['revision']),1))
    assert play.sdk.chat_completion.await_count==0


def test_private_topics_only_current_peer_hears_and_channels_share_limit(play):
    _,_,view=setup(play);view=enter(play,view)
    view=play.play.table(view['play_id'],command(view['revision'],'START_CALL',{'peer_character_id':'b'}),1)
    with pytest.raises(PackagePlayError): ask_topic(play,view)
    _,view=ask_topic(play,view,channel='PRIVATE')
    turn=view['single_player']['turns'][-1]
    assert turn['reply_to'].startswith('private-') and view['discussion']['entries']==[]
    view=asyncio.run(play.play.reply_private(view['play_id'],turn['reply_request'],1))
    _,view=fallback(play,view)
    assert view['discussion']['entries']==[] and len(view['memories']['entries'])==1
    row=play.play._row(view['play_id'],1);package,binding=play.play._resolve(row)
    state=play.play._replay(row,package,binding)
    assert not any(g['id']=='memory-c' for g in state.engine.state()['memory_grants'])
    view=play.play.table(view['play_id'],command(view['revision'],'STOP_CALL'),1)
    with pytest.raises(PackagePlayError): ask_topic(play,view)


def test_topic_context_is_frozen_and_only_selected_materials(play):
    package,doc,view=setup(play);view=enter(play,view)
    _,view=ask_topic(play,view)
    req=view['single_player']['turns'][-1]['reply_request']
    row=play.play._row(view['play_id'],1);package,binding=play.play._resolve(row)
    state=play.play._replay(row,package,binding)
    context=play.play._dialogue_context(state,binding,'b',req['reply_to'],version=play.play.full_dialogue_model.model_contract)
    assert {(m['collection'],m['id']) for m in context['materials']}=={('knowledge','initial-b')}
    doc['topics'][0]['responders'][0]['answers'][0]['fixed_fallback']='后来修改的模板'
    play.play.single_player_content={content_hash(package):SinglePlayerContent(package,doc)}
    view=asyncio.run(play.play.respond(view['play_id'],req,1))
    _,view=fallback(play,view)
    assert '后来修改' not in canonical_json(view['single_player']['turns'])
    _,view=ask_topic(play,view,'clarify')
    row=play.play._row(view['play_id'],1);package,binding=play.play._resolve(row)
    state=play.play._replay(row,package,binding)
    context=play.play._dialogue_context(state,binding,'b',view['single_player']['turns'][-1]['reply_to'],
                                      version=play.play.full_dialogue_model.model_contract)
    assert any(e['speaker']=='b' and '其他经过' in e['text'] for e in context['discussion'])


def test_stale_unsent_question_can_use_explicit_fallback_without_model(play):
    _,_,view=setup(play);view=enter(play,view)
    _,view=ask_topic(play,view)
    view=play.play.speak(view['play_id'],{'schema_version':'package-discussion-command/1.0','action':'SPEAK',
        'expected_revision':view['revision'],'idempotency_key':'my-observation','text':'我想再看一下资料。'},1)
    assert view['single_player']['turns'][-1]['reply_request'] is None
    _,view=fallback(play,view)
    assert play.sdk.chat_completion.await_count==0 and view['single_player']['turns'][-1]['status']=='FALLBACK'


@pytest.mark.parametrize('mutation', ['hash','role','future-basis','bad-fallback'])
def test_invalid_content_rejected(play,mutation):
    package=full_package();package['knowledge'][1]['retelling']='MAY_RETELL';doc=document(package)
    if mutation=='hash':doc['package_hash']='0'*64
    if mutation=='role':doc['topics'][0]['responders'][0]['character_id']='c'
    if mutation=='future-basis':doc['topics'][0]['responders'][0]['basis'][0]['text_sha256']='0'*64
    if mutation=='bad-fallback':doc['topics'][0]['responders'][0]['answers'][0]['fixed_fallback']=''
    with pytest.raises(ValueError):SinglePlayerContent(package,doc)
