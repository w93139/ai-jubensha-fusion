"""Immutable human approvals and released package snapshots."""
from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint, event

from ..base import BaseSQLAlchemyModel
from .script_package import _deny_snapshot_change


class ScriptPublicationApproval(BaseSQLAlchemyModel):
    __tablename__ = "script_publication_approvals"

    version_id = Column(Integer, ForeignKey("script_package_versions.id"), nullable=False, index=True)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    idempotency_key = Column(String(96), nullable=False)
    input_hash = Column(String(64), nullable=False)
    package_hash = Column(String(64), nullable=False)
    bundle_hash = Column(String(64), nullable=False)
    basis_hash = Column(String(64), nullable=False)
    source_report_hash = Column(String(64), nullable=False)
    approval_hash = Column(String(64), nullable=False)
    approval_json = Column(Text, nullable=False)

    __table_args__ = (UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_approval_actor_key"),)


class ScriptPackageRelease(BaseSQLAlchemyModel):
    __tablename__ = "script_package_releases"

    version_id = Column(Integer, ForeignKey("script_package_versions.id"), nullable=False)
    approval_id = Column(Integer, ForeignKey("script_publication_approvals.id"), nullable=False)
    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    idempotency_key = Column(String(96), nullable=False)
    input_hash = Column(String(64), nullable=False)
    package_hash = Column(String(64), nullable=False)
    bundle_hash = Column(String(64), nullable=False)
    basis_hash = Column(String(64), nullable=False)
    source_report_hash = Column(String(64), nullable=False)
    release_hash = Column(String(64), nullable=False)
    release_json = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("version_id", name="uq_script_release_version"),
        UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_release_actor_key"),
    )


for _model in (ScriptPublicationApproval, ScriptPackageRelease):
    event.listen(_model, "before_update", _deny_snapshot_change)
    event.listen(_model, "before_delete", _deny_snapshot_change)
