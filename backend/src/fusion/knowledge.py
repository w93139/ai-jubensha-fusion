"""角色知识边界：先授权，再生成上下文；摘要和模型都不能扩大可见范围。"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass

from src.fusion.rules import EvidenceVisibility


def event_is_visible(visibility: str, recipient: int | None, character_id: int | None) -> bool:
    """只放行显式公开或发给当前角色的私有事件，未实现的权限默认拒绝。"""
    if visibility == EvidenceVisibility.PUBLIC.value:
        return recipient is None
    if visibility == EvidenceVisibility.CHARACTER_PRIVATE.value:
        return character_id is not None and recipient == character_id
    # SYSTEM_TRUTH、MURDERER_ONLY、未知值都不是普通角色的读取凭据。
    return False


@dataclass(frozen=True)
class RoleScope:
    session_id: str
    script_id: int
    character_id: int
    phase: str
    state_version: int


@dataclass(frozen=True)
class RoleKnowledgeView:
    """服务端构建的角色快照；不包含客观凶手标签或未获得的证据。"""

    scope: RoleScope
    identity: dict
    evidence: tuple[dict, ...]
    events: tuple[dict, ...]

    def model_inputs(self) -> tuple[dict, list[dict]]:
        # 返回副本，避免一个模型适配器污染其他角色的快照。
        context = {
            **deepcopy(self.identity),
            "phase": self.scope.phase,
            "known_evidence": deepcopy(list(self.evidence)),
        }
        return context, deepcopy(list(self.events))
