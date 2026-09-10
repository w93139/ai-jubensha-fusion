"""Versioned finite citation checks; fictional sources and offline SDK only."""
import asyncio
from copy import deepcopy
import json

import pytest

from src.fusion.package_guided_flow import digest
from src.fusion.package_play import PackagePlayService
from src.fusion.package_play_rules import PlayRulesError
from src.fusion.package_single_player import SinglePlayerContent
from src.fusion.package_validation import canonical_json, content_hash
from src.fusion.topic_answer_checks import validate_contract, freeze_contract, validate_topic_answer
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_play_store import play, start, events
from tests.fusion_security.test_package_full_play import full_package
from tests.fusion_security.test_single_player_topics import document, ask_topic, fallback
from tests.fusion_security.test_package_guided_flow import enter
from tests.fusion_security.test_full_play_store import command
from tests.fusion_security.test_full_play_decisions import output


VERSION = 'topic-answer-check/1.1'
SOURCE = ('旅店门前有一盏旧灯，门旁放着一只空木箱；这些都是用来核对的虚构资料。\n\n'
          '钟楼的门牌上写着蓝色标记，附近石阶暂时没有发现别的物品；仍须继续调查。')


def ref(identifier='initial-b', passages=None, collection='knowledge'):
    return {'collection': collection, 'id': identifier, 'passage_ids': passages or ['p0002']}


def contract():
    return {'schema_version': VERSION, 'conditional_basis': [
        {'when_any': ['钟楼'], 'requires': [ref()]}]}


def materials():
    return {('knowledge', 'initial-b'): {'text': SOURCE}}


def speech(text='我提到钟楼，只作线索核对。', refs=None):
    return {'segments': [{'mode': 'REPORT', 'text': text, 'basis': [ref()] if refs is None else refs}]}


@pytest.mark.parametrize('mutation', [
    'legacy_version', 'unknown_version', 'rules_not_list', 'too_many_rules', 'extra_rule_key',
    'missing_when', 'empty_when', 'too_many_when', 'empty_term', 'punctuation_term', 'long_term',
    'duplicate_normalized_term', 'empty_requires', 'too_many_requires', 'wrong_collection',
    'missing_material', 'extra_ref_key', 'empty_passages', 'too_many_passages',
    'duplicate_passage', 'unknown_passage',
])
def test_conditional_basis_rejects_invalid_authored_directory(mutation):
    c = contract(); rule = c['conditional_basis'][0]; basis = rule['requires'][0]
    if mutation == 'legacy_version': c['schema_version'] = 'topic-answer-check/1.0'
    elif mutation == 'unknown_version': c['schema_version'] = 'topic-answer-check/99'
    elif mutation == 'rules_not_list': c['conditional_basis'] = {}
    elif mutation == 'too_many_rules': c['conditional_basis'] *= 17
    elif mutation == 'extra_rule_key': rule['extra'] = True
    elif mutation == 'missing_when': del rule['when_any']
    elif mutation == 'empty_when': rule['when_any'] = []
    elif mutation == 'too_many_when': rule['when_any'] = [str(n) for n in range(17)]
    elif mutation == 'empty_term': rule['when_any'] = ['']
    elif mutation == 'punctuation_term': rule['when_any'] = ['！？']
    elif mutation == 'long_term': rule['when_any'] = ['楼' * 81]
    elif mutation == 'duplicate_normalized_term': rule['when_any'] = ['钟楼', '钟，楼']
    elif mutation == 'empty_requires': rule['requires'] = []
    elif mutation == 'too_many_requires': rule['requires'] *= 4
    elif mutation == 'wrong_collection': basis['collection'] = 'discussion'
    elif mutation == 'missing_material': basis['id'] = 'foreign-book'
    elif mutation == 'extra_ref_key': basis['text'] = 'untrusted'
    elif mutation == 'empty_passages': basis['passage_ids'] = []
    elif mutation == 'too_many_passages': basis['passage_ids'] = [f'p{i:04}' for i in range(1, 8)]
    elif mutation == 'duplicate_passage': basis['passage_ids'] *= 2
    elif mutation == 'unknown_passage': basis['passage_ids'] = ['p9999']
    with pytest.raises(ValueError):
        validate_contract(c, materials(), {'initial'})


def test_conditional_basis_valid_directory_and_freeze_are_independent():
    c = contract(); c['required_terms'] = {'initial': [['钟楼']]}
    validate_contract(c, materials(), {'initial'})
    frozen = freeze_contract(c, 'initial', [])
    assert frozen['required_terms'] == [['钟楼']]
    c['conditional_basis'][0]['requires'][0]['passage_ids'] = ['p0001']
    assert frozen['conditional_basis'][0]['requires'][0]['passage_ids'] == ['p0002']


@pytest.mark.parametrize('refs', [[], [ref(passages=['p0001'])],
    [ref(identifier='foreign-book')], [{'collection': 'knowledge', 'id': 'initial-b'}]])
def test_conditional_basis_missing_or_wrong_same_segment_citation_rejected(refs):
    with pytest.raises(PlayRulesError, match='SINGLE_TOPIC_BASIS_INCOMPLETE'):
        validate_topic_answer(speech(refs=refs), {'answer_contract': contract()})


def test_conditional_basis_other_segment_cannot_cover_trigger():
    value = speech(refs=[ref(passages=['p0001'])])
    value['segments'] += speech('另一个问题稍后再说。')['segments']
    with pytest.raises(PlayRulesError, match='SINGLE_TOPIC_BASIS_INCOMPLETE'):
        validate_topic_answer(value, {'answer_contract': contract()})


def test_conditional_basis_normalization_all_required_refs_and_no_trigger():
    c = contract()
    validate_topic_answer(speech(), {'answer_contract': c})
    validate_topic_answer(speech('暂时只能核对旅店。', [ref(passages=['p0001'])]), {'answer_contract': c})
    with pytest.raises(PlayRulesError, match='BASIS_INCOMPLETE'):
        validate_topic_answer(speech('钟，楼的情况还得查。', [ref(passages=['p0001'])]), {'answer_contract': c})
    c['conditional_basis'][0]['requires'] = [ref(passages=['p0001', 'p0002']), ref('second-book')]
    with pytest.raises(PlayRulesError, match='BASIS_INCOMPLETE'):
        validate_topic_answer(speech(refs=[ref(passages=['p0001', 'p0002'])]), {'answer_contract': c})
    validate_topic_answer(speech(refs=[ref(passages=['p0001', 'p0002']), ref('second-book')]), {'answer_contract': c})


def configured_service(play, db=None):
    return PackagePlayService(db if db is not None else play.db, play.publisher, play.model,
        play.policy, lambda: play.clock[0], speech_policy='role-speech/1.10')


def begin(play, channel, version=VERSION):
    play.play = configured_service(play)
    p = full_package(); p['knowledge'][1]['text'] = SOURCE; p['knowledge'][1]['retelling'] = 'MAY_RETELL'
    doc = document(p)
    doc['topics'][0]['responders'][0]['basis'][0]['text_sha256'] = digest(SOURCE)
    doc['topics'][0]['responders'][0]['answer_contract'] = contract() if version == VERSION else {'schema_version': version}
    play.play.single_player_content = {content_hash(p): SinglePlayerContent(p, doc)}
    _, view = start(play, p); view = enter(play, view)
    if channel == 'PRIVATE':
        view = play.play.table(view['play_id'], command(view['revision'], 'START_CALL', {'peer_character_id': 'b'}), 1)
    return p, doc, view


@pytest.mark.parametrize('channel', ['PUBLIC', 'PRIVATE'])
def test_conditional_basis_new_turn_frozen_paid_invalid_fallback_and_replay(play, channel):
    p, doc, view = begin(play, channel)
    _, view = ask_topic(play, view, channel=channel)
    identifier = view['play_id']; request = view['single_player']['turns'][-1]['reply_request']
    doc['topics'][0]['responders'][0]['answer_contract']['conditional_basis'][0]['requires'][0]['passage_ids'] = ['p0001']
    play.play.single_player_content = {content_hash(p): SinglePlayerContent(p, doc)}
    captured = []
    async def complete(messages, **params):
        context = json.loads(messages[1].content)['context']; captured.append(context)
        check = next(m for m in context['strategy_materials'] if m['id'] == 'single-player-answer-check')
        assert VERSION in check['text'] and 'conditional_basis' in check['text']
        assert 'p0002' in check['text'] and 'p0001' not in check['text']
        return output(speech(refs=[ref(passages=['p0001'])]))
    play.sdk.chat_completion.side_effect = complete
    respond = play.play.reply_private if channel == 'PRIVATE' else play.play.respond
    view = asyncio.run(respond(identifier, request, 1))
    assert view['single_player']['turns'][-1]['status'] == 'FAILED'
    assert view['single_player']['turns'][-1]['receipt_status'] == 'INVALID'
    assert view['budget']['used_tokens'] == 110
    assert play.sdk.chat_completion.await_count == 1
    old = [(e.event_json, e.request_json, e.state_hash) for e in events(play)]
    assert asyncio.run(respond(identifier, request, 1)) == view
    budget = deepcopy(view['budget']); _, view = fallback(play, view)
    assert view['single_player']['turns'][-1]['status'] == 'FALLBACK' and view['budget'] == budget
    if channel == 'PRIVATE': assert view['discussion']['entries'] == []
    assert [(e.event_json, e.request_json, e.state_hash) for e in events(play)[:len(old)]] == old
    play.db.commit()
    with play.factory() as db:
        recovered = configured_service(play, db)
        recovered.single_player_content = play.play.single_player_content
        assert recovered.get(identifier, 1) == view
    assert play.sdk.chat_completion.await_count == 1 and len(captured) == 1
    # A new follow-up freezes the changed catalogue, so the same citation now
    # satisfies its own p0001 requirement. This also proves valid SDK output
    # is not merely being rejected by an unrelated model/schema constraint.
    _, view = ask_topic(play, view, intent='clarify', channel=channel)
    play.sdk.chat_completion.side_effect = None
    play.sdk.chat_completion.return_value = output(speech(refs=[ref(passages=['p0001'])]))
    view = asyncio.run(respond(identifier, view['single_player']['turns'][-1]['reply_request'], 1))
    assert view['single_player']['turns'][-1]['status'] == 'OK'
    assert view['budget']['used_tokens'] == 220 and play.sdk.chat_completion.await_count == 2


@pytest.mark.parametrize('old_status', ['READY', 'PENDING', 'OK', 'FAILED'])
def test_conditional_basis_directory_upgrade_preserves_old_turn_and_context(play, old_status):
    p, doc, view = begin(play, 'PUBLIC', 'topic-answer-check/1.0')
    _, view = ask_topic(play, view)
    identifier = view['play_id']; request = view['single_player']['turns'][-1]['reply_request']
    row = play.play._row(identifier, 1); package, binding = play.play._resolve(row)
    state = play.play._replay(row, package, binding)
    original_context = play.play._dialogue_context(state, binding, 'b', request['reply_to'], version=play.play.full_dialogue_model.model_contract)
    play.sdk.chat_completion.return_value = output(speech(refs=[ref(passages=['p0001'])]))
    if old_status == 'PENDING':
        _, prepared = play.play._begin(identifier, request, 1)
        assert prepared is not None
    elif old_status == 'FAILED':
        play.sdk.chat_completion.side_effect = RuntimeError('fictional failure')
        view = asyncio.run(play.play.respond(identifier, request, 1))
    elif old_status == 'OK':
        view = asyncio.run(play.play.respond(identifier, request, 1))
        assert view['single_player']['turns'][-1]['status'] == 'OK'
    prior = [(e.event_json, e.request_json, e.state_hash) for e in events(play)]
    doc['topics'][0]['responders'][0]['answer_contract'] = contract()
    play.play.single_player_content = {content_hash(p): SinglePlayerContent(p, doc)}
    refreshed = play.play.get(identifier, 1)
    assert refreshed['single_player']['turns'][-1]['status'] == old_status
    assert [(e.event_json, e.request_json, e.state_hash) for e in events(play)] == prior
    row = play.play._row(identifier, 1); package, binding = play.play._resolve(row)
    state = play.play._replay(row, package, binding)
    restored_context = play.play._dialogue_context(state, binding, 'b', request['reply_to'],
        revision=request['expected_revision'] if old_status in ('READY', 'PENDING') else state.revision,
        version=play.play.full_dialogue_model.model_contract)
    # Later receipt discussion can add a reply; the frozen policy text itself is identical.
    assert restored_context['strategy_materials'] == original_context['strategy_materials']
    if old_status in ('READY', 'PENDING'):
        assert content_hash(restored_context) == content_hash(original_context)
    before_calls = play.sdk.chat_completion.await_count
    if old_status == 'READY':
        refreshed = asyncio.run(play.play.respond(identifier, request, 1))
        assert refreshed['single_player']['turns'][-1]['status'] == 'OK'
        assert play.sdk.chat_completion.await_count == before_calls + 1
    elif old_status == 'PENDING':
        assert asyncio.run(play.play.respond(identifier, request, 1)) == refreshed
        play.clock[0] += 100
        refreshed = asyncio.run(play.play.respond(identifier, request, 1))
        assert refreshed['single_player']['turns'][-1]['receipt_status'] == 'EXPIRED'
        assert play.sdk.chat_completion.await_count == before_calls
    else:
        assert asyncio.run(play.play.respond(identifier, request, 1)) == refreshed
        assert play.sdk.chat_completion.await_count == before_calls
    if refreshed['single_player']['turns'][-1]['status'] == 'FAILED':
        _, refreshed = fallback(play, refreshed)
    _, refreshed = ask_topic(play, refreshed, intent='clarify')
    play.sdk.chat_completion.side_effect = None
    request = refreshed['single_player']['turns'][-1]['reply_request']
    refreshed = asyncio.run(play.play.respond(identifier, request, 1))
    assert refreshed['single_player']['turns'][-1]['receipt_status'] == 'INVALID'
    assert refreshed['single_player']['turns'][0]['status'] in ('OK', 'FALLBACK')
