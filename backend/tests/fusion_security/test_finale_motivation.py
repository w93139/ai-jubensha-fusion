"""Short finale claims use synthetic sources and the real one-shot SDK adapter."""
import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import json

import pytest

from src.fusion.package_dialogue_model import FINALE_MOTIVATION_POLICY, dialogue_metadata, validate_finale_motivation
from src.fusion.package_guided_flow import GuidedFlowContent
from src.fusion.package_play import PackagePlayService, PackagePlayError
from src.fusion.package_validation import canonical_json, content_hash
from tests.fusion_security.test_package_play_store import play, events, action_body
from tests.fusion_security.test_package_runtime import runtime, publish, request
from tests.fusion_security.test_package_full_play import full_package
from tests.fusion_security.test_package_guided_flow import content, guided
from tests.fusion_security.test_full_play_store import command
from tests.fusion_security.test_full_play_decisions import output, decision
from tests.fusion_security.test_structured_finale import submission


def service(play, db=None, enabled=True):
    return PackagePlayService(play.db if db is None else db, play.publisher, play.model, play.policy,
        lambda: play.clock[0], guided_content=getattr(play, 'finale_guides', {}),
        finale_policy=getattr(play, 'test_finale_policy', FINALE_MOTIVATION_POLICY) if enabled else None)


def enter_finale(play, human='a', enabled=True):
    p = full_package()
    doc = content(p); doc['selected_character_id'] = human
    play.finale_guides = {content_hash(p): {human: GuidedFlowContent(p, doc)}}
    play.play = service(play, enabled=enabled)
    publish(play, p)
    opening = play.service.create(request(character=human), 1); play.db.commit()
    view = play.play.create({'opening_session_id': opening['session_id'], 'idempotency_key': 'start-finale'}, 1)
    while view['full_game']['phase_kind'] != 'FINALE':
        if view['full_game']['phase_kind'] == 'READING':
            view = play.play.act(view['play_id'], action_body(view['revision'], f'advance-{view["revision"]}', 'ADVANCE_PHASE'), 1)
        else:
            while view['mechanics']['available_actions']:
                view = play.play.guided(view['play_id'], guided(view, 'INVESTIGATE',
                    {'action_id': view['mechanics']['available_actions'][0]['id']}), 1)
            view = play.play.guided(view['play_id'], guided(view, 'FINISH_INVESTIGATION'), 1)
    play.db.commit()
    return p, opening, view


def answer(text='我怀疑 a，已公开的纸条仍有疑点。'):
    return {'text': text, 'basis': [{'collection': 'evidence', 'id': 'evidence-look-note'}]}


@pytest.mark.parametrize('human', list('abcde'))
def test_finale_four_ai_once_refresh_and_public_only_context(play, human):
    _, opening, view = enter_finale(play, human)
    assert view['finale_speeches'] == [{'character_id': a, 'text': ''} for a in 'abcde' if a != human]
    assert not view['finale_motivation']['complete'] and not view['table_decisions']['available']
    assert play.sdk.chat_completion.await_count == 0
    identifier = view['play_id']; row = play.play._row(identifier, 1)
    package, binding = play.play._resolve(row)
    before = deepcopy(play.play._replay(row, package, binding).engine.state())
    with pytest.raises(PackagePlayError, match='FINALE_MOTIVATIONS_PENDING'):
        play.play.table(identifier, command(view['revision'], 'SEAL_FINALE', submission(human)), 1)
    async def complete(messages, **params):
        assert not play.db.in_transaction()
        with play.factory() as db:
            pending = service(play, db).get(identifier, 1)
            assert pending['pending_ai']
        context = json.loads(messages[1].content)['context']
        assert context['character']['id'] != human
        for secret in ('PRIVATE_BOOK_', 'PRIVATE_MEMORY_', 'SYSTEM_TRUTH', 'answer_key', 'group_id', 'questions', 'accusation_options'):
            assert secret not in canonical_json(context)
        assert {m['id'] for m in context['materials']} == {'evidence-find-key', 'evidence-open-box', 'evidence-look-note'}
        assert params['max_tokens'] == 192
        return output(answer())
    play.sdk.chat_completion.side_effect = complete
    view = asyncio.run(play.play.complete_finale_motivations(identifier, 1))
    assert view['finale_motivation']['complete'] and view['finale_motivation']['completed_count'] == 4
    assert all(e['text'] == answer()['text'] for e in view['finale_speeches'])
    assert play.sdk.chat_completion.await_count == 4 and not view['pending_ai']
    assert play.play._replay(row, package, binding).engine.state() == before
    saved = [(e.event_json, e.state_hash) for e in events(play)]
    assert asyncio.run(play.play.complete_finale_motivations(identifier, 1)) == view
    with play.factory() as db:
        assert service(play, db).get(identifier, 1) == view
    assert play.service.get(opening['session_id'], 1) == opening
    assert [(e.event_json, e.state_hash) for e in events(play)] == saved
    assert play.sdk.chat_completion.await_count == 4


@pytest.mark.parametrize('failure', ['transport', 'invalid', 'long', 'certainty', 'foreign', 'unknown-usage'])
def test_finale_model_failure_is_persisted_empty_and_does_not_block_sealing(play, failure):
    _, _, view = enter_finale(play)
    if failure == 'transport': play.sdk.chat_completion.side_effect = RuntimeError('private provider error')
    else:
        value = answer()
        if failure == 'invalid': value = {'refs': []}
        if failure == 'long': value['text'] = '我怀疑' + '甲' * 61
        if failure == 'certainty': value['text'] = '我怀疑 a，但我确定他就是凶手。'
        if failure == 'foreign': value['basis'][0]['id'] = 'unacquired-evidence'
        reply = output(value)
        if failure == 'unknown-usage': reply.usage = None
        play.sdk.chat_completion.return_value = reply
    view = asyncio.run(play.play.complete_finale_motivations(view['play_id'], 1))
    assert view['finale_motivation']['complete'] and not view['pending_ai']
    assert len(view['finale_speeches']) == 4 and all(e['text'] == '' for e in view['finale_speeches'])
    assert view['budget']['used_tokens'] > 0 and play.sdk.chat_completion.await_count == 4
    view = play.play.table(view['play_id'], command(view['revision'], 'SEAL_FINALE', submission('a')), 1)
    assert view['full_game']['finale']['sealed']
    assert 'vote_disclosure' not in view['full_game']['finale']
    play.db.commit()
    with play.factory() as db: assert service(play, db).get(view['play_id'], 1) == view
    assert asyncio.run(play.play.complete_finale_motivations(view['play_id'], 1)) == view
    assert play.sdk.chat_completion.await_count == 4


@pytest.mark.parametrize('reason', ['disabled', 'budget', 'unavailable'])
def test_finale_unavailable_is_free_persisted_empty_not_retried(play, reason):
    if reason == 'disabled': play.policy = replace(play.policy, paid_calls_enabled=False)
    if reason == 'budget': play.policy = replace(play.policy, cost_limit_cny=Decimal('0.00001'))
    _, _, view = enter_finale(play)
    if reason == 'unavailable': play.play.finale_model.available = False
    view = asyncio.run(play.play.complete_finale_motivations(view['play_id'], 1))
    assert view['finale_motivation']['complete'] and all(e['text'] == '' for e in view['finale_speeches'])
    assert view['budget']['used_tokens'] == 0 and play.sdk.chat_completion.await_count == 0
    assert asyncio.run(play.play.complete_finale_motivations(view['play_id'], 1)) == view


def test_finale_old_binding_read_and_continuation_do_not_opt_in(play):
    _, _, view = enter_finale(play, enabled=False)
    before = [(e.event_json, e.state_hash) for e in events(play)]
    assert 'finale_speeches' not in view and 'finale_motivation' not in view
    assert asyncio.run(service(play).complete_finale_motivations(view['play_id'], 1)) == view
    assert [(e.event_json, e.state_hash) for e in events(play)] == before
    assert play.sdk.chat_completion.await_count == 0


def test_finale_pending_recovery_expires_once_then_continues_other_seats(play):
    _, _, view = enter_finale(play); identifier = view['play_id']
    key, prepared = play.play._begin_finale_motivation(identifier, 'b', 1)
    assert prepared and not play.db.in_transaction()
    pending = play.play.get(identifier, 1)
    assert asyncio.run(play.play.complete_finale_motivations(identifier, 1)) == pending
    assert play.sdk.chat_completion.await_count == 0
    play.clock[0] += 301
    play.sdk.chat_completion.return_value = output(answer())
    view = asyncio.run(service(play).complete_finale_motivations(identifier, 1))
    assert view['finale_motivation']['complete'] and view['finale_speeches'][0]['text'] == ''
    assert play.sdk.chat_completion.await_count == 3 and not view['pending_ai']
    assert play.play._finish(identifier, key, 1, {'status': 'OK', 'motivation': answer()}) == view


def test_finale_all_sealed_discloses_actual_votes_without_scoring_or_extra_calls(play):
    _, _, view = enter_finale(play)
    play.sdk.chat_completion.return_value = output(answer())
    view = asyncio.run(play.play.complete_finale_motivations(view['play_id'], 1))
    speeches = deepcopy(view['finale_speeches'])
    view = play.play.table(view['play_id'], command(view['revision'], 'SEAL_FINALE', submission('a')), 1)
    for actor in 'bcde':
        play.sdk.chat_completion.return_value = output(submission(actor))
        view = asyncio.run(play.play.decide(view['play_id'], decision(view['revision'], actor, 'SEAL_FINALE'), 1))
        if actor != 'e': assert 'vote_disclosure' not in view['full_game']['finale']
    assert view['full_game']['result'] is None
    rows = deepcopy(view['full_game']['finale']['vote_disclosure'])
    assert len(rows) == 5 and {r['character_id'] for r in rows} == set('abcde')
    assert rows[0]['motivation'] == ''
    for row in rows:
        assert row['voted_for'] == submission(row['character_id'])['vote']['accusation_id']
        assert set(row) == {'character_id', 'voted_for', 'voted_for_label', 'motivation'}
        if row['character_id'] != 'a': assert row['motivation'] == answer()['text']
    row = play.play._row(view['play_id'], 1); package, binding = play.play._resolve(row)
    engine = play.play._replay(row, package, binding).engine
    plain, with_speeches = engine._finale.result(), engine._finale.result(speeches)
    assert {k: v for k, v in plain.items() if k != 'vote_disclosure'} == {k: v for k, v in with_speeches.items() if k != 'vote_disclosure'}
    view = play.play.act(view['play_id'], action_body(view['revision'], 'settle', 'SETTLE'), 1)
    assert view['full_game']['result']['vote_disclosure'] == rows and play.sdk.chat_completion.await_count == 8
    assert view['finale_speeches'] == speeches


def test_finale_prompt_is_not_a_legacy_dialogue_contract(play):
    with pytest.raises(ValueError, match='DIALOGUE_MODEL_VERSION_INVALID'):
        dialogue_metadata(play.model.metadata(), FINALE_MOTIVATION_POLICY)


@pytest.mark.parametrize('text', ['我确定是甲。', '我怀疑甲。还有乙。', '我怀疑甲\n因为线索。', '我怀疑甲\u200b。', ' 我怀疑甲。'])
def test_finale_invalid_one_sentence_format_is_rejected(text):
    with pytest.raises(ValueError):
        validate_finale_motivation({'text': text, 'basis': [{'collection': 'evidence', 'id': 'e'}]},
            {'materials': [{'collection': 'evidence', 'id': 'e'}], 'discussion': []})


def test_finale_cancelled_dispatch_is_terminal_and_only_other_seats_resume(play):
    _, _, view = enter_finale(play)
    play.sdk.chat_completion.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(play.play.complete_finale_motivations(view['play_id'], 1))
    saved = service(play).get(view['play_id'], 1)
    assert not saved['pending_ai'] and saved['finale_motivation']['completed_count'] == 1
    assert saved['finale_speeches'][0]['text'] == ''
    play.sdk.chat_completion.side_effect = None
    play.sdk.chat_completion.return_value = output(answer())
    done = asyncio.run(service(play).complete_finale_motivations(view['play_id'], 1))
    assert done['finale_motivation']['complete'] and play.sdk.chat_completion.await_count == 4
    assert done['finale_speeches'][0]['text'] == ''


def test_finale_changed_live_model_does_not_change_replay_or_retry(play):
    _, _, view = enter_finale(play)
    play.sdk.chat_completion.return_value = output(answer())
    done = asyncio.run(play.play.complete_finale_motivations(view['play_id'], 1))
    changed = service(play)
    changed.finale_model.settings = replace(changed.finale_model.settings, max_input_bytes=8192)
    assert changed.get(view['play_id'], 1) == done
    assert asyncio.run(changed.complete_finale_motivations(view['play_id'], 1)) == done
    assert play.sdk.chat_completion.await_count == 4


def test_finale_other_owner_cannot_read_or_dispatch(play):
    _, _, view = enter_finale(play)
    before = [(e.event_json, e.state_hash) for e in events(play)]
    with pytest.raises(PackagePlayError, match='NOT_FOUND'):
        asyncio.run(play.play.complete_finale_motivations(view['play_id'], 2))
    assert [(e.event_json, e.state_hash) for e in events(play)] == before
    assert play.sdk.chat_completion.await_count == 0


def test_finale_overlapping_request_observes_reservation_not_second_dispatch(play):
    _, _, view = enter_finale(play)
    async def sdk(messages, **params):
        with play.factory() as db:
            other = service(play, db)
            current = await other.complete_finale_motivations(view['play_id'], 1)
            assert current['pending_ai']
        return output(answer())
    play.sdk.chat_completion.side_effect = sdk
    done = asyncio.run(play.play.complete_finale_motivations(view['play_id'], 1))
    assert done['finale_motivation']['complete'] and play.sdk.chat_completion.await_count == 4


def test_finale_tampered_result_fails_replay_without_model(play):
    _, _, view = enter_finale(play)
    play.sdk.chat_completion.return_value = output(answer())
    asyncio.run(play.play.complete_finale_motivations(view['play_id'], 1))
    event = events(play)[-1]
    raw = json.loads(event.event_json)
    # Even an altered valid-looking short text must not survive the event hash.
    def alter(value):
        if isinstance(value, dict):
            if 'motivation' in value and isinstance(value['motivation'], dict):
                value['motivation']['text'] = '我怀疑另一位虚构角色。'; return True
            return any(alter(v) for v in value.values())
        return False
    assert alter(raw)
    from sqlalchemy import text
    play.db.execute(text('UPDATE script_package_play_events SET event_json=:value WHERE id=:id'),
        {'value':canonical_json(raw), 'id':event.id})
    play.db.commit()
    with pytest.raises(PackagePlayError): service(play).get(view['play_id'], 1)
    assert play.sdk.chat_completion.await_count == 4


def test_finale_disclosure_keeps_offseat_identity_abstention_and_prior_text():
    from tests.fusion_security.test_structured_finale import engine
    game = engine()
    with pytest.raises(ValueError): game.vote_disclosure([])
    for actor in 'abcde':
        game.seal(actor, submission(actor, accusation='visitor' if actor == 'b' else None))
    prior = [{'character_id':'b', 'text':'我怀疑 a，纸条仍有疑点。'}]
    rows = game.result(prior)['vote_disclosure']
    assert rows[1] == {'character_id':'b', 'voted_for':'visitor', 'voted_for_label':'访客', 'motivation':prior[0]['text']}
    assert rows[0]['voted_for'] is None and rows[0]['voted_for_label'] == '弃权'
    assert 'hidden-group' not in canonical_json(rows)


def test_finale_reserved_event_slots_do_not_spend_ordinary_finale_slots(play):
    from contextlib import nullcontext
    from unittest.mock import Mock
    from src.fusion.finale_motivation import COMMAND
    _, _, view = enter_finale(play)
    row = play.play._row(view['play_id'], 1); package, binding = play.play._resolve(row)
    state = play.play._replay(row, package, binding)
    # 1999 ordinary events plus the separately reserved START.
    state.revision = 2000
    instance = service(play); instance.db = Mock(); instance.db.begin_nested.side_effect = lambda: nullcontext()
    for actor in 'bcde':
        instance._append(row, binding, state, 'ACTION', {'schema_version':COMMAND,
            'expected_revision':state.revision, 'idempotency_key':instance._finale_key(binding, actor),
            'character_id':actor, 'action':'SKIP'}, {'reason':'BUDGET_EXCEEDED'})
    assert not any(e['status'] == 'READY' for e in state.finale_speeches)
    assert state.revision == 2004 and instance._finale_event_count(state) == 5
    # The original last ordinary slot remains available, then stays bounded.
    instance._consume = Mock()
    instance._append(row, binding, state, 'ACTION', {'idempotency_key':'last-ordinary'}, {})
    with pytest.raises(PackagePlayError, match='EVENT_LIMIT'):
        instance._append(row, binding, state, 'ACTION', {'idempotency_key':'extra-ordinary'}, {})


def test_opt_in_replay_cannot_use_motivation_slots_for_ordinary_events(play, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import src.fusion.package_play as module
    _, _, view = enter_finale(play)
    row = play.play._row(view['play_id'], 1); package, binding = play.play._resolve(row)
    engine = module.play_engine(package, row.selected_character_id, binding['rules_contract'])
    state = module.Replay(engine, 0, row.binding_hash, play.play._budget(binding['budget']))
    digest = content_hash(state.state()); assert digest == binding['initial_state_hash']
    previous = row.binding_hash; forged = []
    # Rehashed events do not acquire the exclusive nine slots just by using a
    # new binding. Other domain checks are isolated to reach this specific fence.
    for revision in range(1, 2002):
        request = {'idempotency_key':str(revision)}
        payload = {'schema_version':play.play._event_contract(state), 'play_id':row.play_id,
            'revision':revision,'kind':'ACTION','request_hash':content_hash(request),
            'previous_event_hash':previous, 'state_hash':digest,'data':{}}
        event = SimpleNamespace(revision=revision, previous_event_hash=previous, request_json=canonical_json(request),
            request_hash=content_hash(request), event_json=canonical_json(payload), event_hash=content_hash(payload),
            idempotency_key=play.play._key('ACTION', request), kind='ACTION', state_hash=digest)
        forged.append(event); previous=event.event_hash
    instance=service(play); instance.db=Mock()
    instance.db.query.return_value.filter_by.return_value.order_by.return_value.populate_existing.return_value.yield_per.return_value=forged
    instance._consume=Mock()
    monkeypatch.setattr(module, 'capture_workspace', lambda _: None)
    with pytest.raises(PackagePlayError, match='HISTORY_INVALID'):
        instance._replay(row, package, binding)
    assert instance._consume.call_count == 2001


@pytest.mark.parametrize('bad', [False, True])
def test_grounded_finale_uses_only_acquired_origins_and_rejects_moved_evidence(play, bad):
    play.test_finale_policy = 'finale-motivation/1.1'
    _, _, view = enter_finale(play)
    async def sdk(messages, **params):
        context = json.loads(messages[1].content)['context']
        assert context['schema_version'] == 'finale-motivation-context/1.1'
        assert {o['id'] for o in context['evidence_origins']} == {m['id'] for m in context['materials'] if m['collection']=='evidence'}
        origins = {o['id']: o['labels'] for o in context['evidence_origins']}
        assert all(origins.values())
        other = next(label for mid,labels in origins.items() if mid != 'evidence-look-note' for label in labels)
        value = answer('我怀疑 a，' + (other if bad else origins['evidence-look-note'][0]) + '的纸条仍有疑点。')
        return output(value)
    play.sdk.chat_completion.side_effect = sdk
    done = asyncio.run(play.play.complete_finale_motivations(view['play_id'],1))
    assert done['finale_motivation']['policy']=='finale-motivation/1.1'
    assert done['finale_motivation']['complete']
    assert all(bool(s['text']) is not bad for s in done['finale_speeches'])
    assert service(play).get(view['play_id'],1)==done
    assert play.sdk.chat_completion.await_count==4


def test_new_finale_policy_preserves_recorded_old_speeches_and_hashes(play):
    _, _, view=enter_finale(play)
    play.sdk.chat_completion.return_value=output(answer())
    old=asyncio.run(play.play.complete_finale_motivations(view['play_id'],1))
    before=[(e.event_hash,e.state_hash) for e in events(play)]
    play.test_finale_policy='finale-motivation/1.1'
    newer=service(play)
    assert newer.get(view['play_id'],1)==old
    assert asyncio.run(newer.complete_finale_motivations(view['play_id'],1))==old
    assert [(e.event_hash,e.state_hash) for e in events(play)]==before
    assert play.sdk.chat_completion.await_count==4


def test_grounded_finale_preserves_other_speakers_experience_attribution():
    context={'schema_version':'finale-motivation-context/1.1','character':{'id':'a'},
        'materials':[], 'evidence_origins':[],
        'discussion':[{'id':'claim-1','speaker':'b','text':'我说我见过那个旅客。'}]}
    raw={'text':'我怀疑 b，他见过那个旅客。','basis':[{'collection':'discussion','id':'claim-1'}]}
    with pytest.raises(ValueError,match='ATTRIBUTION_REQUIRED'): validate_finale_motivation(raw,context)
    raw['text']='我怀疑 b，他说见过那个旅客。'
    assert validate_finale_motivation(raw,context)==raw
