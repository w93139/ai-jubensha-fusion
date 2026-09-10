"""Immutable bindings for package opening previews; legacy sessions are separate."""
from sqlalchemy import Column, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper

from src.db.base import BaseSQLAlchemyModel


class ScriptPackagePlaySession(BaseSQLAlchemyModel):
    __tablename__ = "script_package_play_sessions"

    session_id = Column(String(40), nullable=False, unique=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    release_id = Column(Integer, ForeignKey("script_package_releases.id"), nullable=False)
    version_id = Column(Integer, ForeignKey("script_package_versions.id"), nullable=False)
    package_hash = Column(String(64), nullable=False)
    selected_character_id = Column(String(96), nullable=False)
    idempotency_key = Column(String(96), nullable=False)
    request_hash = Column(String(64), nullable=False)
    binding_json = Column(Text, nullable=False)
    binding_hash = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("owner_user_id", "idempotency_key", name="uq_package_play_actor_key"),
    )


def _deny_binding_change(mapper: Mapper, connection: Connection, target: ScriptPackagePlaySession) -> None:
    raise ValueError("PACKAGE_PLAY_BINDING_IMMUTABLE")


event.listen(ScriptPackagePlaySession, "before_update", _deny_binding_change)
event.listen(ScriptPackagePlaySession, "before_delete", _deny_binding_change)
