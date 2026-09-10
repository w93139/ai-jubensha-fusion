"""Finite answer schemas bind authorized options without repairing model answers."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from itertools import product
from unittest.mock import AsyncMock

from jsonschema import Draft202012Validator
import pytest

from src.fusion.agents import PlayerModelSettings
from src.fusion.package_role_model import PackageRoleModelError
from src.fusion.package_table_model import (
    BOUND_MODEL_CONTRACT, BoundPackageTableModel, PackageTableModel, TableContext,
    output_schema, table_context_window, table_metadata, validate_table_decision,
)
from src.fusion.package_validation import canonical_json, content_hash
from src.services.llm_service import LLMResponse


def settings(**changes):
    return replace(PlayerModelSettings(provider='volcengine_ark',
        model='doubao-seed-character-260628', timeout_seconds=60, retries=0,
        max_output_tokens=4096, max_input_bytes=65536, thinking_mode='disabled',
        temperature=0, paid_calls_enabled=True), **changes)


def context():
    return {'schema_version': 'package-table-context/1.0', 'play_id': 'play-' + 'a' * 32,
        'package_hash': 'b' * 64, 'revision': 220, 'character': {'id': 'b', 'name': '乙'},
        'current_phase': {'id': 'finale', 'title': '终局'},
        'materials': [{'collection': 'knowledge', 'id': 'book-b', 'text': '本人当前经历。',
                       'kind': 'FACT', 'public': False}],
        'discussion': [{'id': 'claim-1', 'sequence': 1, 'speaker': 'a', 'kind': 'CLAIM', 'text': '请分别说明。'}],
        'action': 'SEAL_FINALE', 'options': [],
        'questions': [
            {'id': 'q-one', 'prompt': '第一次选择',
             'options': [{'id': x, 'label': x} for x in ['red', 'blue', 'white']], 'max_choices': 1},
            {'id': 'q-two', 'prompt': '第二次选择',
             'options': [{'id': x, 'label': x} for x in ['north', 'south']], 'max_choices': 2},
            {'id': 'q-empty', 'prompt': '未知选择', 'options': [], 'max_choices': 0}],
        'accusation_options': [{'id': 'a', 'label': '甲'}, {'id': 'b', 'label': '乙'}],
        'trust_character_ids': ['a', 'c', 'd', 'e'],
        'history_window': {'policy': 'full-play-context-window/1.0', 'omitted_count': 0}}


def answer():
    return {'schema_version': 'structured-finale-submission/1.0',
        'answers': [{'question_id': 'q-one', 'option_ids': ['red']},
                    {'question_id': 'q-two', 'option_ids': ['north', 'south']},
                    {'question_id': 'q-empty', 'option_ids': []}],
        'vote': {'accusation_id': None, 'trust_character_id': None}, 'reflection': ''}


def response(value):
    return LLMResponse(content=canonical_json(value), usage={'prompt_tokens': 100, 'completion_tokens': 10},
                       model=settings().model, finish_reason='stop')


def test_legacy_metadata_schema_and_prepared_match_pre_change_snapshots():
    old = PackageTableModel(object(), settings())
    # Captured before adding 1.1, using the entirely fictional context above.
    assert content_hash(old.metadata()) == 'e11f7438858e24dfff9948d4cd92ad4d7ac889529180d82affd6bb38e77a5e60'
    assert content_hash(output_schema(context())) == 'da6a4d76d3fce5489924fe3c93fa45e22d62d8b4fea48e281bcd274f63ed91a2'
    assert content_hash(old.prepare(context())) == '24753cf3f00172752d6d92eb07daf099080bd9e63ff9c8cb903ce6d4b441fb5d'
    new = BoundPackageTableModel(object(), settings())
    assert new.metadata()['schema_version'] == BOUND_MODEL_CONTRACT
    assert new.metadata()['prompt_hash'] == old.metadata()['prompt_hash']
    assert new.metadata()['answer_binding_policy'] == 'per-question-options-and-limit/1.0'
    assert new.metadata()['input_measure_policy'] == 'validated-context/1.0'
    assert 'answer_binding_policy' not in old.metadata()


@pytest.mark.parametrize('kind', ['too_many', 'foreign_question_option', 'unknown_option', 'no_available_option'])
def test_bound_schema_rejects_option_errors_accepted_by_old_schema(kind):
    value = answer()
    if kind == 'too_many': value['answers'][0]['option_ids'] = ['red', 'blue']
    elif kind == 'foreign_question_option': value['answers'][0]['option_ids'] = ['north']
    elif kind == 'unknown_option': value['answers'][0]['option_ids'] = ['hidden']
    else: value['answers'][2]['option_ids'] = ['red']
    assert Draft202012Validator(output_schema(context())).is_valid(value)
    schema = output_schema(context(), BOUND_MODEL_CONTRACT)
    Draft202012Validator.check_schema(schema)
    assert not Draft202012Validator(schema).is_valid(value)
    with pytest.raises(ValueError): validate_table_decision(value, context())


def test_bound_schema_retains_valid_answers_and_explicit_unknown_with_no_options():
    c = context(); before = deepcopy(c)
    schema = output_schema(c, BOUND_MODEL_CONTRACT)
    for value in (answer(), {**answer(), 'answers': [dict(question_id=q['id'], option_ids=[]) for q in c['questions']]}):
        Draft202012Validator(schema).validate(value)
        assert validate_table_decision(value, c) == value
    assert c == before


def test_compaction_preserves_every_question_option_set_and_its_own_limit():
    c = context()
    c['questions'].extend([
        {**deepcopy(c['questions'][0]), 'id': 'q-same-reordered',
         'options': list(reversed(c['questions'][0]['options']))},
        {**deepcopy(c['questions'][0]), 'id': 'q-same-options-other-limit', 'max_choices': 2},
        {**deepcopy(c['questions'][2]), 'id': 'q-empty-too'}])
    schema = output_schema(c, BOUND_MODEL_CONTRACT)
    branches = schema['$defs']['StructuredAnswer']['anyOf']
    assert len(branches) == 4
    validator = Draft202012Validator({'$defs': schema['$defs'], '$ref': '#/$defs/StructuredAnswer'})
    questions = {q['id']: q for q in c['questions']}
    options = ['red', 'blue', 'white', 'north', 'south', 'foreign']
    for identifier in [*questions, 'foreign-question']:
        for count in range(4):
            for selected in product(options, repeat=count):
                q = questions.get(identifier)
                expected = bool(q is not None and count <= q['max_choices']
                                and set(selected) <= {o['id'] for o in q['options']})
                assert validator.is_valid({'question_id': identifier, 'option_ids': list(selected)}) is expected
    # Option duplication is intentionally also checked by the final validator;
    # schema compaction never substitutes for its existing strict validation.


@pytest.mark.parametrize('kind', ['missing', 'duplicate_question', 'duplicate_option', 'extra', 'wrong_vote'])
def test_end_validator_still_requires_exact_question_coverage_and_unique_choices(kind):
    value = answer()
    if kind == 'missing': value['answers'].pop()
    elif kind == 'duplicate_question': value['answers'][2] = dict(question_id='q-one', option_ids=['blue'])
    elif kind == 'duplicate_option': value['answers'][1]['option_ids'] = ['north', 'north']
    elif kind == 'extra': value['answers'].append(dict(question_id='future-question', option_ids=[]))
    else: value['vote']['trust_character_id'] = 'b'
    before = deepcopy(value)
    model = BoundPackageTableModel(object(), settings())
    with pytest.raises(ValueError): model._read_output(canonical_json(value), model.prepare(context()))
    assert value == before
    if kind in ('missing', 'extra'):
        assert not Draft202012Validator(output_schema(context(), BOUND_MODEL_CONTRACT)).is_valid(value)


@pytest.mark.parametrize('action', ['CAST_BALLOT', 'BREAK_TIE'])
def test_new_ballot_and_tie_keep_existing_schema_and_legal_choices(action):
    c = context(); c.update(action=action, options=[{'id': 'go', 'label': '调查', 'cost': 1}],
                            questions=[], accusation_options=[], trust_character_ids=[])
    assert output_schema(c, BOUND_MODEL_CONTRACT) == output_schema(c)
    old, new = PackageTableModel(object(), settings()), BoundPackageTableModel(object(), settings())
    assert old.prepare(c) == new.prepare(c)
    value = {'choice_id': 'go'}
    if action == 'CAST_BALLOT': value['kind'] = 'CHOOSE'
    assert validate_table_decision(value, c) == value
    value['choice_id'] = 'hidden'
    with pytest.raises(ValueError): validate_table_decision(value, c)


@pytest.mark.parametrize('kind', ['schema_limit', 'schema_options', 'context_limit', 'input_reservation'])
def test_tampered_prepared_is_rejected_before_mock_sdk(kind):
    sdk = AsyncMock(); model = BoundPackageTableModel(sdk, settings()); frozen = model.prepare(context())
    bad = deepcopy(frozen)
    if kind.startswith('schema_'):
        props = bad['params']['response_format']['json_schema']['schema']['$defs']['StructuredAnswer']['anyOf'][0]['properties']
        if kind == 'schema_limit': props['option_ids']['maxItems'] = 10
        else: props['option_ids']['items']['enum'].append('foreign')
    elif kind == 'context_limit':
        payload = json.loads(bad['messages'][1]['content']); payload['context']['questions'][0]['max_choices'] = 2
        bad['messages'][1]['content'] = canonical_json(payload)
    else: bad['input_tokens'] += 1
    result = asyncio.run(model.call(bad))
    assert result['status'] == 'INVALID' and result['model_attempted'] is False
    sdk.chat_completion.assert_not_awaited()
    assert model.prepare(context()) == frozen


@pytest.mark.parametrize('invalid', [False, True])
def test_mock_dispatch_preserves_input_and_invalid_answers_are_not_repaired(invalid):
    sdk = AsyncMock(); model = BoundPackageTableModel(sdk, settings()); prepared = model.prepare(context())
    value = answer()
    if invalid: value['answers'][0]['option_ids'] = ['red', 'blue']
    original = deepcopy(value)
    async def complete(messages, **params):
        assert [dict(role=m.role, content=m.content) for m in messages] == prepared['messages']
        assert params == prepared['params']
        return response(value)
    sdk.chat_completion.side_effect = complete
    result = asyncio.run(model.call(prepared))
    assert result['model_attempted'] is True and result['status'] == ('INVALID' if invalid else 'OK')
    assert result['usage']['prompt_tokens'] == 100
    if invalid: assert 'decision' not in result
    else: assert result['decision'] == value
    assert value == original and sdk.chat_completion.await_count == 1


def test_window_measures_bound_schema_and_parsed_context_at_exact_limit():
    c = context(); c.pop('history_window')
    c['discussion'] = [dict(id=f'claim-{n}', sequence=n, speaker='a', kind='CLAIM', text='只是尚待核对的发言。' * 20)
                       for n in range(1, 221)]
    c['questions'] = [dict(id=f'q-{n}', prompt=f'第{n}题', max_choices=2,
                          options=[dict(id=f'option-{n}-{i}', label=f'合法选项{i}') for i in range(8)]) for n in range(12)]
    original = deepcopy(c); model = BoundPackageTableModel(object(), settings())
    full = table_context_window(c, 65536, BOUND_MODEL_CONTRACT)
    assert len(full['discussion']) == 60 and full['history_window']['omitted_count'] == 160
    full_prepared = model.prepare(full); limit = full_prepared['input_tokens'] - 4096 - 1
    window = table_context_window(c, limit, BOUND_MODEL_CONTRACT)
    bounded = BoundPackageTableModel(object(), settings(max_input_bytes=limit))
    prepared = bounded.prepare(window)
    actual_bytes = len(canonical_json(prepared['messages']).encode()) + len(canonical_json(prepared['params']['response_format']).encode())
    assert actual_bytes == prepared['input_tokens'] - 4096 <= limit
    assert len(window['discussion']) < 60
    assert window['questions'] == c['questions'] and window['materials'] == c['materials'] and c == original
    assert json.loads(prepared['messages'][1]['content'])['context'] == TableContext.model_validate(window).model_dump()
    with pytest.raises(PackageRoleModelError, match='PACKAGE_TABLE_INPUT_TOO_LARGE'):
        bounded.prepare(full)


def test_required_questions_cannot_be_trimmed_to_fit_input_ceiling():
    c = context(); c['discussion'] = []
    c['questions'] = [dict(id=f'q-{n}', prompt='本人获准的完整题目。' * 80, max_choices=1,
                          options=[dict(id=f'option-{n}-{i}', label='合法选项') for i in range(20)]) for n in range(30)]
    original = deepcopy(c); sdk = AsyncMock(); model = BoundPackageTableModel(sdk, settings())
    with pytest.raises(PackageRoleModelError, match='PACKAGE_TABLE_INPUT_TOO_LARGE'): model.prepare(c)
    with pytest.raises(PackageRoleModelError, match='FULL_PLAY_REQUIRED_CONTEXT_TOO_LARGE'):
        table_context_window(c, 65536, BOUND_MODEL_CONTRACT)
    sdk.chat_completion.assert_not_awaited(); assert c == original


def test_unknown_model_version_is_not_silently_treated_as_legacy():
    with pytest.raises(ValueError): table_metadata({}, 'package-table-model/99')
    with pytest.raises(ValueError): output_schema(context(), 'package-table-model/99')
    with pytest.raises(ValueError): table_context_window(context(), 65536, 'package-table-model/99')
