"""Transactional candidate intake only; never writes scripts or publishes them.

The request-scoped caller commits/rolls back. A savepoint groups the snapshot
and report; database uniqueness arbitrates concurrent submissions. No slow
model/file/worker operations occur inside these transactions.
"""
from __future__ import annotations

from hashlib import sha256
import json
from typing import Any

from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.db.models.script_package import ScriptImportJob, ScriptPackageVersion
from src.fusion.package_validation import (
    CANONICAL_VERSION, PackageInputError, canonical_json,
    content_hash, validate_package, validator_version_for,
)
from src.schemas.script_package import ImportPackageRequest


class PackageConflict(ValueError):
    pass


class PackageNotFound(ValueError):
    pass


class PackageImportService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def submit(self, document: Any, *, submitted_by: int, idempotency_key: str) -> dict:
        try:
            body = ImportPackageRequest.model_validate({"package": document, "idempotency_key": idempotency_key})
        except ValidationError as exc:
            raise PackageInputError("需要有效的导入幂等键和 JSON 对象候选包") from exc
        if type(submitted_by) is not int or submitted_by <= 0:
            raise PackageInputError("导入任务需要有效的提交人")
        serialized = canonical_json(body.package)
        digest = sha256(serialized.encode("utf-8")).hexdigest()
        existing = self._existing_job(submitted_by, idempotency_key, digest)
        if existing is not None:
            return self._job_result(existing)

        report = validate_package(body.package)
        report_json = canonical_json(report)
        # At most one local retry, and only after a uniqueness race. No model
        # requests or external side effects are repeated.
        for attempt in range(2):
            try:
                with self.db.begin_nested():
                    version = None
                    if report["valid"]:
                        version = self.db.query(ScriptPackageVersion).filter_by(
                            script_key=document["script_key"], content_version=document["content_version"],
                        ).one_or_none()
                        if version is not None:
                            self._version_result(version)
                            if version.package_hash != digest:
                                raise PackageConflict("内容版本已被使用；修改内容必须使用新版本")
                        else:
                            version = ScriptPackageVersion(
                                script_key=document["script_key"], content_version=document["content_version"],
                                package_hash=digest, manifest_hash=content_hash(document["sources"]),
                                contract_version=document["schema_version"], canonical_version=CANONICAL_VERSION,
                                package_json=serialized,
                            )
                            self.db.add(version)
                            self.db.flush()
                    job = ScriptImportJob(
                        submitted_by=submitted_by, idempotency_key=idempotency_key,
                        input_hash=digest, version_id=version.id if version is not None else None,
                        status="SUCCEEDED" if version is not None else "BLOCKED",
                        step="DETERMINISTIC_VALIDATION", validator_version=validator_version_for(body.package),
                        report_hash=sha256(report_json.encode("utf-8")).hexdigest(), report_json=report_json,
                    )
                    self.db.add(job)
                    self.db.flush()
                return self._job_result(job)
            except IntegrityError:
                existing = self._existing_job(submitted_by, idempotency_key, digest)
                if existing is not None:
                    return self._job_result(existing)
                if attempt == 1:
                    raise PackageConflict("导入事务发生冲突，请查询原任务后使用相同幂等键重试") from None
        raise AssertionError("unreachable")

    def _existing_job(self, actor: int, key: str, digest: str) -> ScriptImportJob | None:
        job = self.db.query(ScriptImportJob).filter_by(submitted_by=actor, idempotency_key=key).one_or_none()
        if job is not None and job.input_hash != digest:
            raise PackageConflict("该幂等键已绑定另一份候选包")
        return job

    def get_job(self, job_id: int) -> dict:
        job = self.db.get(ScriptImportJob, job_id)
        if job is None:
            raise PackageNotFound("导入任务不存在")
        return self._job_result(job)

    def get_version(self, version_id: int) -> dict:
        version = self.db.get(ScriptPackageVersion, version_id)
        if version is None:
            raise PackageNotFound("候选版本不存在")
        return self._version_result(version)

    def _job_result(self, job: ScriptImportJob) -> dict:
        try:
            report = json.loads(job.report_json)
            if (sha256(job.report_json.encode("utf-8")).hexdigest() != job.report_hash
                    or report["package_hash"] != job.input_hash
                    or report["validator_version"] != job.validator_version
                    or report["publication_ready"] is not False
                    or bool(report["valid"]) != (job.status == "SUCCEEDED")):
                raise ValueError
            if job.version_id is not None:
                version = self.get_version(job.version_id)
                if version["package_hash"] != job.input_hash:
                    raise ValueError
        except (ValueError, KeyError, TypeError, PackageNotFound) as exc:
            raise PackageConflict("导入记录完整性检查失败") from exc
        return {"id": job.id, "status": job.status, "step": job.step,
                "version_id": job.version_id, "input_hash": job.input_hash,
                "report_hash": job.report_hash, "report": report, "publication_ready": False}

    def _version_result(self, version: ScriptPackageVersion) -> dict:
        try:
            document = json.loads(version.package_json)
            if (content_hash(document) != version.package_hash
                    or content_hash(document["sources"]) != version.manifest_hash
                    or document["script_key"] != version.script_key
                    or document["content_version"] != version.content_version
                    or document["schema_version"] != version.contract_version
                    or version.canonical_version != CANONICAL_VERSION):
                raise ValueError
        except (ValueError, KeyError, TypeError) as exc:
            raise PackageConflict("候选版本完整性检查失败") from exc
        return {"id": version.id, "script_key": version.script_key,
                "content_version": version.content_version, "package_hash": version.package_hash,
                "manifest_hash": version.manifest_hash, "package": document,
                "publication_ready": False}
