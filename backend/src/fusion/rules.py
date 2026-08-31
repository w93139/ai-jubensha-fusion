"""确定性的剧本杀规则；LLM 无权绕过这里的校验。"""
from __future__ import annotations

from collections import Counter
from enum import StrEnum
from typing import Iterable


class ParticipantType(StrEnum):
    HUMAN = "HUMAN"
    AI = "AI"


class EvidenceVisibility(StrEnum):
    PUBLIC = "PUBLIC"
    CHARACTER_PRIVATE = "CHARACTER_PRIVATE"
    MURDERER_ONLY = "MURDERER_ONLY"
    SYSTEM_TRUTH = "SYSTEM_TRUTH"


class ScriptPublishStatus(StrEnum):
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    PUBLISHED = "PUBLISHED"
    ARCHIVED = "ARCHIVED"


class FusionGamePhase(StrEnum):
    CHARACTER_SELECTION = "CHARACTER_SELECTION"
    SCRIPT_READING = "SCRIPT_READING"
    BACKGROUND = "BACKGROUND"
    INTRODUCTION = "INTRODUCTION"
    EVIDENCE_ROUND_1 = "EVIDENCE_ROUND_1"
    INVESTIGATION = "INVESTIGATION"
    EVIDENCE_ROUND_2 = "EVIDENCE_ROUND_2"
    DISCUSSION = "DISCUSSION"
    VOTING = "VOTING"
    RUNOFF_VOTING = "RUNOFF_VOTING"
    REVELATION = "REVELATION"
    ENDED = "ENDED"


PHASE_ORDER = (
    FusionGamePhase.CHARACTER_SELECTION,
    FusionGamePhase.SCRIPT_READING,
    FusionGamePhase.BACKGROUND,
    FusionGamePhase.INTRODUCTION,
    FusionGamePhase.EVIDENCE_ROUND_1,
    FusionGamePhase.INVESTIGATION,
    FusionGamePhase.EVIDENCE_ROUND_2,
    FusionGamePhase.DISCUSSION,
    FusionGamePhase.VOTING,
    FusionGamePhase.REVELATION,
    FusionGamePhase.ENDED,
)


ALLOWED_ACTIONS = {
    FusionGamePhase.CHARACTER_SELECTION: {"select_character"},
    FusionGamePhase.SCRIPT_READING: {"ready"},
    FusionGamePhase.BACKGROUND: {"advance_phase"},
    FusionGamePhase.INTRODUCTION: {"send_message", "advance_phase"},
    FusionGamePhase.EVIDENCE_ROUND_1: {"search_location", "reveal_evidence", "advance_phase"},
    FusionGamePhase.INVESTIGATION: {"send_message", "ask_question", "reveal_evidence", "advance_phase"},
    FusionGamePhase.EVIDENCE_ROUND_2: {"search_location", "reveal_evidence", "advance_phase"},
    FusionGamePhase.DISCUSSION: {"send_message", "ask_question", "reveal_evidence", "advance_phase"},
    FusionGamePhase.VOTING: {"cast_vote"},
    FusionGamePhase.RUNOFF_VOTING: {"cast_vote"},
    FusionGamePhase.REVELATION: {"advance_phase"},
    FusionGamePhase.ENDED: set(),
}


def require_action_allowed(phase: str | FusionGamePhase, action: str) -> None:
    phase_value = FusionGamePhase(phase)
    if action not in ALLOWED_ACTIONS[phase_value]:
        raise ValueError(f"{action} is not allowed during {phase_value.value}")


def next_phase(phase: str | FusionGamePhase) -> FusionGamePhase:
    phase_value = FusionGamePhase(phase)
    if phase_value == FusionGamePhase.RUNOFF_VOTING:
        return FusionGamePhase.REVELATION
    index = PHASE_ORDER.index(phase_value)
    return PHASE_ORDER[min(index + 1, len(PHASE_ORDER) - 1)]


def resolve_vote(character_ids: Iterable[int]) -> dict:
    counts = Counter(character_ids)
    if not counts:
        return {"status": "PENDING", "leaders": [], "counts": {}}
    max_votes = max(counts.values())
    leaders = sorted(character_id for character_id, count in counts.items() if count == max_votes)
    return {
        "status": "DECIDED" if len(leaders) == 1 else "TIED",
        "leaders": leaders,
        "counts": dict(counts),
    }
