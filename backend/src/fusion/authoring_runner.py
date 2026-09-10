"""Finite durable Compiler → candidate → model Audit workflow.

The HTTP endpoint only queues. This worker owns no long-lived DB session and
never repeats a dispatched call. A model audit cannot approve publication.
"""
from __future__ import annotations

import asyncio

from src.fusion.authoring_jobs import AuthoringJobError, AuthoringJobStore
from src.fusion.authoring_model import (
    AuthoringModel, AuthoringModelError, parse_compiler_output, validate_model_audit,
)
from src.fusion.authoring_sources import AuthoringSourceError, prepare_authoring_sources
from src.fusion.package_import import PackageConflict, PackageNotFound
from src.fusion.package_validation import content_hash
from src.fusion.source_bundles import SourceBundleError, SourceBundleStore, verifier_version_for
from src.schemas.authoring import AuthoringRequest, AuthoringRequestV12, parse_authoring_request


async def submit_authoring_job(jobs: AuthoringJobStore, sources: SourceBundleStore,
                               model: AuthoringModel, request: AuthoringRequest | AuthoringRequestV12, actor: int) -> dict:
    existing = jobs.existing_request(request.model_dump(), actor)
    if existing is not None:
        return existing
    context = await asyncio.to_thread(prepare_authoring_sources, sources, request)
    model.prepare("COMPILE", context)  # Complete input/schema/budget checks; no network.
    return jobs.create(request.model_dump(), context, model.snapshot(), actor)


class AuthoringRunner:
    def __init__(self, jobs: AuthoringJobStore, sources: SourceBundleStore, model: AuthoringModel) -> None:
        self.jobs = jobs
        self.sources = sources
        self.model = model

    async def _invoke(self, job_id: int, token: str, step: str, context: dict, package: dict | None = None) -> dict:
        attempts = self.jobs.get(job_id)["attempts"]
        attempt = next((item for item in attempts if item["step"] == step), None)
        if attempt is not None and attempt["status"] == "SUCCEEDED":
            return attempt["output"]
        if attempt is not None and attempt["status"] not in {"RESERVED"}:
            raise AuthoringModelError("AUTHORING_CALL_NOT_RETRYABLE")
        prepared = self.model.prepare(step, context, package)
        metadata = {"step": step, "prompt_hash": prepared.prompt_hash, "contract_hash": prepared.contract_hash,
                    "input_tokens": prepared.input_tokens, "max_completion_tokens": prepared.max_completion_tokens,
                    "reservation": prepared.reservation.to_metadata(), "request_contract": prepared.request_contract}
        attempt = self.jobs.reserve(job_id, token, step, metadata)
        self.jobs.dispatch(job_id, token, attempt["id"])  # Durable before first byte leaves.
        try:
            result = await self.model.call(step, context, package, prepared=prepared)
        except AuthoringModelError as exc:
            self.jobs.finish_attempt(attempt["id"], None, exc.receipt, error_code=exc.code)
            raise
        output = (parse_compiler_output(result["output"], context) if step == "COMPILE"
                  else validate_model_audit(result["output"], package))
        self.jobs.finish_attempt(attempt["id"], output, result["receipt"])
        return output

    async def _verify(self, context: dict, package: dict) -> dict:
        report = await asyncio.to_thread(self.sources.verify, context["bundle_hash"], document=package)
        persisted = self.sources.get_report(report["report_hash"])
        if (persisted != report or not persisted["valid"]
                or persisted["verifier_version"] != verifier_version_for(package)
                or persisted["bundle_hash"] != context["bundle_hash"]
                or persisted["package_hash"] != content_hash(package)):
            raise AuthoringSourceError("AUTHORING_SOURCE_VERIFICATION_FAILED")
        return persisted

    async def run(self, job_id: int, *, allow_paid: bool = False) -> dict:
        if allow_paid is not True:
            raise AuthoringModelError("AUTHORING_PAID_EXECUTION_DISABLED")
        token = self.jobs.claim(job_id)
        try:
            inputs = self.jobs.inputs(job_id)
            context = inputs["context"]
            if self.model.snapshot() != inputs["model_snapshot"]:
                raise AuthoringModelError("AUTHORING_CONFIGURATION_CHANGED")
            fresh = await asyncio.to_thread(prepare_authoring_sources, self.sources,
                                           parse_authoring_request(inputs["request"]))
            if content_hash(fresh) != content_hash(context):
                raise AuthoringSourceError("AUTHORING_SOURCE_CONTEXT_CHANGED")
            output = parse_compiler_output(await self._invoke(job_id, token, "COMPILE", context), context)
            if output["status"] == "BLOCKED":
                return self.jobs.checkpoint(job_id, token, step="COMPILE", state="BLOCKED",
                                            error_code="COMPILER_REPORTED_BLOCKERS")
            package = output["package"]
            current = self.jobs.get(job_id)
            if current["candidate_version_id"] is None:
                self.jobs.checkpoint(job_id, token, step="CANDIDATE")
                verified = await self._verify(context, package)
                self.jobs.materialize(job_id, token, package, verified["report_hash"])
            else:
                # Re-read the immutable version too; an existing ID alone is no proof.
                with self.jobs.session_factory() as db:
                    from src.fusion.package_import import PackageImportService
                    candidate = PackageImportService(db).get_version(current["candidate_version_id"])
                if candidate["package_hash"] != content_hash(package):
                    raise AuthoringSourceError("AUTHORING_CANDIDATE_BINDING_MISMATCH")
                await self._verify(context, package)
            audit = await self._invoke(job_id, token, "AUDIT", context, package)
            validate_model_audit(audit, package)
            await self._verify(context, package)
            return self.jobs.checkpoint(job_id, token, step="DONE", state="COMPLETED")
        except (AuthoringModelError, AuthoringSourceError, SourceBundleError,
                AuthoringJobError, PackageConflict, PackageNotFound) as exc:
            code = getattr(exc, "code", "AUTHORING_INTEGRITY_FAILED")
            return self._stop(job_id, token, code)
        except Exception:
            # Includes database/provider integration failures; never log private
            # prompts, response bodies, DSNs, or arbitrary exception messages.
            return self._stop(job_id, token, "AUTHORING_WORKER_FAILED")

    def _stop(self, job_id: int, token: str, code: str) -> dict:
        current = self.jobs.get(job_id)
        if current["state"] == "CANCELLED":
            return current
        pending = any(item["status"] in {"IN_FLIGHT", "UNKNOWN"} for item in current["attempts"])
        try:
            return self.jobs.checkpoint(job_id, token, step=current["step"],
                                        state="NEEDS_RECONCILIATION" if pending else "BLOCKED", error_code=code)
        except AuthoringJobError:
            # Another worker/cancellation owns the lease. It alone can advance.
            return self.jobs.get(job_id)
