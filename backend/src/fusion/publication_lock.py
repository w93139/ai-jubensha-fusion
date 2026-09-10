"""Serialize candidate-dependent writes without changing immutable snapshots.

The caller owns the transaction. Never hold this lock while reading source
files or waiting on a model. All writers of an approval basis use this lock.
"""
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from src.db.models.script_package import ScriptPackageVersion
from src.fusion.package_import import PackageNotFound


def lock_candidate_version(db: Session, version_id: int) -> None:
    if type(version_id) is not int or version_id <= 0:
        raise PackageNotFound("候选版本不存在")
    if db.get_bind().dialect.name == "sqlite":
        # sqlite3's legacy transaction mode does not BEGIN for SELECT/SAVEPOINT.
        # A no-op DML acquires the database write reservation and starts the
        # real transaction, without invoking ORM timestamps or changing bytes.
        found = db.execute(text("UPDATE script_package_versions SET id = id WHERE id = :id"), {"id": version_id})
        if found.rowcount != 1:
            raise PackageNotFound("候选版本不存在")
    else:
        found = db.execute(select(ScriptPackageVersion.id).where(
            ScriptPackageVersion.id == version_id).with_for_update()).scalar_one_or_none()
        if found is None:
            raise PackageNotFound("候选版本不存在")
