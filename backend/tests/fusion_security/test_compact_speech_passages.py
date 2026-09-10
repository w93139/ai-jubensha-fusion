"""Grouped enums preserve source permissions; byte windows match SDK payloads."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json

from jsonschema import Draft202012Validator

from src.fusion.package_call_model import CompactPassagePackageCallModel, call_window
from src.fusion.package_dialogue_model import CompactPassageFullPackageDialogueModel, dialogue_context_window, speech_schema, validate_speech
from src.fusion.package_play import PackagePlayService
from src.fusion.package_validation import canonical_json
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_play_store import play, start, action_body
from tests.fusion_security.test_full_play_speech_strategy import package
from tests.fusion_security.test_full_play_decisions import output
from tests.fusion_security.test_full_play_phone import phone, invite
from tests.fusion_security.test_speech_clarification import statement, reply
from tests.fusion_security.test_speech_passages import grounded

VERSION = 'package-dialogue-model/1.10'


def test_grouping_preserves_every_allowed_collection_source_and_passage_tuple():
    c = {'materials': [{'collection': kind, 'id': f'item-{n}', 'text': '一段。' + ('\n\n二段。' if n % 3 else '')}
        for kind in ('knowledge', 'evidence') for n in range(24)],
        'discussion': [{'id': 'item-1', 'text': '公开发言。'}], 'strategy_materials': [], 'reply_to': 'item-1'}
    old, new = [speech_schema(v, c) for v in ('package-dialogue-model/1.9', VERSION)]
    a, b = Draft202012Validator(old), Draft202012Validator(new)
    assert len(canonical_json(new)) < len(canonical_json(old)) / 2
    for kind in ('knowledge', 'evidence', 'discussion', 'memory'):
        for n in range(25):
            for passage in ('p0001', 'p0002', 'p0003'):
                value = {'segments': [{'basis': [{'collection': kind, 'id': f'item-{n}', 'passage_ids': [passage]}],
                    'mode': 'REPORT', 'text': '用于离线核对的台词。'}]}
                assert a.is_valid(value) == b.is_valid(value)
                if b.is_valid(value): validate_speech(value, c, VERSION)


def test_phone_window_counts_default_fields_before_trimming_at_exact_byte_boundary(play):
    c = dict(schema_version='package-call-context/1.1', play_id='play-'+'a'*32, package_hash='b'*64,
        revision=2, character={'id':'actor','name':'本人'}, current_phase={'id':'one','title':'讨论'},
        stage='CONNECTED', peers=[{'id':'peer','name':'对方'}], planning_materials=[], strategy_materials=[],
        materials=[{'collection':'knowledge','id':f'fact-{n}','kind':'FACT','text':'公开场景说明。'} for n in range(30)],
        discussion=[{'id':'old','kind':'CLAIM','sequence':1,'speaker':'peer','text':'先前说的话。'*30},
                    {'id':'question','kind':'CLAIM','sequence':2,'speaker':'peer','text':'现在想说什么？'}], reply_to='question')
    original = deepcopy(c)
    model = CompactPassagePackageCallModel(object(), replace(play.model.settings, max_input_bytes=65536))
    full = call_window(c, 65536, 'package-call-model/1.7')
    size = model.prepare(full)['input_tokens'] - 4096
    limit = size - 1
    trimmed = call_window(c, limit, 'package-call-model/1.7')
    assert trimmed['materials'] == c['materials'] and c == original
    assert trimmed['history_window']['omitted_count'] == 1
    assert [d['id'] for d in trimmed['discussion']] == ['question']
    model = CompactPassagePackageCallModel(object(), replace(play.model.settings, max_input_bytes=limit))
    prepared = model.prepare(trimmed)
    assert prepared['input_tokens'] - 4096 <= limit
    assert all(m['retelling'] is None for m in prepared['source_context']['materials'])


def test_first_public_and_phone_calls_from_unwindowed_service_input_and_replay(play):
    def modern(db):
        return PackagePlayService(db, play.publisher, play.model, play.policy, lambda: play.clock[0], speech_policy='role-speech/1.7')
    play.play = modern(play.db)
    _, view = start(play, package())
    view = play.play.act(view['play_id'], action_body(0, 'advance', 'ADVANCE_PHASE'), 1)
    view = statement(play, view)
    async def complete(messages, **params):
        context = json.loads(messages[1].content)['context']
        value = invite() if context.get('stage') == 'IDLE' else grounded(context)
        if context.get('stage') == 'CONNECTED': value = {'kind':'SPEAK','peer_character_id':None,'speech':value}
        Draft202012Validator(params['response_format']['json_schema']['schema']).validate(value)
        return output(value)
    play.sdk.chat_completion.side_effect = complete
    view = asyncio.run(play.play.respond(view['play_id'], reply(view), 1))
    assert view['last_ai_status'] == 'OK'
    for _ in range(2):
        request = phone(view['revision'])
        view = asyncio.run(play.play.phone_step(view['play_id'], request, 1))
        assert view['last_ai_status'] == 'OK'
        assert asyncio.run(play.play.phone_step(view['play_id'], request, 1)) == view
        with play.factory() as db: assert modern(db).get(view['play_id'], 1) == view
    assert play.sdk.chat_completion.await_count == 3


def test_long_raw_history_is_windowed_before_context_model_validation(play):
    context = dict(schema_version='package-dialogue-context/1.2', play_id='play-'+'a'*32, package_hash='b'*64,
        revision=220, character={'id':'actor','name':'本人'}, current_phase={'id':'one','title':'讨论'},
        channel='PUBLIC', materials=[], strategy_materials=[], reply_to='heard-219',
        discussion=[{'id':f'heard-{n}','kind':'CLAIM','sequence':n+1,'speaker':'peer','text':'实际听到的发言。'} for n in range(220)])
    windowed = dialogue_context_window(context, 65536, VERSION)
    assert len(windowed['discussion']) == 60
    assert windowed['history_window']['omitted_count'] == 160
    model = CompactPassageFullPackageDialogueModel(object(), replace(play.model.settings, max_input_bytes=65536))
    assert model.prepare(windowed)['input_tokens'] - 4096 <= 65536
