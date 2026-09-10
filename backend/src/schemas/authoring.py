"""Bounded authoring contracts. An automatic result never grants publication."""
from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from src.schemas.script_package import (
    Digest, KnowledgeItem, PackageCharacter, PackageEvidence, PackageModel, PackagePhase,
    References, Settlement, SourcedText, StableId, TruthItem,
    InvestigationMechanics, KnowledgeItemV12, PackageEvidenceV12, ScriptPackageV12, Text,
)


class AuthoringRequest(PackageModel):
    idempotency_key: StableId
    bundle_hash: Digest
    source_ids: list[StableId] = Field(min_length=1, max_length=20)
    title: str = Field(min_length=1, max_length=200, pattern=r"\S")
    content_version: StableId
    player_count: int = Field(ge=2, le=8)

    @model_validator(mode="after")
    def unique_sources(self) -> "AuthoringRequest":
        if len(set(self.source_ids)) != len(self.source_ids):
            raise ValueError("source IDs must be unique")
        return self


class CompileBlocker(PackageModel):
    code: Literal["SOURCE_GAP", "UNSUPPORTED_MECHANIC", "CONTRADICTION"]
    message: str = Field(min_length=1, max_length=1000, pattern=r"\S")
    sources: References


class CompileOutput(PackageModel):
    status: Literal["CANDIDATE", "BLOCKED"]
    package: dict | None
    blockers: list[CompileBlocker] = Field(max_length=100)

    @model_validator(mode="after")
    def valid_branch(self) -> "CompileOutput":
        if self.status == "CANDIDATE":
            if self.package is None or self.blockers:
                raise ValueError("a candidate requires a package and no blockers")
        elif self.package is not None or not self.blockers:
            raise ValueError("a blocked result requires blockers and no package")
        return self


class CompilerContent(PackageModel):
    """Model-authored content only; frozen package metadata is server-owned."""
    introduction: SourcedText
    characters: list[PackageCharacter] = Field(min_length=2, max_length=8)
    initial_phase_id: StableId
    phases: list[PackagePhase] = Field(min_length=1, max_length=100)
    knowledge: list[KnowledgeItem] = Field(min_length=1, max_length=5000)
    evidence: list[PackageEvidence] = Field(min_length=1, max_length=5000)
    truth: list[TruthItem] = Field(min_length=1, max_length=1000)
    settlement: Settlement


class CompilerDraftOutput(PackageModel):
    """Strict wire response; never accepts a complete package from the model."""
    schema_version: Literal["compiler-draft/1.0"]
    status: Literal["CANDIDATE", "BLOCKED"]
    content: CompilerContent | None
    blockers: list[CompileBlocker] = Field(max_length=100)

    @model_validator(mode="after")
    def valid_branch(self) -> "CompilerDraftOutput":
        if self.status == "CANDIDATE":
            if self.content is None or self.blockers:
                raise ValueError("a candidate requires content and no blockers")
        elif self.content is not None or not self.blockers:
            raise ValueError("a blocked result requires blockers and no content")
        return self


class AuthoringRequestV12(AuthoringRequest):
    """An explicit new request; legacy requests never acquire a default field."""
    package_contract: Literal["script-package/1.2"]


class CompilerContentV12(CompilerContent):
    knowledge: list[KnowledgeItemV12] = Field(min_length=1, max_length=5000)
    evidence: list[PackageEvidenceV12] = Field(min_length=1, max_length=5000)
    mechanics: InvestigationMechanics


class CompilerDraftOutputV12(CompilerDraftOutput):
    schema_version: Literal["compiler-draft/1.1"]
    content: CompilerContentV12 | None


class AuthoringRequestV13(AuthoringRequestV12):
    """Explicit prepared rule draft, never an approval or a model-owned field."""
    rule_plan: dict

    @field_validator("rule_plan")
    @classmethod
    def strict_rule_plan(cls, value: dict) -> dict:
        ScriptPackageV12.model_validate(value)
        return value  # Preserve omission/order/values in the frozen input.


class CompilerTextSlot(PackageModel):
    collection: Literal["introduction", "knowledge", "evidence", "truth", "settlement.instructions"]
    id: StableId | None
    text: Text


class AuthoringRequestV14(AuthoringRequestV13):
    compiler_mode: Literal["CONFIRM_FROZEN_TEXT"]


class AuthoringRequestV15(AuthoringRequestV14):
    audit_mode: Literal["TARGET_SOURCE_INDEXES"]


class AuthoringRequestV16(AuthoringRequestV15):
    audit_mode: Literal["BOUNDED_TARGET_SOURCE_INDEXES"]


class AuthoringRequestV17(AuthoringRequestV16):
    audit_mode: Literal["DIRECT_BOUNDED_SOURCE_INDEXES"]


class AuthoringRequestV18(AuthoringRequestV17):
    audit_mode: Literal["STRICT_BOUNDED_SOURCE_INDEXES"]


class AuthoringRequestV19(AuthoringRequestV18):
    audit_mode: Literal["PORTABLE_STRICT_SOURCE_INDEXES"]


class AuthoringRequestV110(AuthoringRequestV19):
    audit_mode: Literal["TYPED_STRICT_SOURCE_INDEXES"]


class AuthoringRequestV111(AuthoringRequestV110):
    audit_mode: Literal["RUNTIME_CONTEXT_SOURCE_INDEXES"]


class AuthoringRequestV112(AuthoringRequestV111):
    audit_mode: Literal["CITATION_CATALOG"]

class CompilerConfirmedSlot(PackageModel):
    collection: Literal["introduction", "knowledge", "evidence", "truth", "settlement.instructions"]
    id: StableId | None


class CompilerTextDraft(PackageModel):
    schema_version: Literal["compiler-text-draft/1.0"]
    status: Literal["CANDIDATE", "BLOCKED"]
    slots: list[CompilerTextSlot] | None = Field(max_length=11002)
    blockers: list[CompileBlocker] = Field(max_length=100)

    @model_validator(mode="after")
    def valid_branch(self) -> "CompilerTextDraft":
        if self.status == "CANDIDATE":
            if not self.slots or self.blockers:
                raise ValueError("a candidate requires text slots and no blockers")
        elif self.slots is not None or not self.blockers:
            raise ValueError("a blocked result requires blockers and no slots")
        return self


class CompilerTextConfirmation(CompilerTextDraft):
    schema_version: Literal["compiler-text-confirmation/1.0"]
    slots: list[CompilerConfirmedSlot] | None = Field(max_length=11002)


def parse_authoring_request(document: Any) -> AuthoringRequest | AuthoringRequestV12 | AuthoringRequestV13 | AuthoringRequestV14 | AuthoringRequestV15 | AuthoringRequestV16 | AuthoringRequestV17 | AuthoringRequestV18 | AuthoringRequestV19 | AuthoringRequestV110 | AuthoringRequestV111 | AuthoringRequestV112:
    """An explicit contract selects the new parser, including invalid values."""
    model = AuthoringRequestV12 if isinstance(document, dict) and "package_contract" in document else AuthoringRequest
    if isinstance(document, dict) and "rule_plan" in document:
        model = AuthoringRequestV13
    if isinstance(document, dict) and "compiler_mode" in document:
        model = AuthoringRequestV14
    if isinstance(document, dict) and "audit_mode" in document:
        model = AuthoringRequestV112 if document["audit_mode"] == "CITATION_CATALOG" else AuthoringRequestV111 if document["audit_mode"] == "RUNTIME_CONTEXT_SOURCE_INDEXES" else AuthoringRequestV110 if document["audit_mode"] == "TYPED_STRICT_SOURCE_INDEXES" else AuthoringRequestV19 if document["audit_mode"] == "PORTABLE_STRICT_SOURCE_INDEXES" else AuthoringRequestV18 if document["audit_mode"] == "STRICT_BOUNDED_SOURCE_INDEXES" else AuthoringRequestV17 if document["audit_mode"] == "DIRECT_BOUNDED_SOURCE_INDEXES" else AuthoringRequestV16 if document["audit_mode"] == "BOUNDED_TARGET_SOURCE_INDEXES" else AuthoringRequestV15
    return model.model_validate(document)
