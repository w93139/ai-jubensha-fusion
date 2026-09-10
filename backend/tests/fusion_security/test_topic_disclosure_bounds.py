"""Opt-in disclosure bounds using fictional words and an offline mock SDK."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from src.fusion.package_play_rules import PlayRulesError
from src.fusion.package_single_player import SinglePlayerContent
from src.fusion.package_validation import canonical_json, content_hash
from src.fusion.topic_answer_checks import validate_contract, freeze_contract, validate_topic_answer
from tests.fusion_security.test_conditional_topic_basis import (
    begin, configured_service, contract as basis_contract, materials, ref, speech,
)
from tests.fusion_security.test_package_play_store import play, events
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_single_player_topics import ask_topic, fallback
from tests.fusion_security.test_full_play_decisions import output


VERSION = 'topic-answer-check/1.2'


def contract(terms=None):
    return {**basis_contract(), 'schema_version': VERSION,
            'forbidden_terms': ['密钥', '藏盒'] if terms is None else terms}


@pytest.mark.parametrize('terms', [
    None, {}, '密钥', ('密钥',), True, 7, [None], [7], [True], [''], [' \t\n'], ['！？'],
    ['密\x00钥'], ['密\u200b钥'], ['密' * 81], ['密钥', '密，钥'], ['KEY', 'ｋｅｙ'],
    [f'词{i}' for i in range(33)],
])
def test_disclosure_rejects_invalid_authored_terms(terms):
    c = contract()
    c['forbidden_terms'] = terms
    with pytest.raises(ValueError):
        validate_contract(c, materials(), {'initial'})


@pytest.mark.parametrize('version', ['topic-answer-check/1.0', 'topic-answer-check/1.1'])
@pytest.mark.parametrize('terms', [[], ['密钥']])
def test_legacy_contract_versions_reject_disclosure_field_even_when_empty(version, terms):
    c = {'schema_version': version, 'forbidden_terms': terms}
    with pytest.raises(ValueError):
        validate_contract(c, materials(), {'initial'})


def test_disclosure_valid_boundaries_and_frozen_copy():
    c = contract([f'词{i}' for i in range(31)] + ['字' * 80])
    validate_contract(c, materials(), {'initial'})
    frozen = freeze_contract(c, 'initial', [])
    c['forbidden_terms'][0] = '改词'
    c['conditional_basis'][0]['requires'][0]['passage_ids'][0] = 'p0001'
    assert frozen['forbidden_terms'][0] == '词0'
    assert frozen['conditional_basis'][0]['requires'][0]['passage_ids'] == ['p0002']
    frozen['forbidden_terms'].append('新词')
    assert '新词' not in c['forbidden_terms']
    for optional in ({'schema_version': VERSION}, contract([])):
        validate_contract(optional, materials(), {'initial'})
        validate_topic_answer(speech('旅店旁的情况尚待核对。'), {'answer_contract': freeze_contract(optional, 'initial', [])})


@pytest.mark.parametrize('parts,term', [
    (['我保管着密钥。'], '密钥'),
    (['我保管着密，钥。'], '密钥'),
    (['我保管着密', '钥。'], '密钥'),
    (['我保管着密。', '（钥）'], '密钥'),
    (['Ｋ', 'ＥＹ'], 'key'),
    (['藏\n盒'], '藏盒'),
    (['我没有密钥。'], '密钥'),
])
def test_disclosure_matches_across_segments_punctuation_and_nfkc(parts, term):
    value = {'segments': [speech(part)['segments'][0] for part in parts]}
    frozen = freeze_contract(contract([term]), 'initial', [])
    with pytest.raises(PlayRulesError, match='SINGLE_TOPIC_DISCLOSURE_FORBIDDEN'):
        validate_topic_answer(value, {'answer_contract': frozen})


def test_disclosure_allows_legal_reply_and_retains_conditional_citation_requirement():
    frozen = freeze_contract(contract(), 'initial', [])
    validate_topic_answer(speech('我提到钟楼，只作线索核对。'), {'answer_contract': frozen})
    with pytest.raises(PlayRulesError, match='SINGLE_TOPIC_BASIS_INCOMPLETE'):
        validate_topic_answer(speech(refs=[ref(passages=['p0001'])]), {'answer_contract': frozen})
    for old_version in ('topic-answer-check/1.0', 'topic-answer-check/1.1'):
        old = {'schema_version': old_version}
        validate_contract(old, materials(), {'initial'})
        validate_topic_answer(speech('我保管着密钥。'), {'answer_contract': freeze_contract(old, 'initial', [])})


@pytest.mark.parametrize('channel', ['PUBLIC', 'PRIVATE'])
def test_disclosure_failure_preserves_paid_receipt_fallback_and_replay(play, channel):
    package, doc, view = begin(play, channel)
    authored = doc['topics'][0]['responders'][0]
    authored['answer_contract'] = contract(['密钥'])
    play.play.single_player_content = {content_hash(package): SinglePlayerContent(package, doc)}
    _, view = ask_topic(play, view, channel=channel)
    identifier = view['play_id']
    request = view['single_player']['turns'][-1]['reply_request']
    # A directory edit after the turn cannot relax the frozen disclosure rule.
    authored['answer_contract'] = contract(['藏盒'])
    play.play.single_player_content = {content_hash(package): SinglePlayerContent(package, doc)}
    leaked = '我提到钟楼，只作线索核对。我保管着密钥。'
    captured = []
    async def complete(messages, **params):
        context = json.loads(messages[1].content)['context']
        captured.append(context)
        check = next(item for item in context['strategy_materials'] if item['id'] == 'single-player-answer-check')
        assert VERSION in check['text'] and 'forbidden_terms' in check['text']
        assert '密钥' in check['text'] and '藏盒' not in check['text']
        assert 'conditional_basis' in check['text']
        return output(speech(leaked))
    play.sdk.chat_completion.side_effect = complete
    respond = play.play.reply_private if channel == 'PRIVATE' else play.play.respond
    with patch('src.fusion.package_play.validate_topic_answer', wraps=validate_topic_answer) as checker:
        view = asyncio.run(respond(identifier, request, 1))
    assert checker.call_count == 1
    with pytest.raises(PlayRulesError, match='SINGLE_TOPIC_DISCLOSURE_FORBIDDEN'):
        validate_topic_answer(*checker.call_args.args, **checker.call_args.kwargs)
    turn = view['single_player']['turns'][-1]
    assert turn['status'] == 'FAILED' and turn['receipt_status'] == 'INVALID'
    assert view['budget']['used_tokens'] == 110
    assert play.sdk.chat_completion.await_count == 1
    assert leaked not in canonical_json(view)
    old_events = [(e.event_json, e.request_json, e.state_hash) for e in events(play)]
    assert asyncio.run(respond(identifier, request, 1)) == view
    paid_budget = deepcopy(view['budget'])
    fallback_request, view = fallback(play, view)
    assert view['single_player']['turns'][-1]['status'] == 'FALLBACK'
    assert view['budget'] == paid_budget and leaked not in canonical_json(view)
    assert play.play.topic(identifier, fallback_request, 1) == view
    assert asyncio.run(respond(identifier, request, 1)) == view
    if channel == 'PRIVATE':
        assert view['discussion']['entries'] == []
    assert [(e.event_json, e.request_json, e.state_hash) for e in events(play)[:len(old_events)]] == old_events
    play.db.commit()
    with play.factory() as db:
        restored = configured_service(play, db)
        restored.single_player_content = play.play.single_player_content
        assert restored.get(identifier, 1) == view
    assert play.sdk.chat_completion.await_count == len(captured) == 1
    # A separate follow-up uses the newer allowed wording and succeeds normally.
    _, view = ask_topic(play, view, intent='clarify', channel=channel)
    play.sdk.chat_completion.side_effect = None
    play.sdk.chat_completion.return_value = output(speech('我提到钟楼，只作线索核对。'))
    view = asyncio.run(respond(identifier, view['single_player']['turns'][-1]['reply_request'], 1))
    assert view['single_player']['turns'][-1]['status'] == 'OK'
    assert view['budget']['used_tokens'] == 220 and play.sdk.chat_completion.await_count == 2


@pytest.mark.parametrize('legacy_version', ['topic-answer-check/1.0', 'topic-answer-check/1.1'])
@pytest.mark.parametrize('old_status', ['READY', 'PENDING', 'OK', 'FAILED'])
def test_disclosure_directory_upgrade_does_not_rewrite_old_event_or_context(play, legacy_version, old_status):
    package, doc, view = begin(play, 'PUBLIC', legacy_version)
    _, view = ask_topic(play, view)
    identifier = view['play_id']
    request = view['single_player']['turns'][-1]['reply_request']
    def context():
        row = play.play._row(identifier, 1)
        p, binding = play.play._resolve(row)
        state = play.play._replay(row, p, binding)
        return play.play._dialogue_context(state, binding, 'b', request['reply_to'],
            revision=request['expected_revision'] if old_status in ('READY', 'PENDING') else state.revision,
            version=play.play.full_dialogue_model.model_contract)
    old_context = context()
    leaked_for_new = speech('我提到钟楼，只作线索核对。我保管着密钥。')
    play.sdk.chat_completion.return_value = output(leaked_for_new)
    if old_status == 'PENDING':
        _, prepared = play.play._begin(identifier, request, 1)
        assert prepared is not None
    elif old_status == 'FAILED':
        play.sdk.chat_completion.side_effect = RuntimeError('fictional failure')
        view = asyncio.run(play.play.respond(identifier, request, 1))
    elif old_status == 'OK':
        view = asyncio.run(play.play.respond(identifier, request, 1))
        assert view['single_player']['turns'][-1]['status'] == 'OK'
    old_events = [(e.event_json, e.request_json, e.state_hash) for e in events(play)]
    doc['topics'][0]['responders'][0]['answer_contract'] = contract()
    play.play.single_player_content = {content_hash(package): SinglePlayerContent(package, doc)}
    refreshed = play.play.get(identifier, 1)
    assert refreshed['single_player']['turns'][-1]['status'] == old_status
    assert [(e.event_json, e.request_json, e.state_hash) for e in events(play)] == old_events
    new_context = context()
    assert new_context['strategy_materials'] == old_context['strategy_materials']
    if old_status in ('READY', 'PENDING'):
        assert content_hash(new_context) == content_hash(old_context)
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
    play.sdk.chat_completion.return_value = output(leaked_for_new)
    refreshed = asyncio.run(play.play.respond(identifier, refreshed['single_player']['turns'][-1]['reply_request'], 1))
    assert refreshed['single_player']['turns'][-1]['receipt_status'] == 'INVALID'
    assert refreshed['single_player']['turns'][0]['status'] in ('OK', 'FALLBACK')
