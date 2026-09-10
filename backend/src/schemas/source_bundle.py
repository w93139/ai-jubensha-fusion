"""Private source snapshots are authoring inputs, never player knowledge."""
from typing import Literal

from pydantic import Field, field_validator

from src.schemas.script_package import Digest, PackageModel, SourceFile, StableId


class SourceSelection(PackageModel):
    relative_path: str = Field(min_length=1, max_length=512)
    kind: Literal["original", "ocr", "revised", "supplement", "reference"]
    material_type: Literal["role", "host", "clue", "rules", "source_record", "analysis"]
    original_paths: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("relative_path")
    @classmethod
    def safe_path(cls, value: str) -> str:
        return SourceFile.safe_relative_path(value)

    @field_validator("original_paths")
    @classmethod
    def safe_origins(cls, values: list[str]) -> list[str]:
        return [SourceFile.safe_relative_path(value) for value in values]


class SourcePlan(PackageModel):
    schema_version: Literal["source-plan/1.0"]
    script_key: StableId
    edition: str = Field(min_length=1, max_length=200)
    notes: list[str] = Field(default_factory=list, max_length=30)
    sources: list[SourceSelection] = Field(min_length=1, max_length=1000)

    @field_validator("notes")
    @classmethod
    def bounded_notes(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 500 for value in values):
            raise ValueError("invalid source note")
        return values


class FrozenSource(SourceSelection):
    id: StableId
    sha256: Digest
    size_bytes: int = Field(ge=1, le=16 * 1024 * 1024)
    media_type: Literal["image/jpeg", "image/png", "text/plain", "text/markdown", "application/json", "text/csv"]


class SourceManifest(PackageModel):
    schema_version: Literal["source-bundle/1.0"]
    script_key: StableId
    edition: str = Field(min_length=1, max_length=200)
    notes: list[str] = Field(default_factory=list, max_length=30)
    audience: Literal["AUTHORING_ONLY"]
    sources: list[FrozenSource] = Field(min_length=1, max_length=1000)


class VerifyCandidateSourcesRequest(PackageModel):
    bundle_hash: Digest


class SourceVerificationReport(PackageModel):
    schema_version: Literal["source-verification/1.0"]
    verifier_version: Literal["source-verifier/1.0", "source-verifier/1.1", "source-verifier/1.2", "source-verifier/1.3", "source-verifier/1.4"]
    bundle_hash: Digest
    package_hash: Digest | None
    verified_at: str
    valid: bool
    publication_ready: Literal[False]
    checked_files: int = Field(ge=0, le=1000)
    issues: list[dict] = Field(max_length=1000)
    issues_truncated: bool = False
