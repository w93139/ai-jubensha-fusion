"""Strict candidate-package contract. These models do not grant publication rights.

Do not inherit BaseDataModel: import data must reject extra fields, coercion,
database IDs and caller-supplied approval/status fields.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, field_validator, model_serializer, model_validator

from src.schemas.package_primitives import PackageModel, StableId, Digest, Text, SourceReference, References, normalize_heard_text
from src.schemas.finale_rules import StructuredFinalePlan


CONTRACT_VERSION = "script-package/1.0"
CONTRACT_VERSION_V11 = "script-package/1.1"
CONTRACT_VERSION_V12 = "script-package/1.2"
CONTRACT_VERSION_V13 = "script-package/1.3"
CONTRACT_VERSION_V14 = "script-package/1.4"
class SourceFile(PackageModel):
    id: StableId
    relative_path: str = Field(min_length=1, max_length=512)
    sha256: Digest
    kind: Literal["original", "normalized"]
    media_type: Literal["application/pdf", "text/plain", "text/markdown", "image/png", "image/jpeg"]
    original_source_id: StableId | None = None
    page_count: int | None = Field(default=None, ge=1, le=100000)

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        # A storage-relative locator only: never resolve/read this path here.
        if (any(ord(char) < 32 for char in value) or any(char in value for char in "\\:%?#")
                or any(part in ("", ".", "..") for part in value.split("/"))
                or value.startswith("~")):
            raise ValueError("invalid relative source path")
        return value


class SourcedText(PackageModel):
    text: Text
    sources: References


class PackageCharacter(PackageModel):
    id: StableId
    name: str = Field(min_length=1, max_length=100, pattern=r"\S")
    sources: References


class PackagePhase(PackageModel):
    id: StableId
    title: str = Field(min_length=1, max_length=100, pattern=r"\S")
    next_phase_id: StableId | None
    sources: References


class ReleaseRule(PackageModel):
    """A phase floor plus AND prerequisites; no executable expressions."""

    phase_id: StableId
    required_public_evidence_ids: list[StableId] = Field(default_factory=list, max_length=100)


class KnowledgeItem(PackageModel):
    id: StableId
    text: Text
    kind: Literal["FACT", "CLAIM", "INFERENCE"]
    visibility: Literal["PUBLIC", "CHARACTER_PRIVATE"]
    character_id: StableId | None
    release: ReleaseRule
    disclosure: Literal["PUBLIC", "MAY_SHARE", "MUST_SHARE", "KEEP_PRIVATE"]
    sources: References


class PackageEvidence(PackageModel):
    id: StableId
    text: Text
    visibility: Literal["PUBLIC", "CHARACTER_PRIVATE"]
    character_id: StableId | None
    release: ReleaseRule
    disclosure: Literal["PUBLIC", "MAY_SHARE", "MUST_SHARE", "KEEP_PRIVATE"]
    sources: References


class TruthItem(PackageModel):
    id: StableId
    text: Text
    visibility: Literal["SYSTEM_TRUTH"]
    sources: References


class Settlement(PackageModel):
    phase_id: StableId
    truth_ids: list[StableId] = Field(min_length=1, max_length=1000)
    instructions: SourcedText


class ScriptPackage(PackageModel):
    schema_version: Literal["script-package/1.0"]
    script_key: StableId
    content_version: StableId
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    player_count: int = Field(ge=2, le=8)
    sources: list[SourceFile] = Field(min_length=1, max_length=500)
    introduction: SourcedText
    characters: list[PackageCharacter] = Field(min_length=2, max_length=8)
    initial_phase_id: StableId
    phases: list[PackagePhase] = Field(min_length=1, max_length=100)
    knowledge: list[KnowledgeItem] = Field(min_length=1, max_length=5000)
    evidence: list[PackageEvidence] = Field(min_length=1, max_length=5000)
    truth: list[TruthItem] = Field(min_length=1, max_length=1000)
    settlement: Settlement


class SourceFileV11(PackageModel):
    """Explicit editorial provenance; source declarations never approve content."""

    model_config = ConfigDict(json_schema_extra={"allOf": [
        {"if": {"properties": {"kind": {"const": "normalized"}}},
         "then": {"properties": {"original_source_ids": {"minItems": 1}}},
         "else": {"properties": {"original_source_ids": {"maxItems": 0}}}},
        {"if": {"properties": {"kind": {"const": "supplement"}}},
         "then": {"required": ["provenance_note"], "properties": {"provenance_note": {"type": "string"}}},
         "else": {"not": {"required": ["provenance_note"]}}},
    ]})

    id: StableId
    relative_path: str = Field(min_length=1, max_length=512)
    sha256: Digest
    kind: Literal["original", "normalized", "supplement"]
    media_type: Literal["application/pdf", "text/plain", "text/markdown", "image/png", "image/jpeg"]
    original_source_ids: list[StableId] = Field(max_length=200, json_schema_extra={"uniqueItems": True})
    provenance_note: str | None = Field(default=None, min_length=1, max_length=2000, pattern=r"\S")
    page_count: int | None = Field(default=None, ge=1, le=100000)

    @field_validator("relative_path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        return SourceFile.safe_relative_path(value)

    @model_validator(mode="after")
    def require_explicit_provenance(self) -> SourceFileV11:
        if len(set(self.original_source_ids)) != len(self.original_source_ids):
            raise ValueError("original source IDs must be unique")
        if self.kind == "normalized":
            if not self.original_source_ids:
                raise ValueError("normalized sources require originals")
        elif self.original_source_ids:
            raise ValueError("original and supplement sources cannot declare originals")
        if self.kind == "supplement":
            if self.provenance_note is None:
                raise ValueError("supplement sources require a provenance note")
        elif "provenance_note" in self.model_fields_set:
            raise ValueError("only supplement sources may carry a provenance note")
        return self

    @model_serializer(mode="wrap")
    def serialize_provenance(self, handler: Any) -> dict:
        result = handler(self)
        if self.kind != "supplement":
            # Default serialization must not add a field this contract rejects.
            result.pop("provenance_note", None)
        return result


class ScriptPackageV11(PackageModel):
    # Keep the historical v1 contract independent and unchanged.
    schema_version: Literal["script-package/1.1"]
    script_key: StableId
    content_version: StableId
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    player_count: int = Field(ge=2, le=8)
    sources: list[SourceFileV11] = Field(min_length=1, max_length=500)
    introduction: SourcedText
    characters: list[PackageCharacter] = Field(min_length=2, max_length=8)
    initial_phase_id: StableId
    phases: list[PackagePhase] = Field(min_length=1, max_length=100)
    knowledge: list[KnowledgeItem] = Field(min_length=1, max_length=5000)
    evidence: list[PackageEvidence] = Field(min_length=1, max_length=5000)
    truth: list[TruthItem] = Field(min_length=1, max_length=1000)
    settlement: Settlement


class ReleaseRuleV12(ReleaseRule):
    required_action_ids: list[StableId] = Field(default_factory=list, max_length=100)


class KnowledgeItemV12(KnowledgeItem):
    release: ReleaseRuleV12


class PackageEvidenceV12(PackageEvidence):
    release: ReleaseRuleV12


class PhaseActionBudget(PackageModel):
    phase_id: StableId
    points: int = Field(ge=0, le=1000000)
    advance_policy: Literal["ALLOW_REMAINING", "REQUIRE_EXHAUSTED"]
    origin: Literal["SOURCE_EXPLICIT", "EDITORIAL"]
    sources: References


class InvestigationAction(PackageModel):
    id: StableId
    label: str = Field(min_length=1, max_length=200, pattern=r"\S")
    cost: int = Field(ge=0, le=1000000)
    phase_ids: list[StableId] = Field(min_length=1, max_length=100)
    allowed_character_ids: list[StableId] = Field(min_length=1, max_length=8)
    required_action_ids: list[StableId] = Field(default_factory=list, max_length=100)
    required_public_evidence_ids: list[StableId] = Field(default_factory=list, max_length=100)
    origin: Literal["SOURCE_EXPLICIT", "EDITORIAL"]
    sources: References


class InvestigationMechanics(PackageModel):
    phase_budgets: list[PhaseActionBudget] = Field(min_length=1, max_length=100)
    actions: list[InvestigationAction] = Field(min_length=1, max_length=5000)


class ScriptPackageV12(ScriptPackageV11):
    schema_version: Literal["script-package/1.2"]
    knowledge: list[KnowledgeItemV12] = Field(min_length=1, max_length=5000)
    evidence: list[PackageEvidenceV12] = Field(min_length=1, max_length=5000)
    mechanics: InvestigationMechanics


class SpeechMemoryTrigger(PackageModel):
    kind: Literal["OTHER_PUBLIC_SPEECH"]
    keyword: str = Field(min_length=1, max_length=100, pattern=r"\S")

    @field_validator("keyword")
    @classmethod
    def literal_keyword(cls, value):
        if value != value.strip() or any(ord(char) < 32 for char in value):
            raise ValueError("invalid literal keyword")
        return value


class EvidenceMemoryTrigger(PackageModel):
    kind: Literal["ACQUIRED_EVIDENCE"]
    evidence_id: StableId


MemoryTrigger = Annotated[SpeechMemoryTrigger | EvidenceMemoryTrigger, Field(discriminator="kind")]


class PrivateMemory(PackageModel):
    id: StableId
    character_id: StableId
    phase_id: StableId
    title: str = Field(min_length=1, max_length=100, pattern=r"\S")
    text: Text
    kind: Literal["FACT", "CLAIM", "INFERENCE"]
    card_disclosure: Literal["KEEP_PRIVATE"]
    retelling: Literal["MAY_RETELL", "MUST_RETELL"]
    triggers: list[MemoryTrigger] = Field(min_length=1, max_length=30)
    origin: Literal["SOURCE_EXPLICIT", "EDITORIAL"]
    sources: References


class ScriptPackageV13(ScriptPackageV12):
    schema_version: Literal["script-package/1.3"]
    memories: list[PrivateMemory] = Field(min_length=1, max_length=1000)


class HeardMemoryTrigger(PackageModel):
    kind: Literal['OTHER_HEARD_SPEECH']
    keywords: list[str] = Field(min_length=1, max_length=20)

    @field_validator('keywords')
    @classmethod
    def finite_keywords(cls, value):
        normalized = [normalize_heard_text(x) for x in value]
        if (len(set(normalized)) != len(value) or any(not x for x in normalized)
                or any(not 1 <= len(x) <= 100 or x != x.strip()
                or any(ord(c) < 32 for c in x) for x in value)):
            raise ValueError('invalid heard keywords')
        return value


class PrivateMemoryV14(PrivateMemory):
    triggers: list[Annotated[HeardMemoryTrigger | EvidenceMemoryTrigger, Field(discriminator='kind')]] = Field(min_length=1, max_length=30)


class FullPlayPhase(PackageModel):
    phase_id: StableId
    kind: Literal['READING', 'INVESTIGATION', 'FINALE']
    sources: References


class FullPlayActionOrder(PackageModel):
    action_id: StableId
    order: int = Field(ge=0, le=10000)


class FullPlayMechanics(PackageModel):
    phases: list[FullPlayPhase] = Field(min_length=3, max_length=100)
    action_order: list[FullPlayActionOrder] = Field(min_length=1, max_length=5000)
    finale: StructuredFinalePlan
    sources: References


class KnowledgeItemV14(KnowledgeItemV12):
    retelling: Literal['MAY_RETELL', 'MUST_RETELL'] | None = None

    @model_validator(mode='after')
    def book_retelling_scope(self):
        if self.retelling is not None and (self.visibility != 'CHARACTER_PRIVATE' or self.disclosure != 'KEEP_PRIVATE'):
            raise ValueError('FULL_PLAY_RETELLING_PERMISSION_INVALID')
        return self


class PackageVisual(PackageModel):
    id: StableId
    collection: Literal['knowledge', 'evidence', 'memory']
    material_id: StableId
    source_id: StableId
    label: str = Field(min_length=1, max_length=100, pattern=r'\S')
    # Permission applies to the entire original file, never an implicit crop.
    exposure: Literal['WHOLE_ORIGINAL_IMAGE']


class ScriptPackageV14(ScriptPackageV13):
    schema_version: Literal['script-package/1.4']
    memories: list[PrivateMemoryV14] = Field(min_length=1, max_length=1000)
    knowledge: list[KnowledgeItemV14] = Field(min_length=1, max_length=10000)
    full_play: FullPlayMechanics
    visuals: list[PackageVisual] = Field(default_factory=list, max_length=1000)


def parse_script_package(document: Any) -> ScriptPackage | ScriptPackageV11 | ScriptPackageV12 | ScriptPackageV13 | ScriptPackageV14:
    """Dispatch strict contracts without rewriting raw values or old hashes."""
    models = {CONTRACT_VERSION: ScriptPackage, CONTRACT_VERSION_V11: ScriptPackageV11,
              CONTRACT_VERSION_V12: ScriptPackageV12, CONTRACT_VERSION_V13: ScriptPackageV13, CONTRACT_VERSION_V14: ScriptPackageV14}
    version = document.get("schema_version") if isinstance(document, dict) else None
    model = models.get(version, ScriptPackage) if type(version) is str else ScriptPackage
    return model.model_validate(document)


def package_json_schema(version: str = CONTRACT_VERSION_V11) -> dict:
    models = {CONTRACT_VERSION: ScriptPackage, CONTRACT_VERSION_V11: ScriptPackageV11,
              CONTRACT_VERSION_V12: ScriptPackageV12, CONTRACT_VERSION_V13: ScriptPackageV13, CONTRACT_VERSION_V14: ScriptPackageV14}
    if type(version) is not str or version not in models:
        raise ValueError("unsupported package contract")
    return models[version].model_json_schema()


class ImportPackageRequest(PackageModel):
    idempotency_key: StableId
    # Keep the candidate raw so invalid schema/permissions become a persisted,
    # sanitized BLOCKED report, rather than FastAPI echoing private input.
    package: dict
