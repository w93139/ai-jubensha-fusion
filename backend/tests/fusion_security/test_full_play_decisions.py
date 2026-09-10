"""Formal AI submissions use the real adapter and an isolated fictional SDK."""
import asyncio
from copy import deepcopy
import json

import pytest

from src.fusion.package_play import PackagePlayError
from src.fusion.package_play import Replay, PackagePlayService
from src.fusion.package_role_model import PackageRoleModelError
from src.fusion.package_table_model import validate_table_decision, TableContext
from src.fusion.package_validation import canonical_json
from src.services.llm_service import LLMResponse
from tests.fusion_security.test_package_play_store import play, service, start, events, action_body, MODEL
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_full_play_store import command
from tests.fusion_security.test_package_full_play import full_package
from tests.fusion_security.test_structured_finale import submission
from tests.fusion_security.test_package_memories import memory_package


def decision(revision, actor='b', action='CAST_BALLOT', key=None):
    return {'schema_version': 'package-table-decision-command/1.0', 'expected_revision': revision,
            'idempotency_key': key or f'decide-{revision}', 'character_id': actor, 'action': action}


def output(value):
    return LLMResponse(content=canonical_json(value), usage={'prompt_tokens': 100, 'completion_tokens': 10},
                       model=MODEL, finish_reason='stop')


def open_ballot(play):
    _, view = start(play, full_package())
    identifier = view['play_id']
    play.play.act(identifier, action_body(0, 'advance', 'ADVANCE_PHASE'), 1)
    return play.play.table(identifier, command(1, 'OPEN_BALLOT'), 1)


def test_full_play_ai_ballot_reserves_before_call_recovers_and_cannot_change(play):
    view = open_ballot(play); identifier = view['play_id']
    captured = []
    async def complete(messages, **params):
        assert not play.db.in_transaction()
        with play.factory() as db:
            assert service(play, db).get(identifier, 1)['pending_ai']
        context = json.loads(messages[1].content)['context']; captured.append(context)
        assert context['character']['id'] == 'b'
        raw = canonical_json(context)
        assert 'PRIVATE_BOOK_b' in raw
        for forbidden in ('PRIVATE_BOOK_a', 'PRIVATE_BOOK_c', 'SYSTEM_TRUTH', 'sources', 'group_id', 'facts'):
            assert forbidden not in raw
        return output({'kind': 'CHOOSE', 'choice_id': 'find-key'})
    play.sdk.chat_completion.side_effect = complete
    req = decision(2)
    view = asyncio.run(play.play.decide(identifier, req, 1))
    assert view['last_ai_status'] == 'OK' and view['revision'] == 4
    assert view['full_game']['ballot']['sealed_count'] == 1
    assert view['full_game']['ballot']['ballot'] is None
    assert view['mechanics']['spent_points'] == 0
    assert view['table_decisions']['requests'][-1]['status'] == 'OK'
    assert 'choice_id' not in canonical_json(view['table_decisions'])
    assert asyncio.run(play.play.decide(identifier, req, 1)) == view
    with play.factory() as db: assert service(play, db).get(identifier, 1) == view
    with pytest.raises(PackagePlayError, match='ALREADY_SUBMITTED'):
        asyncio.run(play.play.decide(identifier, decision(4), 1))
    assert play.sdk.chat_completion.await_count == 1


@pytest.mark.parametrize('bad', [
    {'kind': 'CHOOSE', 'choice_id': 'future-hidden'},
    {'kind': 'ABSTAIN'}, {'kind': 'SKIP', 'choice_id': 'find-key'},
    {'kind': 'ABSTAIN', 'choice_id': None, 'score': 5},
])
def test_full_play_bad_ai_output_does_not_cast_or_skip(play, bad):
    view = open_ballot(play); identifier = view['play_id']
    play.sdk.chat_completion.return_value = output(bad)
    view = asyncio.run(play.play.decide(identifier, decision(2), 1))
    assert view['last_ai_status'] == 'INVALID' and view['full_game']['ballot']['sealed_count'] == 0
    assert not view['can_advance'] and view['budget']['used_tokens'] == 110
    assert view['table_decisions']['requests'][-1]['status'] == 'INVALID'
    with play.factory() as db: assert service(play, db).get(identifier, 1) == view


def test_full_play_unknown_ai_stays_unsubmitted_and_same_key_never_retries(play):
    view = open_ballot(play); identifier = view['play_id']; req = decision(2)
    play.sdk.chat_completion.side_effect = RuntimeError('private provider detail')
    view = asyncio.run(play.play.decide(identifier, req, 1))
    assert view['last_ai_status'] == 'UNKNOWN' and view['full_game']['ballot']['sealed_count'] == 0
    assert view['budget']['used_tokens'] > 110 and not view['pending_ai']
    assert asyncio.run(play.play.decide(identifier, req, 1)) == view
    assert play.sdk.chat_completion.await_count == 1
    assert 'private provider detail' not in canonical_json(view)


def test_full_play_late_ai_ballot_accounted_but_not_applied(play):
    view = open_ballot(play); identifier = view['play_id']
    async def complete(messages, **params):
        play.play.speak(identifier, {'schema_version': 'package-discussion-command/1.0', 'action': 'SPEAK',
            'expected_revision': 3, 'idempotency_key': 'interrupt', 'text': '再考虑新的说法。'}, 1)
        play.db.commit()
        return output({'kind': 'CHOOSE', 'choice_id': 'find-key'})
    play.sdk.chat_completion.side_effect = complete
    view = asyncio.run(play.play.decide(identifier, decision(2), 1))
    assert view['last_ai_status'] == 'STALE' and view['full_game']['ballot']['sealed_count'] == 0
    assert view['budget']['used_tokens'] == 110


def test_full_play_expired_reservation_recovered_without_resending(play):
    view = open_ballot(play); identifier = view['play_id']; req = decision(2)
    _, prepared = play.play._begin(identifier, req, 1)
    assert prepared and play.sdk.chat_completion.await_count == 0
    play.clock[0] += 100
    view = asyncio.run(play.play.decide(identifier, req, 1))
    assert view['last_ai_status'] == 'EXPIRED' and view['full_game']['ballot']['sealed_count'] == 0
    assert not view['pending_ai'] and view['budget']['used_tokens'] > 0
    assert play.sdk.chat_completion.await_count == 0


@pytest.mark.parametrize('actor,owner,action', [('a', 1, 'CAST_BALLOT'), ('b', 2, 'CAST_BALLOT'),
                                             ('b', 1, 'BREAK_TIE'), ('b', 1, 'SEAL_FINALE')])
def test_full_play_ai_illegal_actor_stage_or_owner_never_calls(play, actor, owner, action):
    view = open_ballot(play)
    with pytest.raises(PackagePlayError):
        asyncio.run(play.play.decide(view['play_id'], decision(2, actor, action), owner))
    assert len(events(play)) == 2 and play.sdk.chat_completion.await_count == 0


def test_full_play_old_package_rejects_formal_model_commands(play):
    _, view = start(play, memory_package())
    with pytest.raises(PackagePlayError, match='UNSUPPORTED'):
        asyncio.run(play.play.decide(view['play_id'], decision(0), 1))
    assert not events(play) and play.sdk.chat_completion.await_count == 0


def test_full_play_ai_sees_only_its_heard_phone_messages(play):
    _, view = start(play, full_package()); identifier = view['play_id']
    play.play.act(identifier, action_body(0, 'advance', 'ADVANCE_PHASE'), 1)
    play.play.table(identifier, command(1, 'START_CALL', {'peer_character_id': 'b'}), 1)
    play.play.table(identifier, command(2, 'PRIVATE_SPEAK', {'text': '只告诉乙三角木片'}), 1)
    play.play.table(identifier, command(3, 'STOP_CALL'), 1)
    play.play.table(identifier, command(4, 'OPEN_BALLOT'), 1)
    contexts = []
    async def complete(messages, **params):
        contexts.append(json.loads(messages[1].content)['context'])
        return output({'kind': 'ABSTAIN', 'choice_id': None})
    play.sdk.chat_completion.side_effect = complete
    view = asyncio.run(play.play.decide(identifier, decision(5, 'b'), 1))
    view = asyncio.run(play.play.decide(identifier, decision(view['revision'], 'c'), 1))
    assert '只告诉乙三角木片' in canonical_json(contexts[0]) and 'PRIVATE_MEMORY_b' in canonical_json(contexts[0])
    assert '只告诉乙三角木片' not in canonical_json(contexts[1]) and 'PRIVATE_MEMORY_b' not in canonical_json(contexts[1])
    assert view['full_game']['ballot']['sealed_count'] == 2


def test_full_play_complete_persistent_five_seat_model_submissions_and_settlement(play):
    view = open_ballot(play); identifier = view['play_id']; calls = []
    async def complete(messages, **params):
        context = json.loads(messages[1].content)['context']; calls.append(context)
        assert not any(k in canonical_json(context) for k in ('SYSTEM_TRUTH', 'answer_key', 'group_id', 'total_points'))
        actor = context['character']['id']
        if context['action'] == 'SEAL_FINALE':
            assert all(q['id'] == f'{actor}-q' for q in context['questions'])
            return output(submission(actor))
        return output({'kind': 'SKIP', 'choice_id': None})
    play.sdk.chat_completion.side_effect = complete
    for round_no in (1, 2):
        view = play.play.table(identifier, command(view['revision'], 'CAST_BALLOT', {'kind': 'SKIP', 'choice_id': None}), 1)
        for actor in 'bcde':
            view = asyncio.run(play.play.decide(identifier, decision(view['revision'], actor), 1))
            assert view['last_ai_status'] == 'OK'
        assert view['can_advance']
        view = play.play.act(identifier, action_body(view['revision'], f'after-{round_no}', 'ADVANCE_PHASE'), 1)
        if round_no == 1:
            assert view['full_game']['phase_kind'] == 'READING'
            view = play.play.act(identifier, action_body(view['revision'], 'second-read', 'ADVANCE_PHASE'), 1)
            view = play.play.table(identifier, command(view['revision'], 'OPEN_BALLOT'), 1)
    assert view['full_game']['phase_kind'] == 'FINALE'
    view = play.play.table(identifier, command(view['revision'], 'SEAL_FINALE', submission('a')), 1)
    for actor in 'bcde':
        view = asyncio.run(play.play.decide(identifier, decision(view['revision'], actor, 'SEAL_FINALE'), 1))
        assert view['last_ai_status'] == 'OK' and view['full_game']['result'] is None
        assert view['full_game']['finale']['submission']['answers'][0]['question_id'] == 'a-q'
    assert view['full_game']['finale']['all_sealed']
    view = play.play.act(identifier, action_body(view['revision'], 'settle', 'SETTLE'), 1)
    play.db.commit()
    assert view['settled'] and [r['total_points'] for r in view['full_game']['result']['totals']] == [1, 2, 3, 4, 5]
    assert play.sdk.chat_completion.await_count == 12
    with play.factory() as db: assert service(play, db).get(identifier, 1) == view


@pytest.mark.parametrize('mutation', ['missing_question', 'foreign_option', 'self_trust', 'duplicate_option', 'extra_score'])
def test_full_play_finale_model_payload_is_locally_checked(mutation):
    from src.fusion.package_play_engine import play_engine
    from tests.fusion_security.test_package_full_play import advance, investigate
    engine = play_engine(full_package(), 'a'); advance(engine); investigate(engine); advance(engine); advance(engine); investigate(engine); advance(engine)
    context = engine.decision_context('b', 'SEAL_FINALE'); value = submission('b')
    if mutation == 'missing_question': value['answers'] = []
    if mutation == 'foreign_option': value['answers'][0]['option_ids'] = ['foreign']
    if mutation == 'self_trust': value['vote']['trust_character_id'] = 'b'
    if mutation == 'duplicate_option': value['answers'][0]['option_ids'] *= 2
    if mutation == 'extra_score': value['score'] = 100
    with pytest.raises(ValueError): validate_table_decision(value, context)


@pytest.mark.parametrize('operation,count', [('proposal', 131), ('dialogue', 161), ('finale', 600)])
def test_full_play_long_history_keeps_required_materials_and_can_prepare(play, operation, count):
    from src.fusion.package_play_engine import play_engine
    from tests.fusion_security.test_package_full_play import advance, investigate
    engine = play_engine(full_package(), 'a'); advance(engine)
    if operation == 'finale':
        investigate(engine); advance(engine); advance(engine); investigate(engine); advance(engine)
    state = Replay(engine, count + 50, '0' * 64, play.policy)
    state.discussion = [{'id': f'statement-{i}', 'sequence': i, 'speaker': 'a', 'kind': 'CLAIM',
        'phase_id': engine.state()['current_phase_id'], 'text': f'{i}号发言' + '只是待核对说法。' * 70} for i in range(1, count + 1)]
    binding = {'play_id': 'play-' + 'a' * 32, 'package_hash': 'b' * 64, 'model': play.model.metadata()}
    original = deepcopy(state.discussion)
    if operation == 'proposal':
        context = PackagePlayService._proposal_context(state, binding, 'b')
        prepared = play.play.full_proposal_model.prepare(context)
    elif operation == 'dialogue':
        context = PackagePlayService._dialogue_context(state, binding, 'b', 'statement-1')
        assert any(c['id'] == 'statement-1' for c in context['discussion'])
        prepared = play.play.full_dialogue_model.prepare(context)
    else:
        context = PackagePlayService._table_context(state, binding, 'b', 'SEAL_FINALE')
        assert context['questions'] == engine.decision_context('b', 'SEAL_FINALE')['questions']
        assert 'PRIVATE_BOOK_b' in canonical_json(context)
        prepared = play.play.table_model.prepare(context)
    assert 0 < len(context['discussion']) <= 61
    assert context['history_window']['omitted_count'] == count - len(context['discussion'])
    assert prepared['input_tokens'] <= 24000 + 4096
    assert state.discussion == original  # stored claims are never deleted
    assert play.sdk.chat_completion.await_count == 0


def test_full_play_oversized_required_material_is_rejected_before_budget_or_sdk(play):
    package = full_package(); package['knowledge'][1]['text'] = '必须完整保留的私人经历' * 3000
    _, view = start(play, package); identifier = view['play_id']
    play.play.act(identifier, action_body(0, 'advance', 'ADVANCE_PHASE'), 1)
    play.play.table(identifier, command(1, 'OPEN_BALLOT'), 1)
    with pytest.raises(PackagePlayError, match='REQUIRED_CONTEXT_TOO_LARGE'):
        asyncio.run(play.play.decide(identifier, decision(2), 1))
    assert play.sdk.chat_completion.await_count == 0 and len(events(play)) == 2


def test_full_play_no_acquired_answer_options_still_allows_explicit_uncertain(play):
    from src.fusion.package_play_engine import play_engine
    from src.fusion.package_validation import validate_package
    from tests.fusion_security.test_package_full_play import advance, investigate
    package=full_package()
    for q in package['full_play']['finale']['questions']:
        for option in q['options']:
            option['available_when']=[[{'collection':'evidence','id':'evidence-look-note'}]]
    assert validate_package(package)['valid']
    engine=play_engine(package,'a'); advance(engine); investigate(engine); advance(engine); advance(engine); investigate(engine); advance(engine)
    state=Replay(engine,50,'0'*64,play.policy)
    binding={'play_id':'play-'+'a'*32,'package_hash':'b'*64,'model':play.model.metadata()}
    context=PackagePlayService._table_context(state,binding,'b','SEAL_FINALE')
    assert context['questions'][0]['options']==[] and context['questions'][0]['max_choices']==0
    assert play.play.table_model.prepare(context)
    value=submission('b',[])
    assert validate_table_decision(value,context)==value
    with pytest.raises(ValueError): validate_table_decision(submission('b'),context)
    engine.table_apply('SEAL_FINALE','b',value,51)
    assert engine.state()['full_play']['finale']['sheets']['b']==value


def test_full_play_larger_input_budget_is_frozen_and_cost_reservation_still_precedes_sdk(play):
    package=full_package(); package['knowledge'][1]['text']='合法私人经历需要完整保留。'*1000
    _, view=start(play,package); identifier=view['play_id']
    row=play.play._row(identifier,1); _,binding=play.play._resolve(row)
    assert binding['schema_version']=='package-text-play-binding/1.2'
    assert binding['full_input']['max_input_bytes']==65536 and binding['model']['max_input_bytes']==24000
    play.play.act(identifier,action_body(0,'advance','ADVANCE_PHASE'),1)
    view=play.play.table(identifier,command(1,'OPEN_BALLOT'),1)
    assert view['table_decisions']['available']
    async def complete(messages,**params):
        from src.db.models.package_play import ScriptPackagePlayEvent
        assert not play.db.in_transaction()
        with play.factory() as db:
            stored=json.loads(db.query(ScriptPackagePlayEvent).order_by(ScriptPackagePlayEvent.revision.desc()).first().event_json)['data']
        assert stored['reservation']['prompt_tokens'] > 24000
        assert stored['model']['max_input_bytes']==65536
        assert stored['model']['max_output_tokens']==4096
        assert stored['reservation']['completion_tokens']==play.play.table_model.profile.reserved_completion_tokens(4096)
        assert params.get('max_tokens',params.get('max_completion_tokens'))==4096
        return output({'kind':'ABSTAIN','choice_id':None})
    play.sdk.chat_completion.side_effect=complete
    view=asyncio.run(play.play.decide(identifier,decision(2),1))
    assert view['last_ai_status']=='OK' and play.sdk.chat_completion.await_count==1
    with play.factory() as db:
        changed=PackagePlayService(db,play.publisher,play.model,play.policy,lambda:play.clock[0],full_input_bytes=32768)
        recovered=changed.get(identifier,1)
        assert recovered['revision']==view['revision'] and recovered['full_game']==view['full_game']
        assert recovered['table_decisions']['reason']=='CONFIG_CHANGED'
        with pytest.raises(PackagePlayError,match='AI_UNAVAILABLE'):
            asyncio.run(changed.decide(identifier,decision(4,'c'),1))
    assert play.sdk.chat_completion.await_count==1


@pytest.mark.parametrize('limit', [True, 63, 4097, 100.0, '4096'])
def test_full_play_output_policy_is_a_bounded_trusted_server_setting(play, limit):
    with pytest.raises(PackagePlayError, match='OUTPUT_POLICY_INVALID'):
        PackagePlayService(play.db, play.publisher, play.model, play.policy, full_table_output_tokens=limit)


def test_full_play_output_change_keeps_saved_result_and_blocks_new_decisions(play):
    view = open_ballot(play); identifier = view['play_id']
    play.sdk.chat_completion.return_value = output({'kind': 'ABSTAIN', 'choice_id': None})
    view = asyncio.run(play.play.decide(identifier, decision(2), 1))
    play.db.commit()
    with play.factory() as db:
        changed = PackagePlayService(db, play.publisher, play.model, play.policy, lambda: play.clock[0], full_table_output_tokens=2048)
        recovered = changed.get(identifier, 1)
        assert recovered['full_game'] == view['full_game'] and recovered['revision'] == view['revision']
        assert recovered['table_decisions']['reason'] == 'CONFIG_CHANGED'
        with pytest.raises(PackagePlayError, match='AI_UNAVAILABLE'):
            asyncio.run(changed.decide(identifier, decision(4, 'c'), 1))
    assert play.sdk.chat_completion.await_count == 1


def test_full_play_prior_input_only_binding_replays_its_own_output_budget(play):
    from sqlalchemy import update
    from src.db.models.package_play import ScriptPackagePlay
    from src.fusion.package_validation import content_hash
    # Simulate the prior explicit binding. Its base dialogue limit was also the
    # table limit; reading it must never reinterpret a 64-token reservation as 4096.
    _, view = start(play, full_package()); identifier = view['play_id']
    row = play.play._row(identifier, 1); binding = json.loads(row.binding_json)
    binding['schema_version'] = 'package-text-play-binding/1.1'; del binding['full_output']
    play.db.execute(update(ScriptPackagePlay).where(ScriptPackagePlay.id == row.id).values(
        binding_json=canonical_json(binding), binding_hash=content_hash(binding)))
    play.db.commit()
    old = PackagePlayService(play.db, play.publisher, play.model, play.policy, lambda: play.clock[0], full_table_output_tokens=64)
    old.act(identifier, action_body(0, 'advance', 'ADVANCE_PHASE'), 1)
    old.table(identifier, command(1, 'OPEN_BALLOT'), 1)
    play.sdk.chat_completion.return_value = output({'kind': 'ABSTAIN', 'choice_id': None})
    prior = asyncio.run(old.decide(identifier, decision(2), 1)); play.db.commit()
    assert prior['last_ai_status'] == 'OK'
    assert json.loads(events(play)[-2].event_json)['data']['model']['max_output_tokens'] == 64
    with play.factory() as db:
        recovered = service(play, db).get(identifier, 1)
    assert recovered['revision'] == prior['revision'] and recovered['full_game'] == prior['full_game']
    assert recovered['table_decisions']['reason'] == 'CONFIG_CHANGED'
