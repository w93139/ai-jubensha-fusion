"""Compact wire report with an explicit, non-approving completeness claim."""
from typing import Literal
from pydantic import Field
from src.schemas.indexed_audit import IndexedAuditDraft, IndexedFinding


class BoundedFinding(IndexedFinding):
    message: str = Field(min_length=1, max_length=160, pattern=r"\S")


class BoundedAuditDraft(IndexedAuditDraft):
    schema_version: Literal["bounded-audit-draft/1.0"]
    status: Literal["COMPLETE", "INCOMPLETE"]
    summary: str = Field(min_length=1, max_length=240, pattern=r"\S")
    findings: list[BoundedFinding] = Field(max_length=10)
