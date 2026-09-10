"""Persistent authoring state; updates only through the store's fenced CAS writes."""
from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, event

from ..base import BaseSQLAlchemyModel
from .script_package import _deny_snapshot_change


class AuthoringJob(BaseSQLAlchemyModel):
    __tablename__ = "authoring_jobs"

    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    idempotency_key = Column(String(96), nullable=False)
    request_json = Column(Text, nullable=False)
    request_hash = Column(String(64), nullable=False)
    context_json = Column(Text, nullable=False)
    context_hash = Column(String(64), nullable=False)
    model_snapshot_json = Column(Text, nullable=False)
    model_snapshot_hash = Column(String(64), nullable=False)
    input_hash = Column(String(64), nullable=False)
    state = Column(String(32), nullable=False)
    step = Column(String(16), nullable=False)
    revision = Column(Integer, nullable=False)
    lease_token = Column(String(64), nullable=True)
    lease_expires_at = Column(DateTime(), nullable=True)
    candidate_version_id = Column(Integer, ForeignKey("script_package_versions.id"), nullable=True)
    source_report_hash = Column(String(64), nullable=True)
    error_code = Column(String(96), nullable=True)
    state_hash = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("submitted_by", "idempotency_key", name="uq_authoring_job_actor_key"),
        CheckConstraint("state IN ('QUEUED','RUNNING','NEEDS_RECONCILIATION','BLOCKED','COMPLETED','CANCELLED')", name="ck_authoring_job_state"),
        CheckConstraint("step IN ('COMPILE','CANDIDATE','AUDIT','DONE')", name="ck_authoring_job_step"),
        CheckConstraint("revision >= 0", name="ck_authoring_job_revision"),
    )


class AuthoringAttempt(BaseSQLAlchemyModel):
    __tablename__ = "authoring_attempts"

    job_id = Column(Integer, ForeignKey("authoring_jobs.id"), nullable=False, index=True)
    step = Column(String(16), nullable=False)
    status = Column(String(16), nullable=False)
    prepared_json = Column(Text, nullable=False)
    prepared_hash = Column(String(64), nullable=False)
    output_json = Column(Text, nullable=True)
    output_hash = Column(String(64), nullable=True)
    receipt_json = Column(Text, nullable=True)
    receipt_hash = Column(String(64), nullable=True)
    error_code = Column(String(96), nullable=True)
    state_hash = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "step", name="uq_authoring_attempt_job_step"),
        CheckConstraint("step IN ('COMPILE','AUDIT')", name="ck_authoring_attempt_step"),
        CheckConstraint("status IN ('RESERVED','IN_FLIGHT','SUCCEEDED','FAILED','UNKNOWN')", name="ck_authoring_attempt_status"),
    )


# ORM callers cannot mutate snapshots, re-arm a dispatched call, or delete its
# ledger. The store uses explicit SQL updates with revision/status predicates.
for _model in (AuthoringJob, AuthoringAttempt):
    event.listen(_model, "before_update", _deny_snapshot_change)
    event.listen(_model, "before_delete", _deny_snapshot_change)
