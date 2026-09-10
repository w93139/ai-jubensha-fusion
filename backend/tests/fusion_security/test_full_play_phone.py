"""Anonymous one-step phone controller, fictional isolated materials only."""
import asyncio
import json
from dataclasses import replace

import pytest

from src.fusion.package_play import PackagePlayError
from src.fusion.package_validation import canonical_json
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_play_store import play, start, service, action_body, events
from tests.fusion_security.test_package_full_play import full_package
from tests.fusion_security.test_full_play_decisions import output
from tests.fusion_security.test_full_play_store import command


def phone(revision,key=None):
    return dict(schema_version='package-phone-command/1.0',action='PHONE_STEP',expected_revision=revision,idempotency_key=key or f'phone-{revision}')


def pause(revision):
    return dict(schema_version='package-phone-pause-command/1.0',action='PAUSE_PHONE',expected_revision=revision,idempotency_key=f'pause-{revision}')


def open_phase(play):
    _,v=start(play,full_package())
    return play.play.act(v['play_id'],action_body(0,'advance','ADVANCE_PHASE'),1)


def invite(peer='c'):return dict(kind='INVITE',peer_character_id=peer,speech=None)
def end():return dict(kind='END',peer_character_id=None,speech=None)
def say(context):return dict(kind='SPEAK',peer_character_id=None,speech=dict(segments=[dict(text='你愿意进一步说明吗？',mode='QUESTION',basis=[dict(collection='discussion',id=context['reply_to'])])]))


def test_full_play_phone_ai_pair_private_roundtrip_and_once_receipt(play):
    v=open_phase(play);pid=v['play_id'];captured=[]
    async def complete(messages,**params):
        assert not play.db.in_transaction()
        with play.factory() as db: assert service(play,db).get(pid,1)['pending_ai']
        c=json.loads(messages[1].content)['context'];captured.append(c)
        if len(captured)==1:
            assert c['character']['id']=='b' and c['stage']=='IDLE'
            assert 'PRIVATE_BOOK_b' in canonical_json(c) and 'PRIVATE_BOOK_c' not in canonical_json(c)
            return output(invite())
        assert c['character']['id']==('c' if len(captured)==2 else 'b')
        assert c['planning_materials']==[] and 'PRIVATE_BOOK_' not in canonical_json(c)
        return output(say(c) if len(captured)==2 else end())
    play.sdk.chat_completion.side_effect=complete
    for _ in range(3):
        req=phone(v['revision']);v=asyncio.run(play.play.phone_step(pid,req,1))
        assert v['last_ai_status']=='OK'
        assert v['full_game']['call'] is None and v['full_game']['private_discussion']==[]
        assert v['private_replies']['requests']==[] and v['private_replies']['options']==[]
        assert all(set(r)=={'request_id','revision','status'} for r in v['phone_turns']['requests'])
        assert 'reply_to' not in canonical_json(v['phone_turns'])
        count=play.sdk.chat_completion.await_count
        assert asyncio.run(play.play.phone_step(pid,req,1))==v and play.sdk.chat_completion.await_count==count
        with play.factory() as db: assert service(play,db).get(pid,1)==v
    assert not v['full_game']['phone_busy'] and v['full_game']['can_open_ballot']
    assert len(captured)==3


def test_full_play_phone_human_receives_invitation_then_must_speak(play):
    v=open_phase(play);pid=v['play_id'];play.sdk.chat_completion.return_value=output(invite('a'))
    v=asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    assert v['full_game']['call']['character_ids']==['b','a'] and not v['phone_turns']['available']
    assert v['full_game']['private_discussion'][-1]['origin']=='PROGRAM_OPENING'
    with pytest.raises(PackagePlayError,match='WAITING_FOR_HUMAN'):
        asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    v=play.play.table(pid,command(v['revision'],'PRIVATE_SPEAK',{'text':'请说说你的看法。'}),1)
    play.sdk.chat_completion.side_effect=lambda messages,**kwargs:output(say(json.loads(messages[1].content)['context']))
    v=asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    assert v['last_ai_status']=='OK' and len(v['full_game']['private_discussion'])==3
    assert v['discussion']['entries']==[] and not v['phone_turns']['available']


@pytest.mark.parametrize('bad',[{'kind':'PASS','peer_character_id':'c','speech':None},end(),dict(kind='INVITE',peer_character_id='b',speech=None),dict(kind='INVITE',peer_character_id='future',speech=None)])
def test_full_play_phone_bad_invitation_never_connects(play,bad):
    v=open_phase(play);play.sdk.chat_completion.return_value=output(bad)
    v=asyncio.run(play.play.phone_step(v['play_id'],phone(v['revision']),1))
    assert v['last_ai_status']=='INVALID' and not v['full_game']['phone_busy']


def test_full_play_phone_unknown_then_zero_model_pause_and_late_result(play):
    v=open_phase(play);pid=v['play_id'];play.sdk.chat_completion.return_value=output(invite())
    v=asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    req=phone(v['revision']);_,prepared=play.play._begin(pid,req,1)
    v=play.play.get(pid,1);assert v['pending_ai']
    pause_req=pause(v['revision']);v=play.play.pause_phone(pid,pause_req,1);play.db.commit()
    assert not v['full_game']['phone_busy'] and v['pending_ai']
    play.sdk.chat_completion.return_value=output(end())
    result=asyncio.run(play.play.phone_model.call(prepared))
    v=play.play._finish(pid,req['idempotency_key'],1,result)
    assert v['last_ai_status']=='STALE' and not v['full_game']['phone_busy']
    calls=play.sdk.chat_completion.await_count
    assert play.play.pause_phone(pid,pause_req,1)==v
    assert asyncio.run(play.play.phone_step(pid,req,1))==v and play.sdk.chat_completion.await_count==calls


def test_full_play_phone_transport_unknown_no_resend_and_budget_free_exit(play):
    v=open_phase(play);pid=v['play_id'];play.sdk.chat_completion.return_value=output(invite())
    v=asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    req=phone(v['revision']);play.sdk.chat_completion.side_effect=RuntimeError('private debug')
    v=asyncio.run(play.play.phone_step(pid,req,1));assert v['last_ai_status']=='UNKNOWN'
    assert asyncio.run(play.play.phone_step(pid,req,1))==v and play.sdk.chat_completion.await_count==2
    play.play.policy=replace(play.play.policy,paid_calls_enabled=False)
    v=play.play.pause_phone(pid,pause(v['revision']),1)
    assert not v['full_game']['phone_busy'] and play.sdk.chat_completion.await_count==2


def test_full_play_phone_client_cannot_select_hidden_actor_or_reply(play):
    v=open_phase(play)
    for extra in [dict(character_id='c'),dict(reply_to='private-3'),dict(text='fabricated')]:
        with pytest.raises(PackagePlayError,match='REQUEST_INVALID'):
            asyncio.run(play.play.phone_step(v['play_id'],{**phone(v['revision']),**extra},1))
    assert play.sdk.chat_completion.await_count==0


def test_full_play_phone_actual_speech_grants_only_listener_never_opening(play):
    v=open_phase(play);pid=v['play_id'];play.sdk.chat_completion.return_value=output(invite())
    v=asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    row=play.play._row(pid,1);p,b=play.play._resolve(row)
    assert play.play._replay(row,p,b).engine.state()['memory_grants']==[]
    async def complete(messages,**params):
        c=json.loads(messages[1].content)['context'];value=say(c)
        value['speech']['segments'][0]['text']='你是否听说过三角木片？'
        return output(value)
    play.sdk.chat_completion.side_effect=complete
    v=asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    state=play.play._replay(play.play._row(pid,1),p,b)
    assert {m['id'] for m in state.engine.state()['memory_grants']}=={'memory-b'}
    assert v['memories']['entries']==[] and state.engine.personal_discussion('d')==[]
    async def retell(messages,**params):
        c=json.loads(messages[1].content)['context'];value=say(c)
        assert c['character']['id']=='b'
        value['speech']['segments'][0].update(text='那段往事中还有什么需要核对？',basis=[dict(collection='memory',id='memory-b')])
        return output(value)
    play.sdk.chat_completion.side_effect=retell;req=phone(v['revision'])
    v=asyncio.run(play.play.phone_step(pid,req,1));assert v['last_ai_status']=='OK'
    assert asyncio.run(play.play.phone_step(pid,req,1))==v
    state=play.play._replay(play.play._row(pid,1),p,b)
    assert state.retelling_attempts==[dict(character_id='b',memory_id='memory-b',sequence=v['revision'],status='ATTEMPTED_UNVERIFIED')]
    assert state.state()['phone_turns']['retelling_attempts']==state.retelling_attempts


def test_full_play_phone_genuine_pass_rotates_and_expired_attempt_never_resends(play):
    v=open_phase(play);pid=v['play_id'];actors=[]
    async def complete(messages,**params):
        actors.append(json.loads(messages[1].content)['context']['character']['id'])
        return output(dict(kind='PASS',peer_character_id=None,speech=None))
    play.sdk.chat_completion.side_effect=complete
    v=asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    assert v['last_ai_status']=='OK' and not v['full_game']['phone_busy']
    req=phone(v['revision']);play.play._begin(pid,req,1)
    play.play.now=lambda:2000
    v=asyncio.run(play.play.phone_step(pid,req,1))
    assert v['last_ai_status']=='EXPIRED' and actors==['b']
    with play.factory() as db: assert service(play,db).get(pid,1)['phone_turns']['requests']==v['phone_turns']['requests']


def test_full_play_phone_rejects_goals_as_speech_basis_and_keeps_line_for_pause(play):
    v=open_phase(play);pid=v['play_id'];play.sdk.chat_completion.return_value=output(invite())
    v=asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    async def complete(messages,**params):
        c=json.loads(messages[1].content)['context'];value=say(c)
        value['speech']['segments'][0]['basis']=[dict(collection='knowledge',id='private-c')]
        return output(value)
    play.sdk.chat_completion.side_effect=complete
    v=asyncio.run(play.play.phone_step(pid,phone(v['revision']),1))
    assert v['last_ai_status']=='INVALID' and v['full_game']['phone_busy']
    assert v['full_game']['private_discussion']==[]


from unittest.mock import AsyncMock, Mock
from contextlib import nullcontext
from types import SimpleNamespace
from src.fusion.package_play import PackagePlayService
from src.fusion.package_play_engine import play_engine
from tests.fusion_security.test_package_play_http import play_http


@pytest.mark.parametrize('suffix,method,body',[('phone-step','phone_step',phone(0)),('phone-pause','pause_phone',pause(0))])
@pytest.mark.parametrize('token,status',[(None,401),('disabled',403),('player',200)])
def test_full_play_phone_http_owner_dispatch(play_http,suffix,method,body,token,status):
    client,svc=play_http;mock=AsyncMock(return_value={}) if method=='phone_step' else Mock(return_value={})
    setattr(svc,method,mock)
    r=client.post('/api/fusion/package-plays/play-x/'+suffix,json=body,headers={'Authorization':'Bearer '+token} if token else {})
    assert r.status_code==status
    if status==200:
        assert r.headers['Cache-Control']=='no-store' and mock.call_args.args[1].model_dump()==body and mock.call_args.args[2]==2
    else:mock.assert_not_called()


@pytest.mark.parametrize('status',['STALE','EXPIRED','UNKNOWN'])
def test_full_play_phone_last_result_slot_survives_pause(status):
    instance=object.__new__(PackagePlayService);instance.db=Mock();instance.db.begin_nested.side_effect=lambda:nullcontext()
    engine=play_engine(full_package(),'a');engine.apply('ADVANCE_PHASE')
    engine.table_apply('START_CALL','b',{'peer_character_id':'c'},1)
    state=SimpleNamespace(engine=engine,revision=1997,pending={},previous='0'*64,phone_model={},state=lambda:{})
    def consume(state,kind,request,data,binding):
        if kind=='AI_REQUEST':state.pending['step']={'operation':'PHONE'}
        elif kind=='ACTION':state.engine.pause_phone(state.revision+1)
        else:state.pending.clear()
    instance._consume=consume;row=SimpleNamespace(play_id='play-'+'1'*32)
    instance._append(row,{},state,'AI_REQUEST',phone(1997,'step'),{})
    instance._append(row,{},state,'ACTION',pause(1998),{})
    instance._append(row,{},state,'AI_RESULT',{'idempotency_key':'step','request_id':'step'},{'status':status,'decision':None})
    assert state.revision==2000 and not state.pending and not state.engine.view()['full_game']['phone_busy']
