"""Strict commands for an independently authorized, version-bound text play."""
from typing import Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_serializer, model_validator

from src.schemas.script_package import PackageModel, StableId
from src.schemas.table_decisions import InvestigationVote
from src.schemas.finale_rules import StructuredSubmission


class CreatePackagePlayRequest(PackageModel):
    opening_session_id: str = Field(pattern=r"^package-[0-9a-f]{32}$", min_length=40, max_length=40)
    idempotency_key: StableId


class PackagePlayTarget(PackageModel):
    collection: Literal["knowledge", "evidence"]
    id: StableId


class PackageInvestigationTarget(PackageModel):
    action_id: StableId


class PackagePlayActionRequest(PackageModel):
    model_config = ConfigDict(json_schema_extra={"allOf": [
        {"if": {"properties": {"action": {"const": "SHARE_MATERIAL"}}},
         "then": {"required": ["target"], "properties": {"target": {"type": "object", "required": ["collection", "id"]}}}},
        {"if": {"properties": {"action": {"const": "PERFORM_ACTION"}}},
         "then": {"required": ["target"], "properties": {"target": {"type": "object", "required": ["action_id"]}}}},
        {"if": {"properties": {"action": {"enum": ["ADVANCE_PHASE", "SETTLE"]}}},
         "then": {"not": {"required": ["target"]}}},
    ]})

    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    action: Literal["ADVANCE_PHASE", "SHARE_MATERIAL", "SETTLE", "PERFORM_ACTION"]
    target: PackagePlayTarget | PackageInvestigationTarget | None = None

    @model_validator(mode="after")
    def action_target(self) -> "PackagePlayActionRequest":
        target_type = {"SHARE_MATERIAL": PackagePlayTarget, "PERFORM_ACTION": PackageInvestigationTarget}.get(self.action)
        if ((target_type is None and "target" in self.model_fields_set)
                or (target_type is not None and not isinstance(self.target, target_type))):
            raise ValueError("invalid action target")
        return self

    @model_serializer(mode="wrap")
    def serialize_action(self, handler: Any) -> dict:
        result = handler(self)
        if self.action not in {"SHARE_MATERIAL", "PERFORM_ACTION"}:
            # Do not add an explicitly forbidden null target on roundtrip.
            result.pop("target", None)
        return result


class PackagePlayAskRequest(PackageModel):
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    character_id: StableId
    question: str = Field(min_length=1, max_length=1000, pattern=r"\S",
                          description="One non-blank question, at most 1000 input characters; surrounding whitespace is trimmed before hashing.")

    @field_validator("question")
    @classmethod
    def trim_question(cls, value: str) -> str:
        # Raw length/non-blank checks run first and match the public JSONSchema.
        # Persist/hash the normalized value; do not coerce non-string inputs.
        trimmed = value.strip()
        if not trimmed:
            raise ValueError("question must not be blank")
        return trimmed


class PackagePlaySpeakRequest(PackageModel):
    """Explicit discussion extension; identity and phase are server-owned."""
    schema_version: Literal["package-discussion-command/1.0"]
    action: Literal["SPEAK"]
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    text: str = Field(min_length=1, max_length=1000, pattern=r"\S")

    @field_validator("text")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()


class PackagePlayProposalRequest(PackageModel):
    schema_version: Literal["package-investigation-command/1.0"]
    action: Literal["PROPOSE"]
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    character_id: StableId


class PackagePlayRespondRequest(PackageModel):
    schema_version: Literal["package-dialogue-command/1.0"]
    action: Literal["RESPOND"]
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    character_id: StableId
    reply_to: StableId


class FullPlayChoice(PackageModel):
    choice_id: StableId


class FullPlayPeer(PackageModel):
    peer_character_id: StableId


class FullPlayText(PackageModel):
    text: str = Field(min_length=1, max_length=1000, pattern=r'\S')


class FullPlayActionRequest(PackageModel):
    schema_version: Literal['package-full-play-command/1.0']
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    action: Literal['OPEN_BALLOT', 'CAST_BALLOT', 'BREAK_TIE', 'START_CALL', 'STOP_CALL', 'PRIVATE_SPEAK', 'SEAL_FINALE']
    payload: InvestigationVote | StructuredSubmission | FullPlayChoice | FullPlayPeer | FullPlayText | None = None

    @model_validator(mode='after')
    def payload_shape(self):
        model = {'CAST_BALLOT': InvestigationVote, 'BREAK_TIE': FullPlayChoice, 'START_CALL': FullPlayPeer,
                 'PRIVATE_SPEAK': FullPlayText, 'SEAL_FINALE': StructuredSubmission}.get(self.action)
        if ((model is None and 'payload' in self.model_fields_set)
                or (model is not None and not isinstance(self.payload, model))):
            raise ValueError('FULL_PLAY_PAYLOAD_INVALID')
        return self

    @model_serializer(mode='wrap')
    def serialize_payload(self, handler):
        result = handler(self)
        if self.action in ('OPEN_BALLOT', 'STOP_CALL'):
            result.pop('payload', None)
        return result


class FullPlayDecisionRequest(PackageModel):
    schema_version: Literal['package-table-decision-command/1.0']
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    character_id: StableId
    action: Literal['CAST_BALLOT', 'BREAK_TIE', 'SEAL_FINALE']


class FullPlayPrivateReplyRequest(PackageModel):
    schema_version: Literal['package-private-dialogue-command/1.0']
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    character_id: StableId
    action: Literal['RESPOND_PRIVATE']
    reply_to: StableId


class FullPlayPhoneRequest(PackageModel):
    schema_version: Literal['package-phone-command/1.0']
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    action: Literal['PHONE_STEP']


class FullPlayPhonePauseRequest(PackageModel):
    schema_version: Literal['package-phone-pause-command/1.0']
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    action: Literal['PAUSE_PHONE']


class GuidedHintTarget(PackageModel):
    topic_id: StableId
    level: Literal[1, 2, 3]

    @field_validator('level', mode='before')
    @classmethod
    def strict_level(cls, value):
        if type(value) is not int:
            raise ValueError('GUIDED_HINT_LEVEL_INVALID')
        return value


class GuidedRoundTarget(PackageModel):
    action_ids: list[StableId] = Field(max_length=100)

    @field_validator('action_ids')
    @classmethod
    def unique_actions(cls, value):
        if len(value) != len(set(value)):
            raise ValueError('GUIDED_ROUND_DUPLICATE_ACTION')
        return value


class GuidedPlayRequest(PackageModel):
    schema_version: Literal['package-guided-command/1.0']
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    action: Literal['INVESTIGATE', 'INVESTIGATE_ROUND', 'FINISH_INVESTIGATION', 'PRESENT_REQUIRED', 'REQUEST_HINT']
    payload: PackageInvestigationTarget | GuidedHintTarget | GuidedRoundTarget | None = None

    @model_validator(mode='after')
    def payload_shape(self):
        model = {'INVESTIGATE': PackageInvestigationTarget, 'INVESTIGATE_ROUND': GuidedRoundTarget,
                 'REQUEST_HINT': GuidedHintTarget}.get(self.action)
        if ((model is None and 'payload' in self.model_fields_set)
                or (model is not None and not isinstance(self.payload, model))):
            raise ValueError('GUIDED_PAYLOAD_INVALID')
        return self

    @model_serializer(mode='wrap')
    def serialize_payload(self, handler):
        result = handler(self)
        if self.action not in ('INVESTIGATE', 'INVESTIGATE_ROUND', 'REQUEST_HINT'):
            result.pop('payload', None)
        return result


class ScriptedRetellingRequest(PackageModel):
    schema_version: Literal['package-scripted-retelling-command/1.0']
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    entry_id: StableId


class TopicSelection(PackageModel):
    topic_id: StableId
    character_id: StableId
    intent_id: StableId
    channel: Literal['PUBLIC', 'PRIVATE']


class TopicFallback(PackageModel):
    turn_id: StableId


class TopicCommand(PackageModel):
    schema_version: Literal['package-topic-command/1.0']
    expected_revision: int = Field(ge=0)
    idempotency_key: StableId
    action: Literal['ASK_TOPIC', 'USE_FALLBACK']
    payload: TopicSelection | TopicFallback

    @model_validator(mode='after')
    def payload_shape(self):
        if not isinstance(self.payload, TopicSelection if self.action == 'ASK_TOPIC' else TopicFallback):
            raise ValueError('SINGLE_TOPIC_PAYLOAD_INVALID')
        return self
