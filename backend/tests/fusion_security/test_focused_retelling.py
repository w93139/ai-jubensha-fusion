"""Derived target excerpts stay within the authorized, versioned request."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

import pytest

from src.fusion.package_dialogue_model import (
    FocusedTaskPassageFullPackageDialogueModel, TaskPassageFullPackageDialogueModel,
    dialogue_context_window, validate_speech,
)
from src.fusion.package_play import PackagePlayService
from src.fusion.package_role_model import PackageRoleModelError
from tests.fusion_security.test_package_runtime import runtime
from tests.fusion_security.test_package_play_store import play
from tests.fusion_security import test_required_retelling as old

VERSION = 'package-dialogue-model/1.12'


def focused(play, db=None):
    return PackagePlayService(db if db is not None else play.db, play.publisher, play.model,
        play.policy, lambda: play.clock[0], speech_policy='role-speech/1.9')


def context(play):
    v = old.begin(play)
    _, c = old.state_context(play, v)
    return c


def test_target_copy_is_exact_authorized_source_and_not_a_second_basis(play):
    c = context(play); before = deepcopy(c)
    model = FocusedTaskPassageFullPackageDialogueModel(play.sdk, play.play.full_dialogue_model.settings)
    prepared = model.prepare(c)
    wire = json.loads(prepared['messages'][1]['content'])['context']
    target = wire['response_task']['target']
    material = next(m for m in wire['materials'] if m['id'] == target['id'])
    assert wire['response_task']['target_passages'] == material['passages']
    assert c == before and 'target_passages' not in prepared['source_context']['response_task']
    assert validate_speech(old.assigned_reply(c), c, VERSION)
    bad = old.assigned_reply(c); bad['segments'][0]['basis'][0]['collection'] = 'response_task'
    with pytest.raises(ValueError): validate_speech(bad, c, VERSION)
    legacy = TaskPassageFullPackageDialogueModel(play.sdk, model.settings).prepare(c)
    assert 'target_passages' not in json.loads(legacy['messages'][1]['content'])['context']['response_task']
    assert model.metadata()['task_wire_policy'] == 'authorized-target-passage-copy/1.0'
    assert 'task_wire_policy' not in TaskPassageFullPackageDialogueModel(play.sdk, model.settings).metadata()


@pytest.mark.parametrize('where', ['wire', 'source'])
def test_changed_target_copy_or_original_is_rejected_before_sdk(play, where):
    c = context(play)
    model = FocusedTaskPassageFullPackageDialogueModel(play.sdk, play.play.full_dialogue_model.settings)
    prepared = model.prepare(c)
    if where == 'wire':
        wire = json.loads(prepared['messages'][1]['content'])
        wire['context']['response_task']['target_passages'][0]['text'] = '伪造来源'
        prepared['messages'][1]['content'] = json.dumps(wire, ensure_ascii=False)
    else:
        prepared['source_context']['response_task']['target']['id'] = 'memory-b-second'
    result = asyncio.run(model.call(prepared))
    assert result['status'] == 'INVALID' and result['model_attempted'] is False
    assert play.sdk.chat_completion.await_count == 0


def test_exact_capacity_includes_target_echo_and_normalized_prior_defaults(play):
    c = context(play)
    c['response_task']['prior_attempts'] = [dict(request_sequence=1, result_status='UNKNOWN', status='FAILED_UNVERIFIED')]
    model = FocusedTaskPassageFullPackageDialogueModel(play.sdk, play.play.full_dialogue_model.settings)
    size = model.prepare(c)['input_tokens'] - 4096
    assert dialogue_context_window(c, size, VERSION) == c
    with pytest.raises(PackageRoleModelError, match='REQUIRED_CONTEXT_TOO_LARGE'):
        dialogue_context_window(c, size-1, VERSION)


def test_new_service_preserves_bounded_rotation_and_restart(play):
    with patch.object(old, 'modern', focused), patch.object(old, 'VERSION', VERSION):
        old.test_rotation_two_attempt_limit_no_implicit_semantic_completion_and_replay(play)


def test_meta_refusal_is_zero_sdk_without_task_copy(play):
    with patch.object(old, 'modern', focused), patch.object(old, 'VERSION', VERSION):
        old.test_meta_question_and_not_yet_unlocked_memory_create_no_assignment_and_zero_sdk(play)


def test_plain_and_private_context_have_no_target_copy(play):
    c = context(play); c.pop('response_task'); c['channel'] = 'PRIVATE'
    model = FocusedTaskPassageFullPackageDialogueModel(play.sdk, play.play.full_dialogue_model.settings)
    wire = json.loads(model.prepare(c)['messages'][1]['content'])['context']
    assert 'response_task' not in wire
