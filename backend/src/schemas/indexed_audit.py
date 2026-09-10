"""Model wire contract only. Indices never grant new source permissions."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from src.schemas.script_package import PackageModel, StableId
from src.schemas.script_review import Category, FindingTargetV12, ReviewText


class IndexedFinding(PackageModel):
    id: StableId
    category: Category
    severity: Literal["BLOCKER", "WARNING", "INFO"]
    target: FindingTargetV12
    message: ReviewText
    source_indexes: list[Annotated[int, Field(ge=0, le=99)]] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_indexes(self):
        if len(set(self.source_indexes)) != len(self.source_indexes):
            raise ValueError("duplicate source index")
        return self


class IndexedAuditDraft(PackageModel):
    schema_version: Literal["indexed-audit-draft/1.0"]
    summary: ReviewText
    coverage: list[Category] = Field(min_length=5, max_length=5)
    findings: list[IndexedFinding] = Field(max_length=200)

    @model_validator(mode="after")
    def unique_records(self):
        if len(set(self.coverage)) != 5 or len({item.id for item in self.findings}) != len(self.findings):
            raise ValueError("invalid coverage or duplicate finding")
        return self
