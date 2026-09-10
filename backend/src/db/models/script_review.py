"""Append-only private audit reports and human finding dispositions."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Text, UniqueConstraint, event

from ..base import BaseSQLAlchemyModel
from .script_package import _deny_snapshot_change


class ScriptAuditRecord(BaseSQLAlchemyModel):
    __tablename__ = "script_audit_records"

    version_id = Column(Integer, ForeignKey("script_package_versions.id"), nullable=False, index=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    idempotency_key = Column(String(96), nullable=False)
    input_hash = Column(String(64), nullable=False)
    package_hash = Column(String(64), nullable=False)
    bundle_hash = Column(String(64), nullable=False)
    source_report_hash = Column(String(64), nullable=False)
    audit_hash = Column(String(64), nullable=False)
    audit_json = Column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_audit_actor_key"),)


class ScriptFindingDisposition(BaseSQLAlchemyModel):
    __tablename__ = "script_finding_dispositions"

    audit_id = Column(Integer, ForeignKey("script_audit_records.id"), nullable=False)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    idempotency_key = Column(String(96), nullable=False)
    input_hash = Column(String(64), nullable=False)
    revision = Column(Integer, nullable=False)
    finding_id = Column(String(96), nullable=False)
    status = Column(String(24), nullable=False)
    disposition_hash = Column(String(64), nullable=False)
    disposition_json = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_disposition_actor_key"),
        UniqueConstraint("audit_id", "revision", name="uq_script_disposition_revision"),
        CheckConstraint("revision > 0", name="ck_script_disposition_revision"),
        CheckConstraint("status IN ('OPEN', 'ACKNOWLEDGED', 'DISMISSED')", name="ck_script_disposition_status"),
    )


for _model in (ScriptAuditRecord, ScriptFindingDisposition):
    event.listen(_model, "before_update", _deny_snapshot_change)
    event.listen(_model, "before_delete", _deny_snapshot_change)
