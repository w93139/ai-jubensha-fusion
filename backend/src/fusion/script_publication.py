"""Human-only release gates over immutable candidates and complete review history.

The caller owns commit/rollback. Source I/O precedes the shared candidate lock;
the locked section recomputes the full database basis before appending a record.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models.authoring_job import AuthoringAttempt, AuthoringJob
from src.db.models.script_package import ScriptPackageVersion
from src.db.models.script_publication import ScriptPackageRelease, ScriptPublicationApproval
from src.db.models.script_review import ScriptAuditRecord, ScriptFindingDisposition
from src.db.models.user import User
from src.fusion.authoring_jobs import AuthoringJobStore
from src.fusion.authoring_model import AUDIT_CATEGORIES, parse_compiler_output, validate_model_audit
from src.fusion.package_import import PackageImportService, PackageNotFound
from src.fusion.package_validation import CANONICAL_VERSION, canonical_json, content_hash, parse_package_json, validate_package, validator_version_for
from src.fusion.publication_lock import lock_candidate_version
from src.fusion.script_review import ScriptReviewService
from src.fusion.source_bundles import SourceBundleStore, verifier_version_for
from src.schemas.script_publication import ApprovePublicationRequest, PublishPackageRequest


PUBLICATION_POLICY_VERSION = "script-publication-policy/1.0"
MAX_REPORTS = 100
MAX_JOBS = 100
MAX_DISPOSITIONS = 1000
MAX_HISTORY_BYTES = 4 * 1024 * 1024


class PublicationError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class PublicationConflict(PublicationError):
    pass


def _request_hash(version_id: int, body: Any) -> str:
    return content_hash({"version_id": version_id, "request": body.model_dump()})


def _check(code: str, passed: bool, message: str) -> dict:
    return {"code": code, "passed": bool(passed), "message": message}


def _timestamp(row: Any) -> str:
    return row.created_at.isoformat() + "Z"


class ScriptPublicationService:
    def __init__(self, db: Session, source_store: SourceBundleStore | None = None):
        self.db = db
        self.source_store = source_store
        self.packages = PackageImportService(db)

    def _version(self, version_id: int) -> dict:
        self.db.get(ScriptPackageVersion, version_id, populate_existing=True)
        return self.packages.get_version(version_id)

    def _actor(self, actor: int) -> None:
        user = self.db.get(User, actor, populate_existing=True) if type(actor) is int and actor > 0 else None
        if user is None or user.is_admin is not True or user.is_active is not True:
            raise PublicationError("PUBLICATION_ADMIN_REQUIRED")

    def _collect(self, version_id: int, bundle_hash: str | None = None) -> dict:
        if type(version_id) is not int or version_id <= 0:
            raise PackageNotFound("候选版本不存在")
        version = self._version(version_id)
        document = version["package"]
        audits = self.db.query(ScriptAuditRecord).filter_by(version_id=version_id).order_by(ScriptAuditRecord.id).populate_existing().limit(MAX_REPORTS + 1).all()
        jobs = self.db.query(AuthoringJob).filter_by(candidate_version_id=version_id).order_by(AuthoringJob.id).populate_existing().limit(MAX_JOBS + 1).all()
        if len(audits) > MAX_REPORTS or len(jobs) > MAX_JOBS:
            raise PublicationError("PUBLICATION_REVIEW_LIMIT_EXCEEDED")
        histories = self.db.query(ScriptFindingDisposition).filter(
            ScriptFindingDisposition.audit_id.in_([row.id for row in audits])).order_by(ScriptFindingDisposition.audit_id, ScriptFindingDisposition.revision).populate_existing().limit(MAX_DISPOSITIONS + 1).all() if audits else []
        attempts = self.db.query(AuthoringAttempt).filter(AuthoringAttempt.job_id.in_([row.id for row in jobs])).order_by(AuthoringAttempt.id).populate_existing().limit(MAX_JOBS * 2 + 1).all() if jobs else []
        if len(histories) > MAX_DISPOSITIONS or len(attempts) > MAX_JOBS * 2:
            raise PublicationError("PUBLICATION_REVIEW_LIMIT_EXCEEDED")
        size = sum(len(row.audit_json.encode()) for row in audits) + sum(len(row.disposition_json.encode()) for row in histories)
        size += sum(len((row.output_json or "").encode()) + len((row.receipt_json or "").encode()) for row in attempts)
        if size > MAX_HISTORY_BYTES:
            raise PublicationError("PUBLICATION_REVIEW_LIMIT_EXCEEDED")
        manual, models, scope, source_reports = [], [], set(), set()
        integrity = True
        reviews = ScriptReviewService(self.db)
        for row in audits:
            scope.add(row.bundle_hash)
            source_reports.add((row.source_report_hash, row.bundle_hash))
            try:
                manual.append(reviews._audit_result(row, version))
            except (ValueError, KeyError, TypeError):
                integrity = False
        store = AuthoringJobStore(lambda: self.db)
        settled = True
        for job in jobs:
            entry = {"job_id": job.id, "state": job.state, "attempt_id": None, "status": "MISSING",
                     "output_hash": None, "receipt_hash": None, "report": None, "error_code": None}
            try:
                result = store._result(self.db, store._job(self.db, job.id))
                inputs = store._inputs(job)
                scope.add(result["bundle_hash"])
                if job.source_report_hash is None:
                    integrity = False
                else:
                    source_reports.add((job.source_report_hash, result["bundle_hash"]))
                compiled = next((item for item in result["attempts"] if item["step"] == "COMPILE"), None)
                if (compiled is None or compiled["status"] != "SUCCEEDED"
                        or parse_compiler_output(compiled["output"], inputs["context"])["package"] != document):
                    integrity = False
                if (job.state in {"QUEUED", "RUNNING", "NEEDS_RECONCILIATION"}
                        or any(item["status"] in {"RESERVED", "IN_FLIGHT", "UNKNOWN"} for item in result["attempts"])):
                    settled = False
                audited = next((item for item in result["attempts"] if item["step"] == "AUDIT"), None)
                if audited is not None:
                    raw_attempt = next(row for row in attempts if row.id == audited["id"])
                    entry.update(attempt_id=audited["id"], status=audited["status"], output_hash=audited["output_hash"],
                                 receipt_hash=raw_attempt.receipt_hash, error_code=audited["error_code"])
                    if audited["status"] == "SUCCEEDED":
                        entry["report"] = validate_model_audit(audited["output"], document)
            except (ValueError, KeyError, TypeError, StopIteration):
                integrity = False
            models.append(entry)
        selected = next(iter(scope)) if len(scope) == 1 else (bundle_hash if not scope else None)
        unambiguous = selected is not None and len(scope) <= 1 and (bundle_hash is None or bundle_hash == selected)
        required = {(item["job_id"], finding["id"]): finding["severity"] for item in models if item["report"]
                    for finding in item["report"]["findings"] if finding["severity"] in {"BLOCKER", "WARNING"}}
        basis = {"policy_version": PUBLICATION_POLICY_VERSION, "validator_version": validator_version_for(document),
                 "verifier_version": verifier_version_for(document), "canonical_version": CANONICAL_VERSION,
                 "version_id": version_id, "package_hash": version["package_hash"], "manifest_hash": version["manifest_hash"],
                 "bundle_hash": selected,
                 "manual": [{"id": row.id, "audit_hash": row.audit_hash, "input_hash": row.input_hash,
                             "source_report_hash": row.source_report_hash,
                             "dispositions": [{"id": item.id, "revision": item.revision, "hash": item.disposition_hash}
                                              for item in histories if item.audit_id == row.id]} for row in audits],
                 "jobs": [{"id": row.id, "revision": row.revision, "state_hash": row.state_hash, "input_hash": row.input_hash,
                           "source_report_hash": row.source_report_hash,
                           "attempts": [{"id": item.id, "state_hash": item.state_hash, "prepared_hash": item.prepared_hash,
                                         "output_hash": item.output_hash, "receipt_hash": item.receipt_hash}
                                        for item in attempts if item.job_id == row.id]} for row in jobs]}
        checks = [
            _check("CANDIDATE_VALID", validate_package(document)["valid"], "候选通过当前确定性校验"),
            _check("REVIEW_INTEGRITY_VALID", integrity, "全部报告、处置与模型任务的绑定完整"),
            _check("MANUAL_AUDIT_COMPLETE", any(set(item["report"]["coverage"]) == AUDIT_CATEGORIES for item in manual), "至少一份人工报告完整覆盖五个维度"),
            _check("MANUAL_BLOCKERS_CLOSED", all(item["open_blockers"] == 0 for item in manual), "全部人工阻断问题已附理由排除"),
            _check("MANUAL_WARNINGS_REVIEWED", all(item["open_warnings"] == 0 for item in manual), "全部人工警告已确认知悉或附理由排除"),
            _check("MODEL_AUDIT_COMPLETE", any(item["report"] is not None for item in models), "至少一份模型审核完整且有效"),
            _check("AUTHORING_TASKS_SETTLED", settled, "关联任务没有待完成调用或未知用量"),
            _check("MODEL_FINDING_LIMIT", len(required) <= MAX_DISPOSITIONS, "待处理模型问题处于本阶段数量上限内"),
            _check("SOURCE_SCOPE_VALID", unambiguous, "候选仅绑定一个明确来源快照"),
        ]
        return {"version": version, "basis_hash": content_hash(basis), "bundle_hash": selected,
                "manual_reports": manual, "model_reports": models, "checks": checks,
                "required": required, "source_reports": source_reports}

    def _sources(self, state: dict, store: SourceBundleStore, *, persist: bool) -> dict | None:
        if not next(item["passed"] for item in state["checks"] if item["code"] == "SOURCE_SCOPE_VALID"):
            return None
        try:
            package_hash = state["version"]["package_hash"]
            for digest, bundle in state["source_reports"]:
                old = store.get_report(digest)
                if (not old["valid"] or old["issues"] or old["issues_truncated"]
                        or old["bundle_hash"] != bundle or old["package_hash"] != package_hash):
                    return None
            result = store.verify(state["bundle_hash"], document=state["version"]["package"], persist=persist)
            if (not result["valid"] or result["issues"] or result["issues_truncated"]
                    or result["package_hash"] != package_hash or result["bundle_hash"] != state["bundle_hash"]
                    or result["verifier_version"] != verifier_version_for(state["version"]["package"])):
                return None
            if persist and store.get_report(result["report_hash"]) != result:
                return None
            return result
        except (ValueError, OSError, KeyError, TypeError):
            return None

    def _approval(self, row: ScriptPublicationApproval) -> dict:
        try:
            raw = parse_package_json(row.approval_json.encode())
            request = ApprovePublicationRequest.model_validate(raw["request"])
            if (set(raw) != {"schema_version", "policy_version", "version_id", "submitted_by", "request", "package_hash", "bundle_hash", "basis_hash", "source_report_hash"}
                    or raw["schema_version"] != "script-publication-approval/1.0" or content_hash(raw) != row.approval_hash
                    or type(raw["policy_version"]) is not str
                    or any(raw[key] != getattr(row, key) for key in ("version_id", "submitted_by", "package_hash", "bundle_hash", "basis_hash", "source_report_hash"))
                    or request.idempotency_key != row.idempotency_key or _request_hash(row.version_id, request) != row.input_hash
                    or request.expected_package_hash != row.package_hash or request.expected_basis_hash != row.basis_hash
                    or request.bundle_hash != row.bundle_hash):
                raise ValueError
            version = self._version(row.version_id)
            if version["package_hash"] != row.package_hash:
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise PublicationConflict("PUBLICATION_APPROVAL_INTEGRITY_FAILED") from None
        return {"id": row.id, "version_id": row.version_id, "approval_hash": row.approval_hash,
                **{key: getattr(row, key) for key in ("package_hash", "bundle_hash", "basis_hash", "source_report_hash", "submitted_by")},
                "note": request.note, "model_dispositions": [item.model_dump() for item in request.model_dispositions],
                "policy_version": raw["policy_version"], "created_at": _timestamp(row)}

    @staticmethod
    def _decisions(state: dict, decisions: list) -> bool:
        supplied = {(item["job_id"], item["finding_id"]): item for item in decisions}
        return (len(supplied) == len(decisions) and set(supplied) == set(state["required"])
                and all(severity != "BLOCKER" or supplied[key]["status"] == "DISMISSED"
                        for key, severity in state["required"].items()))

    def _latest_approval(self, version_id: int) -> dict | None:
        row = self.db.query(ScriptPublicationApproval).filter_by(version_id=version_id).order_by(ScriptPublicationApproval.id.desc()).populate_existing().first()
        return self._approval(row) if row else None

    def _approval_valid(self, approval: dict | None, state: dict, sources_valid: bool) -> bool:
        return bool(approval and sources_valid and all(item["passed"] for item in state["checks"])
                    and approval["basis_hash"] == state["basis_hash"] and approval["bundle_hash"] == state["bundle_hash"]
                    and approval["package_hash"] == state["version"]["package_hash"]
                    and approval["policy_version"] == PUBLICATION_POLICY_VERSION
                    and self._decisions(state, approval["model_dispositions"]))

    def _release(self, row: ScriptPackageRelease) -> dict:
        try:
            raw = parse_package_json(row.release_json.encode())
            request = PublishPackageRequest.model_validate(raw["request"])
            approval_row = self.db.get(ScriptPublicationApproval, row.approval_id, populate_existing=True)
            if approval_row is None:
                raise ValueError
            approval = self._approval(approval_row)
            if (set(raw) != {"schema_version", "version_id", "submitted_by", "request", "approval_hash", "package_hash", "bundle_hash", "basis_hash", "source_report_hash"}
                    or raw["schema_version"] != "script-package-release/1.0" or content_hash(raw) != row.release_hash
                    or any(raw[key] != getattr(row, key) for key in ("version_id", "submitted_by", "package_hash", "bundle_hash", "basis_hash", "source_report_hash"))
                    or request.idempotency_key != row.idempotency_key or _request_hash(row.version_id, request) != row.input_hash
                    or request.approval_id != row.approval_id or request.expected_approval_hash != approval["approval_hash"]
                    or raw["approval_hash"] != approval["approval_hash"] or request.expected_basis_hash != row.basis_hash
                    or any(approval[key] != getattr(row, key) for key in ("version_id", "package_hash", "bundle_hash", "basis_hash"))):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise PublicationConflict("PUBLICATION_RELEASE_INTEGRITY_FAILED") from None
        return {"id": row.id, "version_id": row.version_id, "release_hash": row.release_hash, "approval_id": row.approval_id,
                "approval_hash": approval["approval_hash"], "created_at": _timestamp(row),
                **{key: getattr(row, key) for key in ("package_hash", "bundle_hash", "basis_hash", "source_report_hash", "submitted_by")}}

    def gate_state(self, version_id: int, store: SourceBundleStore, bundle_hash: str | None = None) -> dict:
        state = self._collect(version_id, bundle_hash)
        source_ok = self._sources(state, store, persist=False) is not None
        approval = self._latest_approval(version_id)
        if approval is not None:
            approval["valid"] = self._approval_valid(approval, state, source_ok)
        row = self.db.query(ScriptPackageRelease).filter_by(version_id=version_id).populate_existing().one_or_none()
        release = self._release(row) if row else None
        if release:
            release["current_approval_valid"] = bool(approval and approval["valid"] and approval["id"] == release["approval_id"])
        version = state["version"]
        checks = state["checks"] + [_check("SOURCE_VERIFIED", source_ok, "当前来源文件、定位与历史核验记录完整")]
        return {"basis_hash": state["basis_hash"], "bundle_hash": state["bundle_hash"],
                "candidate": {key: version[key] for key in ("id", "script_key", "content_version", "package_hash", "manifest_hash")} |
                             {"title": version["package"]["title"], "contract_version": version["package"]["schema_version"], "player_count": version["package"]["player_count"]},
                "manual_reports": state["manual_reports"], "model_reports": state["model_reports"], "checks": checks,
                "can_approve": release is None and all(item["passed"] for item in checks),
                "can_publish": bool(approval and approval["valid"] and release is None), "approval": approval, "release": release}

    def _existing(self, model: Any, version_id: int, body: Any, actor: int):
        row = self.db.query(model).filter_by(submitted_by=actor, idempotency_key=body.idempotency_key).populate_existing().one_or_none()
        if row is not None and (row.version_id != version_id or row.input_hash != _request_hash(version_id, body)):
            raise PublicationConflict("PUBLICATION_IDEMPOTENCY_CONFLICT")
        return row

    def approve(self, version_id: int, body: ApprovePublicationRequest, actor: int, store: SourceBundleStore) -> dict:
        self._actor(actor)
        body = ApprovePublicationRequest.model_validate(body.model_dump())
        existing = self._existing(ScriptPublicationApproval, version_id, body, actor)
        if existing is not None:
            return self._approval(existing)
        if self.db.query(ScriptPackageRelease).filter_by(version_id=version_id).first() is not None:
            raise PublicationConflict("PUBLICATION_VERSION_ALREADY_RELEASED")
        state = self._collect(version_id, body.bundle_hash)
        if (state["basis_hash"] != body.expected_basis_hash or state["version"]["package_hash"] != body.expected_package_hash):
            raise PublicationConflict("PUBLICATION_BASIS_CHANGED")
        if not all(item["passed"] for item in state["checks"]):
            raise PublicationConflict("PUBLICATION_GATE_BLOCKED")
        if not self._decisions(state, [item.model_dump() for item in body.model_dispositions]):
            raise PublicationError("PUBLICATION_MODEL_FINDINGS_UNRESOLVED")
        verified = self._sources(state, store, persist=True)
        if verified is None:
            raise PublicationConflict("PUBLICATION_SOURCE_UNVERIFIED")
        # Acquire before SAVEPOINT so SQLite legacy mode has a real outer
        # transaction; a caller rollback must also roll back this append.
        lock_candidate_version(self.db, version_id)
        try:
            with self.db.begin_nested():
                existing = self._existing(ScriptPublicationApproval, version_id, body, actor)
                if existing is not None:
                    return self._approval(existing)
                if self.db.query(ScriptPackageRelease).filter_by(version_id=version_id).first() is not None:
                    raise PublicationConflict("PUBLICATION_VERSION_ALREADY_RELEASED")
                current = self._collect(version_id, body.bundle_hash)
                if current["basis_hash"] != state["basis_hash"] or not all(item["passed"] for item in current["checks"]):
                    raise PublicationConflict("PUBLICATION_BASIS_CHANGED")
                payload = {"schema_version": "script-publication-approval/1.0", "policy_version": PUBLICATION_POLICY_VERSION,
                           "version_id": version_id, "submitted_by": actor, "request": body.model_dump(),
                           "package_hash": body.expected_package_hash, "bundle_hash": body.bundle_hash,
                           "basis_hash": body.expected_basis_hash, "source_report_hash": verified["report_hash"]}
                row = ScriptPublicationApproval(version_id=version_id, submitted_by=actor, idempotency_key=body.idempotency_key,
                    input_hash=_request_hash(version_id, body), package_hash=body.expected_package_hash, bundle_hash=body.bundle_hash,
                    basis_hash=body.expected_basis_hash, source_report_hash=verified["report_hash"],
                    approval_hash=content_hash(payload), approval_json=canonical_json(payload))
                self.db.add(row)
                self.db.flush()
                return self._approval(row)
        except IntegrityError:
            existing = self._existing(ScriptPublicationApproval, version_id, body, actor)
            if existing is not None:
                return self._approval(existing)
            raise PublicationConflict("PUBLICATION_WRITE_CONFLICT") from None

    def publish(self, version_id: int, body: PublishPackageRequest, actor: int, store: SourceBundleStore) -> dict:
        self._actor(actor)
        body = PublishPackageRequest.model_validate(body.model_dump())
        existing = self._existing(ScriptPackageRelease, version_id, body, actor)
        if existing is not None:
            return self._release(existing)
        state = self._collect(version_id)
        approval = self._latest_approval(version_id)
        if (not approval or approval["id"] != body.approval_id or approval["approval_hash"] != body.expected_approval_hash
                or state["basis_hash"] != body.expected_basis_hash or not self._approval_valid(approval, state, True)):
            raise PublicationConflict("PUBLICATION_APPROVAL_EXPIRED")
        verified = self._sources(state, store, persist=True)
        if verified is None:
            raise PublicationConflict("PUBLICATION_SOURCE_UNVERIFIED")
        lock_candidate_version(self.db, version_id)
        try:
            with self.db.begin_nested():
                existing = self._existing(ScriptPackageRelease, version_id, body, actor)
                if existing is not None:
                    return self._release(existing)
                current = self._collect(version_id)
                latest = self._latest_approval(version_id)
                if (current["basis_hash"] != state["basis_hash"] or latest != approval
                        or not self._approval_valid(latest, current, True)):
                    raise PublicationConflict("PUBLICATION_APPROVAL_EXPIRED")
                if self.db.query(ScriptPackageRelease).filter_by(version_id=version_id).first() is not None:
                    raise PublicationConflict("PUBLICATION_VERSION_ALREADY_RELEASED")
                payload = {"schema_version": "script-package-release/1.0", "version_id": version_id, "submitted_by": actor,
                           "request": body.model_dump(), "approval_hash": approval["approval_hash"],
                           "package_hash": approval["package_hash"], "bundle_hash": approval["bundle_hash"],
                           "basis_hash": approval["basis_hash"], "source_report_hash": verified["report_hash"]}
                row = ScriptPackageRelease(version_id=version_id, approval_id=approval["id"], submitted_by=actor,
                    idempotency_key=body.idempotency_key, input_hash=_request_hash(version_id, body),
                    package_hash=approval["package_hash"], bundle_hash=approval["bundle_hash"], basis_hash=approval["basis_hash"],
                    source_report_hash=verified["report_hash"], release_hash=content_hash(payload), release_json=canonical_json(payload))
                self.db.add(row)
                self.db.flush()
                return self._release(row)
        except IntegrityError:
            existing = self._existing(ScriptPackageRelease, version_id, body, actor)
            if existing is not None:
                return self._release(existing)
            raise PublicationConflict("PUBLICATION_WRITE_CONFLICT") from None

    def get_release(self, release_id: int, require_current: bool = True) -> dict:
        if type(release_id) is not int or release_id <= 0:
            raise PackageNotFound("发布记录不存在")
        row = self.db.get(ScriptPackageRelease, release_id, populate_existing=True)
        if row is None:
            raise PackageNotFound("发布记录不存在")
        release = self._release(row)
        if require_current:
            state = self._collect(row.version_id)
            approval = self._latest_approval(row.version_id)
            store = self.source_store or SourceBundleStore()
            if (not approval or approval["id"] != row.approval_id
                    or not self._approval_valid(approval, state, self._sources(state, store, persist=False) is not None)):
                raise PublicationConflict("PUBLICATION_APPROVAL_EXPIRED")
            lock_candidate_version(self.db, row.version_id)
            current = self._collect(row.version_id)
            latest = self._latest_approval(row.version_id)
            if current["basis_hash"] != state["basis_hash"] or latest != approval or not self._approval_valid(latest, current, True):
                raise PublicationConflict("PUBLICATION_APPROVAL_EXPIRED")
        package = self._version(row.version_id)["package"]
        return release | {"package": package, "title": package["title"], "content_version": package["content_version"],
                          "player_count": package["player_count"], "current_approval_valid": True if require_current else None}

    def list_releases(self) -> list[dict]:
        rows = self.db.query(ScriptPackageRelease).order_by(ScriptPackageRelease.id.desc()).limit(101).all()
        if len(rows) > 100:
            raise PublicationError("PUBLICATION_RELEASE_LIMIT_EXCEEDED")
        result = []
        store = self.source_store or SourceBundleStore()
        for row in rows:
            try:
                # Catalog reads make no authorization decision and take no
                # write lock. Session creation rechecks and locks one release.
                gate = self.gate_state(row.version_id, store)
                if gate["release"] and gate["release"]["id"] == row.id and gate["release"]["current_approval_valid"]:
                    candidate = gate["candidate"]
                    result.append(gate["release"] | {key: candidate[key] for key in ("title", "content_version", "player_count")})
            except PublicationError:
                continue
        return result
