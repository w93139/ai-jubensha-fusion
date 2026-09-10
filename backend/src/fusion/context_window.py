"""Deterministic, source-preserving context selection for explicit full-game models."""
from copy import deepcopy
from typing import Literal
from pydantic import Field
from src.schemas.package_primitives import PackageModel

from src.fusion.package_role_model import PackageRoleModelError

WINDOW_POLICY = 'full-play-context-window/1.0'
WINDOW_PROMPT = '\n输入 history_window 说明只带入部分近期对话。未带入的旧发言不代表没有发生，不得据此推断别人从未说过或不知道。本人已获材料与本次题目完整保留；不能为缺失对话编造摘要或证据。\n'


class HistoryWindow(PackageModel):
    policy: Literal['full-play-context-window/1.0']
    omitted_count: int = Field(ge=0, le=2000)


def bounded_context(context, max_bytes, measure, pinned_ids=()):
    """Authorize before this call. Never trim materials, goals, forms, or targets.

    Selection is frozen by model metadata and replayed from the original events.
    No generated summary, synthetic claim, or new knowledge is introduced.
    """
    value = deepcopy(context)
    previous = value.get('history_window', {})
    total = len(value['discussion']) + previous.get('omitted_count', 0)
    original = value['discussion']
    pinned = [c for c in original if c['id'] in pinned_ids]
    if set(pinned_ids) != {c['id'] for c in pinned}:
        raise PackageRoleModelError('FULL_PLAY_CONTEXT_TARGET_MISSING')
    tail = original[-60:]
    def choose(items):
        combined = {c['id']: c for c in [*pinned, *items]}
        value['discussion'] = sorted(combined.values(), key=lambda c: c['sequence'])
        value['history_window'] = {'policy': WINDOW_POLICY, 'omitted_count': total - len(combined)}
    choose([])
    if measure(value) > max_bytes:
        raise PackageRoleModelError('FULL_PLAY_REQUIRED_CONTEXT_TOO_LARGE')
    choose(tail)
    while measure(value) > max_bytes:
        tail = tail[1:]
        choose(tail)
    return value
