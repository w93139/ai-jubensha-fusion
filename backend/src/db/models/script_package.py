"""Private candidate snapshots and completed deterministic import attempts."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper

from ..base import BaseSQLAlchemyModel


class ScriptPackageVersion(BaseSQLAlchemyModel):
    __tablename__ = "script_package_versions"

    script_key = Column(String(96), nullable=False)
    content_version = Column(String(96), nullable=False)
    package_hash = Column(String(64), nullable=False)
    manifest_hash = Column(String(64), nullable=False)
    contract_version = Column(String(64), nullable=False)
    canonical_version = Column(String(64), nullable=False)
    package_json = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("script_key", "content_version", name="uq_script_package_content_version"),
    )


class ScriptImportJob(BaseSQLAlchemyModel):
    __tablename__ = "script_import_jobs"

    submitted_by = Column(Integer, ForeignKey("users.id"), nullable=False)
    idempotency_key = Column(String(96), nullable=False)
    input_hash = Column(String(64), nullable=False)
    version_id = Column(Integer, ForeignKey("script_package_versions.id"), nullable=True)
    status = Column(String(24), nullable=False)
    step = Column(String(32), nullable=False)
    validator_version = Column(String(64), nullable=False)
    report_hash = Column(String(64), nullable=False)
    report_json = Column(Text, nullable=False)

    __table_args__ = (
        UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_import_actor_key"),
        CheckConstraint(
            "(status = 'SUCCEEDED' AND version_id IS NOT NULL) OR "
            "(status = 'BLOCKED' AND version_id IS NULL)", name="ck_script_import_outcome",
        ),
        CheckConstraint("step = 'DETERMINISTIC_VALIDATION'", name="ck_script_import_step"),
    )


def _deny_snapshot_change(mapper: Mapper, connection: Connection, target: BaseSQLAlchemyModel) -> None:
    raise ValueError("导入快照不可修改或删除；请使用新的版本和任务")


for _model in (ScriptPackageVersion, ScriptImportJob):
    event.listen(_model, "before_update", _deny_snapshot_change)
    event.listen(_model, "before_delete", _deny_snapshot_change)
