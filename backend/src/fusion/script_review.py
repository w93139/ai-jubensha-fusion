"""Private, hash-bound manual audit history. No model calls or publication.

The request-scoped caller owns commit/rollback. File verification occurs before
the write savepoint; only immutable candidate data is read during that wait.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models.script_package import ScriptPackageVersion
from src.db.models.script_review import ScriptAuditRecord, ScriptFindingDisposition
from src.fusion.package_import import PackageConflict, PackageImportService, PackageNotFound
from src.fusion.package_validation import canonical_json, content_hash, parse_package_json, validate_package, validator_version_for
from src.fusion.source_bundles import SourceBundleStore, verifier_version_for
from src.fusion.publication_lock import lock_candidate_version
from src.schemas.script_review import (
    FindingDispositionRequest, ManualAuditReport, SubmitAuditRequest, finding_entity,
    parse_audit_report, parse_submit_audit_request,
)


class ReviewInputError(ValueError):
    """Only fixed public-safe error text."""


def _actor(actor: int) -> None:
    if type(actor) is not int or actor <= 0:
        raise ReviewInputError("审核记录需要有效的提交人")


def _request_hash(target_id: int, request: Any) -> str:
    return content_hash({"target_id": target_id, "request": request.model_dump()})


def _validate_findings(document: dict, report: ManualAuditReport) -> None:
    try:
        parse_audit_report(report.model_dump(), document["schema_version"])
    except ValueError:
        raise ReviewInputError("审核报告版本与候选包不匹配") from None
    for finding in report.findings:
        entity = finding_entity(document, finding.target)
        if entity is None:
            raise ReviewInputError("审核问题指向的实体不在候选包中")
        def locator(ref: dict) -> tuple:
            return (ref["source_id"], ref.get("page"), ref.get("anchor"))
        allowed = {locator(ref) for ref in entity["sources"]}
        if any(locator(ref.model_dump()) not in allowed for ref in finding.sources):
            raise ReviewInputError("审核问题必须引用目标实体已声明且核验过的来源位置")


class ScriptReviewService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.packages = PackageImportService(db)

    @staticmethod
    def _summary(version: dict) -> dict:
        return {key: version[key] for key in ("id", "script_key", "content_version", "package_hash")} | {
            "title": version["package"]["title"], "contract_version": version["package"]["schema_version"],
        }

    def list_candidates(self) -> list[dict]:
        versions = self.db.query(ScriptPackageVersion).order_by(ScriptPackageVersion.id.desc()).limit(100).all()
        return [self._summary(self.packages.get_version(version.id)) for version in versions]

    def get_review(self, version_id: int) -> dict:
        version = self.packages.get_version(version_id)
        records = self.db.query(ScriptAuditRecord).filter_by(version_id=version_id).order_by(ScriptAuditRecord.id.desc()).limit(20).all()
        document = version["package"]
        references = [{"target": {"collection": "introduction", "id": None}, "sources": document["introduction"]["sources"]},
                      {"target": {"collection": "settlement", "id": None}, "sources": document["settlement"]["instructions"]["sources"]}]
        references.extend({"target": {"collection": name, "id": item["id"]}, "sources": item["sources"]}
                          for name in ("characters", "phases", "knowledge", "evidence", "truth") for item in document[name])
        if document["schema_version"] in ("script-package/1.2", "script-package/1.3"):
            references.extend(
                {"target": {"collection": "mechanics." + name, "id": item[key]}, "sources": item["sources"]}
                for name, key in (("actions", "id"), ("phase_budgets", "phase_id"))
                for item in document["mechanics"][name]
            )
        if document["schema_version"] == "script-package/1.3":
            references.extend({"target": {"collection": "memories", "id": item["id"]}, "sources": item["sources"]}
                              for item in document["memories"])
        return {"candidate": self._summary(version), "audits": [self._audit_result(row, version) for row in records],
                "references": references,
                "source_files": [{key: item[key] for key in ("id", "relative_path", "kind")} for item in document["sources"]],
                "publication_ready": False}

    def prepare_audit(self, version_id: int, body: SubmitAuditRequest, actor: int) -> tuple[dict | None, dict]:
        _actor(actor)
        version = self.packages.get_version(version_id)
        if version["package_hash"] != body.expected_package_hash:
            raise PackageConflict("候选版本已变化，请重新读取后提交")
        existing = self._existing_audit(version_id, body, actor)
        if existing is not None:
            return existing, version["package"]
        if not validate_package(version["package"])["valid"]:
            raise ReviewInputError("候选包未通过当前确定性校验")
        _validate_findings(version["package"], body.report)
        return None, version["package"]

    def _existing_audit(self, version_id: int, body: SubmitAuditRequest, actor: int) -> dict | None:
        row = self.db.query(ScriptAuditRecord).filter_by(submitted_by=actor, idempotency_key=body.idempotency_key).one_or_none()
        if row is None:
            return None
        if row.input_hash != _request_hash(version_id, body) or row.version_id != version_id:
            raise PackageConflict("审核幂等键已绑定另一份提交")
        return self._audit_result(row)

    def save_audit(self, version_id: int, body: SubmitAuditRequest, actor: int,
                   verification: dict, store: SourceBundleStore) -> dict:
        existing, document = self.prepare_audit(version_id, body, actor)
        if existing is not None:
            return existing
        # Only consume a report which was actually persisted by this store,
        # never trust an HTTP caller's assertion that files passed verification.
        saved = store.get_report(verification["report_hash"])
        if (saved != verification or not saved["valid"] or saved["issues"] or saved["issues_truncated"]
                or saved["verifier_version"] != verifier_version_for(document)
                or saved["package_hash"] != body.expected_package_hash or saved["bundle_hash"] != body.bundle_hash):
            raise ReviewInputError("当前来源核验未通过，不能登记审核报告")
        lock_candidate_version(self.db, version_id)
        existing, document = self.prepare_audit(version_id, body, actor)
        if existing is not None:
            return existing
        payload = {
            "schema_version": "script-audit-record/1.0", "version_id": version_id,
            "submitted_by": actor, "request": body.model_dump(),
            "source_report_hash": saved["report_hash"], "source_verifier_version": saved["verifier_version"],
            "package_validator_version": validator_version_for(document),
        }
        digest = content_hash(payload)
        try:
            with self.db.begin_nested():
                row = ScriptAuditRecord(
                    version_id=version_id, submitted_by=actor, idempotency_key=body.idempotency_key,
                    input_hash=_request_hash(version_id, body), package_hash=content_hash(document),
                    bundle_hash=body.bundle_hash, source_report_hash=saved["report_hash"],
                    audit_hash=digest, audit_json=canonical_json(payload),
                )
                self.db.add(row)
                self.db.flush()
            return self._audit_result(row)
        except IntegrityError:
            existing = self._existing_audit(version_id, body, actor)
            if existing is not None:
                return existing
            raise PackageConflict("审核记录保存冲突，请刷新后重试") from None

    def _audit_row(self, audit_id: int) -> ScriptAuditRecord:
        row = self.db.get(ScriptAuditRecord, audit_id)
        if row is None:
            raise PackageNotFound("审核记录不存在")
        return row

    def _audit_result(self, row: ScriptAuditRecord, version: dict | None = None) -> dict:
        version = version or self.packages.get_version(row.version_id)
        try:
            raw = parse_package_json(row.audit_json.encode("utf-8"))
            body = parse_submit_audit_request(raw["request"])
            if (raw["schema_version"] != "script-audit-record/1.0" or content_hash(raw) != row.audit_hash
                    or raw["version_id"] != row.version_id or raw["submitted_by"] != row.submitted_by
                    or body.idempotency_key != row.idempotency_key or _request_hash(row.version_id, body) != row.input_hash
                    or body.expected_package_hash != row.package_hash or row.package_hash != version["package_hash"]
                    or body.bundle_hash != row.bundle_hash or raw["source_report_hash"] != row.source_report_hash):
                raise ValueError
            _validate_findings(version["package"], body.report)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise PackageConflict("审核记录完整性检查失败") from None
        records = self.db.query(ScriptFindingDisposition).filter_by(audit_id=row.id).order_by(ScriptFindingDisposition.revision).limit(1001).all()
        if len(records) > 1000:
            raise PackageConflict("审核处理历史超出本阶段限制")
        dispositions = [self._disposition_result(item, row, body.report, index + 1) for index, item in enumerate(records)]
        current = {item["finding_id"]: item["status"] for item in dispositions}
        def is_open(finding: Any) -> bool:
            status = current.get(finding.id, "OPEN")
            return status != "DISMISSED" if finding.severity == "BLOCKER" else status == "OPEN"
        return {"id": row.id, "audit_hash": row.audit_hash, "version_id": row.version_id,
                "package_hash": row.package_hash, "bundle_hash": row.bundle_hash,
                "source_report_hash": row.source_report_hash, "submitted_by": row.submitted_by,
                "report": body.report.model_dump(), "revision": len(dispositions), "dispositions": dispositions,
                "open_blockers": sum(item.severity == "BLOCKER" and is_open(item) for item in body.report.findings),
                "open_warnings": sum(item.severity == "WARNING" and is_open(item) for item in body.report.findings),
                "publication_ready": False}

    @staticmethod
    def _check_disposition(body: FindingDispositionRequest, report: ManualAuditReport) -> None:
        finding = next((item for item in report.findings if item.id == body.finding_id), None)
        if finding is None:
            raise ReviewInputError("处理意见指向的问题不在本审核报告中")
        if finding.severity == "BLOCKER" and body.status == "ACKNOWLEDGED":
            raise ReviewInputError("阻断问题不能仅标记知悉；实际修复应提交新候选，误报需写明排除理由")

    def _disposition_result(self, row: ScriptFindingDisposition, audit: ScriptAuditRecord,
                            report: ManualAuditReport, revision: int) -> dict:
        try:
            raw = parse_package_json(row.disposition_json.encode("utf-8"))
            body = FindingDispositionRequest.model_validate(raw["request"])
            if (content_hash(raw) != row.disposition_hash or raw["audit_id"] != audit.id or row.audit_id != audit.id
                    or raw["submitted_by"] != row.submitted_by or body.idempotency_key != row.idempotency_key
                    or _request_hash(audit.id, body) != row.input_hash or body.expected_audit_hash != audit.audit_hash
                    or body.expected_package_hash != audit.package_hash or body.expected_revision != revision - 1
                    or row.revision != revision or body.status != row.status or body.finding_id != row.finding_id):
                raise ValueError
            self._check_disposition(body, report)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise PackageConflict("审核处理历史完整性检查失败") from None
        return {"id": row.id, "revision": row.revision, "finding_id": row.finding_id,
                "status": row.status, "note": body.note, "submitted_by": row.submitted_by}

    def add_disposition(self, audit_id: int, body: FindingDispositionRequest, actor: int) -> dict:
        _actor(actor)
        audit = self._audit_row(audit_id)
        lock_candidate_version(self.db, audit.version_id)
        result = self._audit_result(audit)
        expected = _request_hash(audit_id, body)
        existing = self.db.query(ScriptFindingDisposition).filter_by(submitted_by=actor, idempotency_key=body.idempotency_key).one_or_none()
        if existing is not None:
            if existing.audit_id != audit_id or existing.input_hash != expected:
                raise PackageConflict("处理幂等键已绑定另一份提交")
            return result
        if (body.expected_package_hash != audit.package_hash or body.expected_audit_hash != audit.audit_hash
                or body.expected_revision != result["revision"]):
            raise PackageConflict("审核记录已有变化，请刷新并核对后提交")
        if result["revision"] >= 1000:
            raise ReviewInputError("审核处理记录已达上限，请提交新的审核报告")
        self._check_disposition(body, parse_audit_report(result["report"]))
        payload = {"audit_id": audit_id, "submitted_by": actor, "request": body.model_dump()}
        try:
            with self.db.begin_nested():
                row = ScriptFindingDisposition(
                    audit_id=audit_id, submitted_by=actor, idempotency_key=body.idempotency_key,
                    input_hash=expected, revision=result["revision"] + 1, finding_id=body.finding_id,
                    status=body.status, disposition_hash=content_hash(payload), disposition_json=canonical_json(payload),
                )
                self.db.add(row)
                self.db.flush()
            return self._audit_result(audit)
        except IntegrityError:
            existing = self.db.query(ScriptFindingDisposition).filter_by(submitted_by=actor, idempotency_key=body.idempotency_key).one_or_none()
            if existing is not None and existing.audit_id == audit_id and existing.input_hash == expected:
                return self._audit_result(audit)
            raise PackageConflict("另一条处理意见已先保存，请刷新后核对") from None
