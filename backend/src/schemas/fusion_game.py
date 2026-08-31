"""融合版单人剧本杀 API 类型。"""
from typing import Any, Literal

from pydantic import BaseModel, Field


class CreateFusionSessionRequest(BaseModel):
    script_id: int


class SelectCharacterRequest(BaseModel):
    character_id: int
    idempotency_key: str = Field(min_length=8, max_length=100)


class FusionActionRequest(BaseModel):
    type: Literal[
        "ready",
        "advance_phase",
        "send_message",
        "ask_question",
        "search_location",
        "reveal_evidence",
        "cast_vote",
    ]
    idempotency_key: str = Field(min_length=8, max_length=100)
    payload: dict[str, Any] = Field(default_factory=dict)


class GameEventEnvelope(BaseModel):
    event_id: int
    session_id: str
    type: str
    timestamp: str
    payload: dict[str, Any]

