"""Task scope narrows one turn after permission checks, never old records."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from src.fusion.package_dialogue_model import (
    ScopedTaskDialogueContext, ScopedTaskPackageDialogueModel, scope_retelling_context,
    dialogue_context_window, validate_speech,
)
from src.fusion.package_play import PackagePlayService
from src.fusion.package_role_model import PackageRoleModelError
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_play_store import play
from tests.fusion_security import test_required_retelling as old

VERSION = 'package-dialogue-model/1.13'


def scoped(play, db=None):
    return PackagePlayService(db if db is not None else play.db, play.publisher, play.model,
        play.policy, lambda: play.clock[0], speech_policy='role-speech/1.10')


def original(play):
    v = old.begin(play); return old.state_context(play, v)[1]


def test_explicit_task_scope_keeps_complete_target_and_original_question_only(play):
    c = original(play); before = deepcopy(c); selected = scope_retelling_context(c)
    target = c['response_task']['target']['id']
    assert selected['materials'] == [m for m in c['materials'] if m['collection']=='memory' and m['id']==target]
    assert selected['strategy_materials'] == []
    assert selected['discussion'] == [d for d in c['discussion'] if d['id']==c['reply_to']]
    assert selected['material_scope'] == 'TURN_TASK_ONLY' and c == before
    assert selected['response_task'] == c['response_task']
    assert scope_retelling_context(selected) == selected


@pytest.mark.parametrize('private', [False, True])
def test_normal_and_private_turns_retain_all_authorized_materials_and_strategy(play, private):
    c = original(play); c.pop('response_task'); c['channel'] = 'PRIVATE' if private else 'PUBLIC'
    selected = scope_retelling_context(c)
    assert selected['material_scope'] == 'ALL_AUTHORIZED'
    for field in ('materials','strategy_materials','discussion','reply_to'): assert selected[field] == c[field]


@pytest.mark.parametrize('bad', ['may','absent','private','extra_source','strategy','extra_claim','wrong_scope'])
def test_scope_cannot_bypass_permission_or_expand_the_assigned_turn(play, bad):
    c = original(play)
    if bad in ('may','absent','private'):
        if bad == 'may': next(m for m in c['materials'] if m['id']=='memory-b')['retelling'] = 'MAY_RETELL'
        elif bad == 'absent': c['response_task']['target']['id'] = 'future-memory'
        else: c['channel'] = 'PRIVATE'
        with pytest.raises(ValueError): scope_retelling_context(c)
    else:
        selected = scope_retelling_context(c)
        if bad == 'extra_source': selected['materials'].append(next(m for m in c['materials'] if m['id']=='memory-b-second'))
        elif bad == 'strategy': selected['strategy_materials'] = c['strategy_materials']
        elif bad == 'extra_claim': selected['discussion'].append(dict(id='extra',sequence=selected['revision'],speaker='a',text='其他话题。',kind='CLAIM'))
        else: selected['material_scope'] = 'ALL_AUTHORIZED'
        with pytest.raises(ValueError): ScopedTaskDialogueContext.model_validate(selected)


def test_prior_full_text_stays_in_audit_only_and_unrelated_basis_cannot_be_selected(play):
    c = original(play)
    c['response_task']['prior_attempts'] = [dict(request_sequence=1,response_id='old-reply',result_status='OK',status='ATTEMPTED_UNVERIFIED',text='与这条回忆无关的旧尾段。')]
    c = scope_retelling_context(c)
    model = ScopedTaskPackageDialogueModel(play.sdk, play.play.full_dialogue_model.settings)
    prepared = model.prepare(c); wire = json.loads(prepared['messages'][1]['content'])['context']
    assert prepared['source_context']['response_task']['prior_attempts'][0]['text'] == '与这条回忆无关的旧尾段。'
    assert wire['response_task']['prior_attempts'] == [dict(request_sequence=1,result_status='OK',status='ATTEMPTED_UNVERIFIED')]
    assert '与这条回忆无关的旧尾段。' not in prepared['messages'][1]['content']
    assert wire['response_task']['target_passages'] == wire['materials'][0]['passages']
    bad = old.assigned_reply(c); bad['segments'][0]['basis'][0]['id'] = 'memory-b-second'
    with pytest.raises(ValueError): validate_speech(bad, c, VERSION)
    changed = deepcopy(prepared); changed['source_context']['response_task']['prior_attempts'][0]['text'] = '替换过的旧话。'
    assert asyncio.run(model.call(changed))['model_attempted'] is False
    assert play.sdk.chat_completion.await_count == 0


def test_exact_scope_and_prior_projection_measured_without_dropping_any_target_text(play):
    c = scope_retelling_context(original(play)); before = deepcopy(c)
    model = ScopedTaskPackageDialogueModel(play.sdk, play.play.full_dialogue_model.settings)
    size = model.prepare(c)['input_tokens'] - 4096
    assert dialogue_context_window(c, size, VERSION) == c
    with pytest.raises(PackageRoleModelError, match='REQUIRED_CONTEXT_TOO_LARGE'):
        dialogue_context_window(c, size-1, VERSION)
    assert c == before


def test_task_status_rotation_and_full_scope_restore_after_two_attempts_each(play):
    with patch.object(old, 'modern', scoped), patch.object(old, 'VERSION', VERSION):
        v = old.begin(play); seen = []
        async def complete(messages, **params):
            c = json.loads(messages[1].content)['context']; seen.append(c)
            return old.output(old.assigned_reply(c) if c.get('response_task') else old.grounded(c))
        play.sdk.chat_completion.side_effect = complete
        for n in range(5):
            req = {**old.reply(v),'idempotency_key':f'scoped-{n}'}
            v = asyncio.run(play.play.respond(v['play_id'],req,1))
            assert v['last_ai_status'] == 'OK'
            assert asyncio.run(play.play.respond(v['play_id'],req,1)) == v
            with play.factory() as db: assert scoped(play,db).get(v['play_id'],1) == v
        assert [c['material_scope'] for c in seen] == ['TURN_TASK_ONLY']*4+['ALL_AUTHORIZED']
        assert len(seen[-1]['materials']) > 1 and seen[-1]['strategy_materials']
        assert 'text' not in seen[2]['response_task']['prior_attempts'][0]
        state, _ = old.state_context(play,v)
        records = [a for a in state.retelling_attempts if a.get('attempt_kind')=='ASSIGNED_PUBLIC_TASK']
        assert len(records)==4 and all(a['text'] and a['status']=='ATTEMPTED_UNVERIFIED' for a in records)
        assert play.sdk.chat_completion.await_count == 5


def test_meta_refusal_before_projection_has_no_task_and_zero_sdk(play):
    with patch.object(old, 'modern', scoped), patch.object(old, 'VERSION', VERSION):
        old.test_meta_question_and_not_yet_unlocked_memory_create_no_assignment_and_zero_sdk(play)


@pytest.mark.parametrize('task', [True, False])
def test_long_actual_history_is_windowed_after_scope_without_rejecting_full_game(play, task):
    c = original(play); c['revision'] = 220
    if not task: c.pop('response_task')
    c['discussion'] = [dict(id=f'claim-{n}',sequence=n,speaker='a',text='真实发生的公开发言。',kind='CLAIM') for n in range(1,221)]
    c['reply_to'] = 'claim-220'; c['history_window']['omitted_count'] = 0
    selected = scope_retelling_context(c)
    assert scope_retelling_context(selected) == selected
    window = dialogue_context_window(selected, 65536, VERSION)
    model = ScopedTaskPackageDialogueModel(play.sdk, play.play.full_dialogue_model.settings)
    assert model.prepare(window)['input_tokens']-4096 <= 65536
    assert len(window['discussion']) == (1 if task else 60)
    assert window['history_window']['omitted_count'] == (219 if task else 160)
    assert window['discussion'][-1] == c['discussion'][-1]
    if not task: assert window['materials'] == c['materials']
