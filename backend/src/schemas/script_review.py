"""Strict manual audit records; these inputs never grant publication rights."""
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, model_validator

from src.schemas.script_package import Digest, PackageModel, References, StableId


Category = Literal["PROVENANCE", "TIMELINE", "EVIDENCE", "KNOWLEDGE_BOUNDARY", "PLAYABILITY"]
ReviewText = Annotated[str, StringConstraints(min_length=1, max_length=4000, pattern=r"\S")]


class FindingTarget(PackageModel):
    collection: Literal["introduction", "settlement", "characters", "phases", "knowledge", "evidence", "truth"]
    id: StableId | None

    @model_validator(mode="after")
    def check_identifier(self) -> "FindingTarget":
        if (self.collection in ("introduction", "settlement")) != (self.id is None):
            raise ValueError("invalid finding target")
        return self


class AuditFinding(PackageModel):
    id: StableId
    category: Category
    severity: Literal["BLOCKER", "WARNING", "INFO"]
    target: FindingTarget
    message: ReviewText
    sources: References


class ManualAuditReport(PackageModel):
    schema_version: Literal["script-audit/1.0"]
    summary: ReviewText
    coverage: list[Category] = Field(min_length=1, max_length=5)
    findings: list[AuditFinding] = Field(max_length=200)

    @model_validator(mode="after")
    def unique_records(self) -> "ManualAuditReport":
        if (len(set(self.coverage)) != len(self.coverage)
                or len({item.id for item in self.findings}) != len(self.findings)
                or any(item.category not in self.coverage for item in self.findings)):
            raise ValueError("invalid audit coverage or finding IDs")
        return self


class SubmitAuditRequest(PackageModel):
    idempotency_key: StableId
    expected_package_hash: Digest
    bundle_hash: Digest
    report: ManualAuditReport


class FindingDispositionRequest(PackageModel):
    idempotency_key: StableId
    expected_package_hash: Digest
    expected_audit_hash: Digest
    expected_revision: int = Field(ge=0, le=1000000)
    finding_id: StableId
    status: Literal["OPEN", "ACKNOWLEDGED", "DISMISSED"]
    note: ReviewText


class FindingTargetV12(FindingTarget):
    collection: Literal["introduction", "settlement", "characters", "phases", "knowledge", "evidence", "truth",
                        "mechanics.actions", "mechanics.phase_budgets"]


class AuditFindingV12(AuditFinding):
    target: FindingTargetV12


class ManualAuditReportV12(ManualAuditReport):
    schema_version: Literal["script-audit/1.1"]
    findings: list[AuditFindingV12] = Field(max_length=200)


class SubmitAuditRequestV12(SubmitAuditRequest):
    report: ManualAuditReportV12


class FindingTargetV13(FindingTargetV12):
    collection: Literal["introduction", "settlement", "characters", "phases", "knowledge", "evidence", "truth",
                        "mechanics.actions", "mechanics.phase_budgets", "memories"]


class AuditFindingV13(AuditFindingV12):
    target: FindingTargetV13


class ManualAuditReportV13(ManualAuditReportV12):
    schema_version: Literal["script-audit/1.2"]
    findings: list[AuditFindingV13] = Field(max_length=200)


class SubmitAuditRequestV13(SubmitAuditRequest):
    report: ManualAuditReportV13


def parse_audit_report(document: Any, package_contract: str | None = None) -> ManualAuditReport | ManualAuditReportV12:
    """Keep report versions separate; a 1.2 candidate requires the new report."""
    version = document.get("schema_version") if isinstance(document, dict) else None
    if package_contract is not None:
        expected = {"script-package/1.0": "script-audit/1.0", "script-package/1.1": "script-audit/1.0",
                    "script-package/1.2": "script-audit/1.1", "script-package/1.3": "script-audit/1.2"}.get(package_contract)
        if expected is None or version != expected:
            raise ValueError("audit contract does not match package")
    model = {"script-audit/1.1": ManualAuditReportV12, "script-audit/1.2": ManualAuditReportV13}.get(version, ManualAuditReport)
    return model.model_validate(document)


def parse_submit_audit_request(document: Any) -> SubmitAuditRequest | SubmitAuditRequestV12:
    report = document.get("report") if isinstance(document, dict) else None
    version = report.get("schema_version") if isinstance(report, dict) else None
    model = {"script-audit/1.1": SubmitAuditRequestV12, "script-audit/1.2": SubmitAuditRequestV13}.get(version, SubmitAuditRequest)
    return model.model_validate(document)


def finding_entity(document: dict, target: FindingTarget | FindingTargetV12 | dict) -> dict | None:
    """Resolve only a declared target, with phase_id identifying a budget row."""
    if not isinstance(document, dict):
        return None
    model = {"script-package/1.2": FindingTargetV12, "script-package/1.3": FindingTargetV13}.get(document.get("schema_version"), FindingTarget)
    try:
        parsed = model.model_validate(target.model_dump() if isinstance(target, FindingTarget) else target)
    except (ValueError, TypeError):
        return None
    if parsed.collection == "introduction":
        result = document.get("introduction")
    elif parsed.collection == "settlement":
        settlement = document.get("settlement")
        result = settlement.get("instructions") if isinstance(settlement, dict) else None
    else:
        key = "id"
        if parsed.collection.startswith("mechanics."):
            mechanics = document.get("mechanics")
            collection = parsed.collection.split(".")[1]
            rows = mechanics.get(collection) if isinstance(mechanics, dict) else None
            if collection == "phase_budgets":
                key = "phase_id"
        else:
            rows = document.get(parsed.collection)
        if not isinstance(rows, list):
            return None
        matches = [item for item in rows if isinstance(item, dict) and item.get(key) == parsed.id]
        result = matches[0] if len(matches) == 1 else None
    return result if isinstance(result, dict) else None
