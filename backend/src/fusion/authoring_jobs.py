"""Short transactions and an at-most-once model-call ledger; never performs I/O.

Every state transition is a compare-and-swap. A lease can be reclaimed before
dispatch, but an in-flight/unknown call requires reconciliation, never resend.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError, OperationalError

from src.db.models.authoring_job import AuthoringAttempt, AuthoringJob
from src.fusion.authoring_model import _context, validate_output_diagnostics, validate_response_fingerprint, validate_response_finish, authoring_response_format
from src.fusion.budget import usage_metadata_is_valid
from src.fusion.package_import import PackageImportService
from src.fusion.publication_lock import lock_candidate_version
from src.fusion.package_validation import canonical_json, content_hash, parse_package_json
from src.schemas.authoring import parse_authoring_request


LEASE_SECONDS = 300
MAX_JOB_COST_CNY = Decimal("0.10")
JOB_STATES = {"QUEUED", "RUNNING", "NEEDS_RECONCILIATION", "BLOCKED", "COMPLETED", "CANCELLED"}
TERMINAL_STATES = {"BLOCKED", "COMPLETED", "CANCELLED"}
STEPS = {"COMPILE": 0, "CANDIDATE": 1, "AUDIT": 2, "DONE": 3}
SUPPORTED_CONTRACT_PAIRS = {("authoring-model/1.12", "bailian-authoring-json/1.12"), ("authoring-model/1.11", "bailian-authoring-json/1.11"), ("authoring-model/1.0", "bailian-authoring-json/1.0"),
                            ("authoring-model/1.1", "bailian-authoring-json/1.1"),
                            ("authoring-model/1.2", "bailian-authoring-json/1.2"),
                            ("authoring-model/1.3", "bailian-authoring-json/1.3"),
                            ("authoring-model/1.4", "bailian-authoring-json/1.4"),
                            ("authoring-model/1.10", "bailian-authoring-json/1.10"), ("authoring-model/1.9", "bailian-authoring-json/1.9"), ("authoring-model/1.8", "bailian-authoring-json/1.8"), ("authoring-model/1.7", "bailian-authoring-json/1.7"), ("authoring-model/1.6", "bailian-authoring-json/1.6"), ("authoring-model/1.5", "bailian-authoring-json/1.5")}
JOB_FIELDS = ("state", "step", "revision", "lease_token", "lease_expires_at", "candidate_version_id", "source_report_hash", "error_code")
ATTEMPT_FIELDS = ("job_id", "step", "status", "prepared_hash", "output_hash", "receipt_hash", "error_code")


class AuthoringJobError(ValueError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _digest(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _code(value: Any) -> str | None:
    if value is not None and (not isinstance(value, str) or re.fullmatch(r"[A-Z][A-Z0-9_]{0,95}", value) is None):
        raise AuthoringJobError("AUTHORING_ERROR_CODE_INVALID")
    return value


def _decimal(value: Any, *, positive: bool = False) -> Decimal:
    try:
        if not isinstance(value, str) or len(value) > 64:
            raise ValueError
        result = Decimal(value)
        if not result.is_finite() or result < 0 or (positive and result == 0):
            raise ValueError
        return result
    except (ValueError, InvalidOperation):
        raise AuthoringJobError("AUTHORING_COST_INVALID") from None


def _object(value: Any, limit: int = 4 * 1024 * 1024) -> dict:
    try:
        encoded = canonical_json(value).encode("utf-8")
        if len(encoded) > limit:
            raise ValueError
        result = parse_package_json(encoded)
        if type(result) is not dict:
            raise ValueError
        return result
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise AuthoringJobError("AUTHORING_INPUT_INVALID") from None


def _snapshot(raw: dict) -> dict:
    snapshot = _object(raw, 32768)
    expected = {"schema_version", "provider", "model", "base_url", "pricing_version", "input_rate_cny",
                "cached_input_rate_cny", "output_rate_cny", "timeout_seconds", "max_completion_tokens",
                "max_input_tokens", "max_call_cost_cny", "prompt_hashes", "schema_hashes", "request_contract"}
    try:
        if (set(snapshot) != expected
                or (snapshot["schema_version"], snapshot["request_contract"]) not in SUPPORTED_CONTRACT_PAIRS
                or snapshot["provider"] != "aliyun_bailian"
                or snapshot["timeout_seconds"] != 90 or snapshot["max_input_tokens"] != 32768
                or snapshot["max_completion_tokens"] != {"COMPILE": 8192, "AUDIT": 4096}
                or _decimal(snapshot["max_call_cost_cny"], positive=True) != Decimal("0.05")):
            raise ValueError
        for key in ("model", "base_url", "pricing_version"):
            if not isinstance(snapshot[key], str) or not 1 <= len(snapshot[key]) <= 300:
                raise ValueError
        for key in ("prompt_hashes", "schema_hashes"):
            if set(snapshot[key]) != {"COMPILE", "AUDIT"} or not all(_digest(value) for value in snapshot[key].values()):
                raise ValueError
        rates = [_decimal(snapshot[key], positive=True) for key in ("input_rate_cny", "cached_input_rate_cny", "output_rate_cny")]
        if rates[1] > rates[0]:
            raise ValueError
        return snapshot
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AuthoringJobError("AUTHORING_MODEL_SNAPSHOT_INVALID") from None


def _price(usage: dict, snapshot: dict) -> Decimal:
    return ((usage["prompt_tokens"] - usage["cached_prompt_tokens"]) * Decimal(snapshot["input_rate_cny"])
            + usage["cached_prompt_tokens"] * Decimal(snapshot["cached_input_rate_cny"])
            + usage["completion_tokens"] * Decimal(snapshot["output_rate_cny"])) / Decimal(1000000)


def _package_binding_matches(request: dict, context: dict, snapshot: dict) -> bool:
    """No cross-version replay, and no default field injected into old records."""
    contract = request.get("package_contract", "script-package/1.1")
    is_v12 = snapshot["schema_version"] in {"authoring-model/1.2", "authoring-model/1.3", "authoring-model/1.4", "authoring-model/1.5", "authoring-model/1.6", "authoring-model/1.7", "authoring-model/1.8", "authoring-model/1.9", "authoring-model/1.10", "authoring-model/1.11", "authoring-model/1.12"}
    return (context.get("package_contract", "script-package/1.1") == contract
            and ("rule_plan" in request) == ("rule_plan" in context)
            and ("rule_plan" in request) == (snapshot["schema_version"] in {"authoring-model/1.3", "authoring-model/1.4", "authoring-model/1.5", "authoring-model/1.6", "authoring-model/1.7", "authoring-model/1.8", "authoring-model/1.9", "authoring-model/1.10", "authoring-model/1.11", "authoring-model/1.12"})
            and ("compiler_mode" in request) == ("compiler_mode" in context)
            and ("compiler_mode" in request) == (snapshot["schema_version"] in {"authoring-model/1.4", "authoring-model/1.5", "authoring-model/1.6", "authoring-model/1.7", "authoring-model/1.8", "authoring-model/1.9", "authoring-model/1.10", "authoring-model/1.11", "authoring-model/1.12"})
            and ("audit_mode" in request) == ("audit_mode" in context)
            and ("audit_mode" in request) == (snapshot["schema_version"] in {"authoring-model/1.5", "authoring-model/1.6", "authoring-model/1.7", "authoring-model/1.8", "authoring-model/1.9", "authoring-model/1.10", "authoring-model/1.11", "authoring-model/1.12"})
            and (request.get("audit_mode") in {"BOUNDED_TARGET_SOURCE_INDEXES", "DIRECT_BOUNDED_SOURCE_INDEXES", "STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) == (snapshot["schema_version"] in {"authoring-model/1.6", "authoring-model/1.7", "authoring-model/1.8", "authoring-model/1.9", "authoring-model/1.10", "authoring-model/1.11", "authoring-model/1.12"})
            and (request.get("audit_mode") in {"DIRECT_BOUNDED_SOURCE_INDEXES", "STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) == (snapshot["schema_version"] in {"authoring-model/1.7", "authoring-model/1.8", "authoring-model/1.9", "authoring-model/1.10", "authoring-model/1.11", "authoring-model/1.12"})
            and (request.get("audit_mode") in {"STRICT_BOUNDED_SOURCE_INDEXES", "PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) == (snapshot["schema_version"] in {"authoring-model/1.8", "authoring-model/1.9", "authoring-model/1.10", "authoring-model/1.11", "authoring-model/1.12"})
            and (request.get("audit_mode") in {"PORTABLE_STRICT_SOURCE_INDEXES", "TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) == (snapshot["schema_version"] in {"authoring-model/1.9", "authoring-model/1.10", "authoring-model/1.11", "authoring-model/1.12"})
            and (request.get("audit_mode") in {"TYPED_STRICT_SOURCE_INDEXES", "RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) == (snapshot["schema_version"] in {"authoring-model/1.10", "authoring-model/1.11", "authoring-model/1.12"})
            and (request.get("audit_mode") in {"RUNTIME_CONTEXT_SOURCE_INDEXES", "CITATION_CATALOG"}) == (snapshot["schema_version"] in {"authoring-model/1.11", "authoring-model/1.12"})
            and (request.get("audit_mode") == "CITATION_CATALOG") == (snapshot["schema_version"] == "authoring-model/1.12")
            and is_v12 == (contract == "script-package/1.2"))


def _prepared(raw: dict, step: str, snapshot: dict, context: dict | None = None) -> dict:
    prepared = _object(raw, 32768)
    try:
        if (step not in ("COMPILE", "AUDIT") or set(prepared) != {"step", "prompt_hash", "contract_hash", "input_tokens",
                "max_completion_tokens", "reservation", "request_contract"} or prepared["step"] != step
                or prepared["prompt_hash"] != snapshot["prompt_hashes"][step]
                or prepared["contract_hash"] != content_hash({"request": prepared["request_contract"], "configuration": snapshot})
                or type(prepared["input_tokens"]) is not int or not 0 < prepared["input_tokens"] <= snapshot["max_input_tokens"]
                or prepared["max_completion_tokens"] != snapshot["max_completion_tokens"][step]):
            raise ValueError
        contract = prepared["request_contract"]
        bound_package = context["rule_plan"] if snapshot["schema_version"] == "authoring-model/1.12" and step == "AUDIT" else None
        expected_contract = {"version": snapshot["request_contract"], "model": snapshot["model"],
                             "response_format": authoring_response_format(snapshot["schema_version"], step, bound_package), "temperature": 0,
                             "max_completion_tokens": prepared["max_completion_tokens"],
                             "extra_body": {"enable_thinking": False, "preserve_thinking": False},
                             "client_max_retries": 0, "timeout_seconds": snapshot["timeout_seconds"],
                             "schema_hash": snapshot["schema_hashes"][step]}
        if bound_package is not None:
            from src.fusion.citation_audit import prepare_citation_contract
            citation_contract = prepare_citation_contract(bound_package)
            expected_contract["schema_hash"] = content_hash(citation_contract["local_schema"])
            expected_contract["citation_binding"] = {key:citation_contract[key] for key in ("schema_version", "package_hash", "catalog_hash")}
        reservation = prepared["reservation"]
        if (contract != expected_contract or not usage_metadata_is_valid(reservation)
                or set(reservation) != {"prompt_tokens", "completion_tokens", "cached_prompt_tokens", "reasoning_tokens", "cost_cny"}
                or reservation["prompt_tokens"] != prepared["input_tokens"]
                or reservation["completion_tokens"] != prepared["max_completion_tokens"] + 16
                or reservation["cached_prompt_tokens"] or reservation.get("reasoning_tokens", 0)
                or _decimal(reservation["cost_cny"], positive=True) != _price(reservation, snapshot)
                or Decimal(reservation["cost_cny"]) > Decimal(snapshot["max_call_cost_cny"])):
            raise ValueError
        return prepared
    except (ValueError, KeyError, TypeError, AttributeError):
        raise AuthoringJobError("AUTHORING_PREPARATION_INVALID") from None


def _hash_state(values: dict) -> str:
    return content_hash({key: value.isoformat() if isinstance(value, datetime) else value for key, value in values.items()})


class AuthoringJobStore:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    @contextmanager
    def _transaction(self):
        try:
            with self.session_factory() as session:
                with session.begin():
                    yield session
        except (IntegrityError, OperationalError):
            raise AuthoringJobError("AUTHORING_STATE_CONFLICT") from None

    @staticmethod
    def _inputs(row: AuthoringJob) -> dict:
        try:
            request = parse_authoring_request(parse_package_json(row.request_json.encode())).model_dump()
            context = _context(parse_package_json(row.context_json.encode()))
            snapshot = _snapshot(parse_package_json(row.model_snapshot_json.encode()))
            if (content_hash(request) != row.request_hash or content_hash(context) != row.context_hash
                    or content_hash(snapshot) != row.model_snapshot_hash or request["idempotency_key"] != row.idempotency_key
                    or any(request[key] != context[key] for key in request if key != "idempotency_key")
                    or not _package_binding_matches(request, context, snapshot)
                    or content_hash({"request": request, "context": context, "model_snapshot": snapshot,
                                     "submitted_by": row.submitted_by}) != row.input_hash):
                raise ValueError
            return {"request": request, "context": context, "model_snapshot": snapshot, "submitted_by": row.submitted_by}
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AuthoringJobError("AUTHORING_INPUT_INTEGRITY_FAILED") from None

    def _job(self, session, identifier: int) -> AuthoringJob:
        if type(identifier) is not int or identifier <= 0:
            raise AuthoringJobError("AUTHORING_IDENTIFIER_INVALID")
        row = session.get(AuthoringJob, identifier)
        if row is None:
            raise AuthoringJobError("AUTHORING_JOB_NOT_FOUND")
        self._inputs(row)
        if row.state_hash != _hash_state({key: getattr(row, key) for key in JOB_FIELDS}):
            raise AuthoringJobError("AUTHORING_STATE_INTEGRITY_FAILED")
        return row

    def _attempt_result(self, row: AuthoringAttempt, job: AuthoringJob) -> dict:
        try:
            prepared = _prepared(parse_package_json(row.prepared_json.encode()), row.step, self._inputs(job)["model_snapshot"], self._inputs(job)["context"])
            output = parse_package_json(row.output_json.encode()) if row.output_json is not None else None
            receipt = parse_package_json(row.receipt_json.encode()) if row.receipt_json is not None else None
            if (row.job_id != job.id or content_hash(prepared) != row.prepared_hash
                    or (content_hash(output) if output is not None else None) != row.output_hash
                    or (content_hash(receipt) if receipt is not None else None) != row.receipt_hash
                    or row.state_hash != _hash_state({key: getattr(row, key) for key in ATTEMPT_FIELDS})
                    or (row.status in {"RESERVED", "IN_FLIGHT"} and (output is not None or receipt is not None))
                    or (row.status == "SUCCEEDED" and (output is None or receipt is None or row.error_code is not None))
                    or (row.status in {"FAILED", "UNKNOWN"} and (output is not None or receipt is None or row.error_code is None))):
                raise ValueError
            if row.status in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
                status, *_ = self._finish_data(prepared, self._inputs(job)["model_snapshot"], output, receipt, row.error_code)
                if status != row.status:
                    raise ValueError
            return {"id": row.id, "step": row.step, "status": row.status, "prepared": prepared, "output": output,
                    "receipt": receipt, "error_code": row.error_code, "output_hash": row.output_hash}
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AuthoringJobError("AUTHORING_ATTEMPT_INTEGRITY_FAILED") from None

    def _attempts(self, session, job: AuthoringJob) -> list[dict]:
        rows = session.query(AuthoringAttempt).filter_by(job_id=job.id).order_by(AuthoringAttempt.id).limit(3).all()
        if len(rows) > 2:
            raise AuthoringJobError("AUTHORING_ATTEMPT_INTEGRITY_FAILED")
        return [self._attempt_result(row, job) for row in rows]

    @staticmethod
    def _charge(attempt: dict) -> Decimal:
        reservation = _decimal(attempt["prepared"]["reservation"]["cost_cny"], positive=True)
        receipt = attempt["receipt"]
        if receipt is None or receipt.get("usage_known") is not True:
            return reservation
        return _decimal(receipt["charged_cost_cny"])

    def _result(self, session, row: AuthoringJob) -> dict:
        inputs = self._inputs(row)
        request = inputs["request"]
        attempts = self._attempts(session, row)
        return {"id": row.id, **{key: request[key] for key in ("title", "bundle_hash", "source_ids", "content_version", "player_count")},
                **{key: getattr(row, key) for key in ("state", "step", "revision", "candidate_version_id", "source_report_hash", "error_code")},
                "created_at": row.created_at.isoformat() + "Z", "updated_at": row.updated_at.isoformat() + "Z",
                "publication_ready": False, "model_snapshot": inputs["model_snapshot"], "attempts": attempts,
                "charged_cost_cny": str(sum((self._charge(item) for item in attempts), Decimal(0)))}

    def create(self, request: dict, context: dict, model_snapshot: dict, actor: int) -> dict:
        try:
            request = parse_authoring_request(request).model_dump()
            context = _context(context)
            snapshot = _snapshot(model_snapshot)
            if (type(actor) is not int or actor <= 0
                    or any(context[key] != request[key] for key in request if key != "idempotency_key")
                    or not _package_binding_matches(request, context, snapshot)):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            raise AuthoringJobError("AUTHORING_INPUT_INVALID") from None
        request_hash = content_hash(request)

        def existing(session):
            row = session.query(AuthoringJob).filter_by(submitted_by=actor, idempotency_key=request["idempotency_key"]).one_or_none()
            if row is not None:
                self._job(session, row.id)
                if row.request_hash != request_hash:
                    raise AuthoringJobError("AUTHORING_IDEMPOTENCY_CONFLICT")
                return self._result(session, row)
            return None

        try:
            with self._transaction() as session:
                found = existing(session)
                if found is not None:
                    return found
                state = {"state": "QUEUED", "step": "COMPILE", "revision": 0, "lease_token": None,
                         "lease_expires_at": None, "candidate_version_id": None, "source_report_hash": None, "error_code": None}
                row = AuthoringJob(submitted_by=actor, idempotency_key=request["idempotency_key"],
                                   request_json=canonical_json(request), request_hash=request_hash,
                                   context_json=canonical_json(context), context_hash=content_hash(context),
                                   model_snapshot_json=canonical_json(snapshot), model_snapshot_hash=content_hash(snapshot),
                                   input_hash=content_hash({"request": request, "context": context, "model_snapshot": snapshot, "submitted_by": actor}),
                                   **state, state_hash=_hash_state(state))
                session.add(row)
                session.flush()
                return self._result(session, row)
        except AuthoringJobError as error:
            if error.code != "AUTHORING_STATE_CONFLICT":
                raise
            with self._transaction() as session:
                found = existing(session)
                if found is not None:
                    return found
            raise

    def get(self, job_id: int) -> dict:
        with self._transaction() as session:
            return self._result(session, self._job(session, job_id))

    def existing_request(self, request: dict, actor: int) -> dict | None:
        """Find an idempotent retry before loading mutable source/config state."""
        try:
            request = parse_authoring_request(request).model_dump()
            if type(actor) is not int or actor <= 0:
                raise ValueError
        except (ValueError, TypeError):
            raise AuthoringJobError("AUTHORING_INPUT_INVALID") from None
        with self._transaction() as session:
            row = session.query(AuthoringJob).filter_by(submitted_by=actor, idempotency_key=request["idempotency_key"]).one_or_none()
            if row is None:
                return None
            self._job(session, row.id)
            if row.request_hash != content_hash(request):
                raise AuthoringJobError("AUTHORING_IDEMPOTENCY_CONFLICT")
            return self._result(session, row)

    def list(self) -> list[dict]:
        with self._transaction() as session:
            rows = session.query(AuthoringJob).order_by(AuthoringJob.id.desc()).limit(100).all()
            return [self._result(session, self._job(session, row.id)) for row in rows]

    def inputs(self, job_id: int) -> dict:
        with self._transaction() as session:
            return self._inputs(self._job(session, job_id))

    @staticmethod
    def _cas_job(session, row: AuthoringJob, *, _token: str | None = None, **changes) -> None:
        candidate_id = changes.get("candidate_version_id") or row.candidate_version_id
        if candidate_id is not None:
            lock_candidate_version(session, candidate_id)
        old_revision = row.revision
        values = {key: getattr(row, key) for key in JOB_FIELDS}
        values.update(changes, revision=old_revision + 1)
        predicates = [AuthoringJob.id == row.id, AuthoringJob.revision == old_revision, AuthoringJob.state_hash == row.state_hash]
        if _token is not None:
            predicates.extend((AuthoringJob.state == "RUNNING", AuthoringJob.lease_token == _token,
                               AuthoringJob.lease_expires_at > _now()))
        result = session.execute(update(AuthoringJob).where(*predicates)
                                 .values(**values, state_hash=_hash_state(values), updated_at=_now()),
                                 execution_options={"synchronize_session": False})
        if result.rowcount != 1:
            raise AuthoringJobError("AUTHORING_STATE_CONFLICT")
        session.expire(row)
        session.refresh(row)

    @staticmethod
    def _owned(row: AuthoringJob, token: str) -> None:
        if (row.state != "RUNNING" or not isinstance(token, str) or row.lease_token != token
                or row.lease_expires_at is None or row.lease_expires_at <= _now()):
            raise AuthoringJobError("AUTHORING_LEASE_LOST")

    def claim(self, job_id: int) -> str:
        needs_reconciliation = False
        token = uuid4().hex
        with self._transaction() as session:
            row = self._job(session, job_id)
            if row.state not in {"QUEUED", "RUNNING"}:
                raise AuthoringJobError("AUTHORING_JOB_NOT_CLAIMABLE")
            if row.state == "RUNNING" and row.lease_expires_at is not None and row.lease_expires_at > _now():
                raise AuthoringJobError("AUTHORING_LEASE_BUSY")
            if any(item["status"] in {"IN_FLIGHT", "UNKNOWN"} for item in self._attempts(session, row)):
                self._cas_job(session, row, state="NEEDS_RECONCILIATION", lease_token=None, lease_expires_at=None,
                              error_code="AUTHORING_USAGE_RECONCILIATION_REQUIRED")
                needs_reconciliation = True
            else:
                self._cas_job(session, row, state="RUNNING", lease_token=token,
                              lease_expires_at=_now() + timedelta(seconds=LEASE_SECONDS), error_code=None)
        if needs_reconciliation:
            raise AuthoringJobError("AUTHORING_USAGE_RECONCILIATION_REQUIRED")
        return token

    def cancel(self, job_id: int, expected_revision: int) -> dict:
        with self._transaction() as session:
            row = self._job(session, job_id)
            if type(expected_revision) is not int or row.revision != expected_revision:
                raise AuthoringJobError("AUTHORING_STATE_CONFLICT")
            if row.state == "CANCELLED":
                return self._result(session, row)
            if row.state in TERMINAL_STATES:
                raise AuthoringJobError("AUTHORING_JOB_TERMINAL")
            self._cas_job(session, row, state="CANCELLED", lease_token=None, lease_expires_at=None, error_code="AUTHORING_CANCELLED")
            return self._result(session, row)

    def recover(self, job_id: int, expected_revision: int) -> dict:
        with self._transaction() as session:
            row = self._job(session, job_id)
            if type(expected_revision) is not int or row.revision != expected_revision:
                raise AuthoringJobError("AUTHORING_STATE_CONFLICT")
            expired = row.state == "RUNNING" and row.lease_expires_at is not None and row.lease_expires_at <= _now()
            if (not expired and row.state != "NEEDS_RECONCILIATION") or any(
                    item["status"] in {"IN_FLIGHT", "UNKNOWN"} for item in self._attempts(session, row)):
                raise AuthoringJobError("AUTHORING_RECOVERY_UNSAFE")
            self._cas_job(session, row, state="QUEUED", lease_token=None, lease_expires_at=None, error_code=None)
            return self._result(session, row)

    def reserve(self, job_id: int, token: str, step: str, prepared: dict) -> dict:
        with self._transaction() as session:
            job = self._job(session, job_id)
            self._owned(job, token)
            prepared = _prepared(prepared, step, self._inputs(job)["model_snapshot"], self._inputs(job)["context"])
            attempts = self._attempts(session, job)
            existing = next((item for item in attempts if item["step"] == step), None)
            if existing is not None:
                if existing["prepared"] != prepared:
                    raise AuthoringJobError("AUTHORING_PREPARATION_CHANGED")
                return existing
            compiled = next((item for item in attempts if item["step"] == "COMPILE"), None)
            if (step == "COMPILE" and (attempts or job.step != "COMPILE")) or (step == "AUDIT" and (
                    job.step != "AUDIT" or job.candidate_version_id is None or job.source_report_hash is None
                    or compiled is None or compiled["status"] != "SUCCEEDED" or compiled["output"].get("status") != "CANDIDATE")):
                raise AuthoringJobError("AUTHORING_STEP_INVALID")
            if sum((self._charge(item) for item in attempts), Decimal(0)) + Decimal(prepared["reservation"]["cost_cny"]) > MAX_JOB_COST_CNY:
                raise AuthoringJobError("AUTHORING_JOB_BUDGET_EXCEEDED")
            # The job row is the shared budget/attempt write fence.
            self._cas_job(session, job, _token=token)
            values = {"job_id": job.id, "step": step, "status": "RESERVED", "prepared_hash": content_hash(prepared),
                      "output_hash": None, "receipt_hash": None, "error_code": None}
            row = AuthoringAttempt(**values, prepared_json=canonical_json(prepared), state_hash=_hash_state(values))
            session.add(row)
            session.flush()
            return self._attempt_result(row, job)

    def dispatch(self, job_id: int, token: str, attempt_id: int) -> dict:
        with self._transaction() as session:
            job = self._job(session, job_id)
            self._owned(job, token)
            row = session.get(AuthoringAttempt, attempt_id)
            if row is None or row.job_id != job_id:
                raise AuthoringJobError("AUTHORING_ATTEMPT_NOT_FOUND")
            self._attempt_result(row, job)
            if row.status != "RESERVED":
                raise AuthoringJobError("AUTHORING_ATTEMPT_ALREADY_DISPATCHED")
            self._cas_job(session, job, _token=token)
            values = {key: getattr(row, key) for key in ATTEMPT_FIELDS} | {"status": "IN_FLIGHT"}
            result = session.execute(update(AuthoringAttempt).where(AuthoringAttempt.id == row.id, AuthoringAttempt.status == "RESERVED",
                                                                   AuthoringAttempt.state_hash == row.state_hash)
                                     .values(status="IN_FLIGHT", state_hash=_hash_state(values), updated_at=_now()),
                                     execution_options={"synchronize_session": False})
            if result.rowcount != 1:
                raise AuthoringJobError("AUTHORING_STATE_CONFLICT")
            session.expire(row)
            return self._attempt_result(row, job)

    @staticmethod
    def _finish_data(prepared: dict, snapshot: dict, output: dict | None, receipt: dict, error_code: str | None) -> tuple:
        receipt = _object(receipt, 32768)
        error_code = _code(error_code)
        try:
            fields = {"schema_version", "provider", "model", "step", "prompt_hash", "contract_hash", "request_contract",
                      "pricing_version", "reservation", "usage_known", "usage", "charged_cost_cny", "estimated_cost_cny", "latency_ms", "result_code"}
            if "output_diagnostics" in receipt:
                validate_output_diagnostics(receipt["output_diagnostics"])
                fields.add("output_diagnostics")
            if "response_finish" in receipt:
                validate_response_finish(receipt["response_finish"])
                fields.add("response_finish")
                if error_code is None and receipt["response_finish"] != "stop":
                    raise ValueError
            if "response_fingerprint" in receipt:
                validate_response_fingerprint(receipt["response_fingerprint"])
                fields.add("response_fingerprint")
            # Old receipts remain byte-for-byte canonical hash compatible: do
            # not inject an empty diagnostics list when the field is absent.
            if (set(receipt) != fields
                    or any(receipt.get(key) != prepared[key] for key in ("step", "prompt_hash", "contract_hash"))
                    or any(receipt.get(key) != snapshot[key] for key in ("schema_version", "provider", "model", "pricing_version", "request_contract"))
                    or receipt.get("reservation") != prepared["reservation"] or type(receipt.get("usage_known")) is not bool
                    or type(receipt.get("latency_ms")) is not int or receipt["latency_ms"] < 0):
                raise ValueError
            # A provider may return a complete zero-token receipt (for example
            # a filtered request). Its known zero charge replaces reservation.
            charged = _decimal(receipt["charged_cost_cny"])
            if charged != _decimal(receipt["estimated_cost_cny"]):
                raise ValueError
            if receipt["usage_known"]:
                usage = receipt["usage"]
                if (not usage_metadata_is_valid(usage) or usage["prompt_tokens"] + usage["completion_tokens"] > 1000000000
                        or set(usage) != {"prompt_tokens", "completion_tokens", "cached_prompt_tokens", "reasoning_tokens", "cost_cny"}
                        or charged != _decimal(usage["cost_cny"]) or charged != _price(usage, snapshot)):
                    raise ValueError
                if error_code is None:
                    if (output is None or receipt.get("result_code") != "PASSED" or charged > Decimal(prepared["reservation"]["cost_cny"])
                            or usage["prompt_tokens"] > prepared["reservation"]["prompt_tokens"]
                            or usage["completion_tokens"] > prepared["reservation"]["completion_tokens"]):
                        raise ValueError
                    return "SUCCEEDED", _object(output), receipt, None
                if output is not None or receipt.get("result_code") != error_code:
                    raise ValueError
                return "FAILED", None, receipt, error_code
            if (output is not None or receipt.get("usage") is not None or error_code is None
                    or receipt.get("result_code") != error_code or charged != Decimal(prepared["reservation"]["cost_cny"])):
                raise ValueError
            return "UNKNOWN", None, receipt, error_code
        except (ValueError, KeyError, TypeError, AttributeError):
            raise AuthoringJobError("AUTHORING_RECEIPT_INVALID") from None

    def finish_attempt(self, attempt_id: int, output: dict | None, receipt: dict, error_code: str | None = None) -> dict:
        with self._transaction() as session:
            row = session.get(AuthoringAttempt, attempt_id)
            if row is None:
                raise AuthoringJobError("AUTHORING_ATTEMPT_NOT_FOUND")
            job = self._job(session, row.job_id)
            if job.candidate_version_id is not None:
                lock_candidate_version(session, job.candidate_version_id)
            current = self._attempt_result(row, job)
            status, output, receipt, error_code = self._finish_data(current["prepared"], self._inputs(job)["model_snapshot"], output, receipt, error_code)
            if row.status in {"SUCCEEDED", "FAILED", "UNKNOWN"}:
                if (current["output"], current["receipt"], current["error_code"], row.status) == (output, receipt, error_code, status):
                    return current
                raise AuthoringJobError("AUTHORING_RESULT_IMMUTABLE")
            if row.status != "IN_FLIGHT":
                raise AuthoringJobError("AUTHORING_ATTEMPT_NOT_DISPATCHED")
            values = {key: getattr(row, key) for key in ATTEMPT_FIELDS} | {
                "status": status, "output_hash": content_hash(output) if output is not None else None,
                "receipt_hash": content_hash(receipt), "error_code": error_code}
            result = session.execute(update(AuthoringAttempt).where(AuthoringAttempt.id == row.id, AuthoringAttempt.status == "IN_FLIGHT",
                                                                   AuthoringAttempt.state_hash == row.state_hash)
                                     .values(**values, output_json=canonical_json(output) if output is not None else None,
                                             receipt_json=canonical_json(receipt), state_hash=_hash_state(values), updated_at=_now()),
                                     execution_options={"synchronize_session": False})
            if result.rowcount != 1:
                raise AuthoringJobError("AUTHORING_STATE_CONFLICT")
            session.expire(row)
            # Deliberately no job update: cancelled/lost-lease workers may only
            # persist their late receipt, never advance workflow state.
            return self._attempt_result(row, job)

    def materialize(self, job_id: int, token: str, document: dict, source_report_hash: str) -> dict:
        with self._transaction() as session:
            job = self._job(session, job_id)
            self._owned(job, token)
            compiled = next((item for item in self._attempts(session, job) if item["step"] == "COMPILE"), None)
            if (not _digest(source_report_hash) or compiled is None or compiled["status"] != "SUCCEEDED"
                    or compiled["output"].get("status") != "CANDIDATE" or compiled["output"].get("package") != document):
                raise AuthoringJobError("AUTHORING_CANDIDATE_BINDING_INVALID")
            if job.candidate_version_id is not None:
                saved = PackageImportService(session).get_version(job.candidate_version_id)
                if saved["package_hash"] != content_hash(document) or job.source_report_hash != source_report_hash:
                    raise AuthoringJobError("AUTHORING_CANDIDATE_BINDING_INVALID")
                return self._result(session, job)
            if job.step not in {"COMPILE", "CANDIDATE"}:
                raise AuthoringJobError("AUTHORING_STEP_INVALID")
            self._cas_job(session, job, _token=token)
            imported = PackageImportService(session).submit(document, submitted_by=job.submitted_by,
                                                            idempotency_key=f"authoring-{job.id}-candidate")
            if imported["status"] != "SUCCEEDED":
                raise AuthoringJobError("AUTHORING_CANDIDATE_INVALID")
            self._cas_job(session, job, _token=token, step="AUDIT", candidate_version_id=imported["version_id"], source_report_hash=source_report_hash)
            return self._result(session, job)

    def checkpoint(self, job_id: int, token: str, *, step: str, state: str = "RUNNING",
                   candidate_version_id: int | None = None, source_report_hash: str | None = None,
                   error_code: str | None = None) -> dict:
        with self._transaction() as session:
            job = self._job(session, job_id)
            self._owned(job, token)
            if step not in STEPS or STEPS[step] < STEPS[job.step] or state not in {"RUNNING", "BLOCKED", "COMPLETED", "NEEDS_RECONCILIATION"}:
                raise AuthoringJobError("AUTHORING_STEP_INVALID")
            # Candidate binding is written atomically by materialize only.
            if ((candidate_version_id is not None and candidate_version_id != job.candidate_version_id)
                    or (source_report_hash is not None and source_report_hash != job.source_report_hash)):
                raise AuthoringJobError("AUTHORING_CANDIDATE_BINDING_INVALID")
            if step in {"AUDIT", "DONE"} and (job.candidate_version_id is None or job.source_report_hash is None):
                raise AuthoringJobError("AUTHORING_STEP_INVALID")
            attempts = self._attempts(session, job)
            if state == "COMPLETED" and (step != "DONE" or not any(item["step"] == "AUDIT" and item["status"] == "SUCCEEDED" for item in attempts)):
                raise AuthoringJobError("AUTHORING_STEP_INVALID")
            changes = {"step": step, "state": state, "error_code": _code(error_code)}
            if state != "RUNNING":
                changes.update(lease_token=None, lease_expires_at=None)
            self._cas_job(session, job, _token=token, **changes)
            return self._result(session, job)
