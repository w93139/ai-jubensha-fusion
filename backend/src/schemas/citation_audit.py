"""Offline candidate wire format; not yet selectable by authoring jobs."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from src.schemas.script_package import PackageModel
from src.schemas.script_review import Category


class CitationFinding(PackageModel):
    category: Category
    severity: Literal['BLOCKER', 'WARNING', 'INFO']
    message: str = Field(min_length=1, max_length=160, pattern=r'\S')
    citation_indexes: list[Annotated[int, Field(ge=0, le=2047)]] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_citations(self):
        if len(set(self.citation_indexes)) != len(self.citation_indexes):
            raise ValueError('duplicate citation index')
        return self


class CitationAuditDraft(PackageModel):
    schema_version: Literal['citation-audit-draft/1.0']
    status: Literal['COMPLETE', 'INCOMPLETE']
    summary: str = Field(min_length=1, max_length=240, pattern=r'\S')
    coverage: list[Category] = Field(min_length=5, max_length=5)
    findings: list[CitationFinding] = Field(max_length=10)

    @model_validator(mode='after')
    def unique_coverage(self):
        if len(set(self.coverage)) != 5:
            raise ValueError('coverage must contain every category exactly once')
        return self
