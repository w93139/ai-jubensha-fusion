"""Immutable text-play bindings and append-only action/model accounting events."""
from sqlalchemy import CheckConstraint, Column, ForeignKey, Integer, String, Text, UniqueConstraint, event
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper

from src.db.base import BaseSQLAlchemyModel


class ScriptPackagePlay(BaseSQLAlchemyModel):
    __tablename__ = "script_package_plays"

    play_id = Column(String(37), nullable=False, unique=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    opening_session_id = Column(String(40), ForeignKey("script_package_play_sessions.session_id"),
                                nullable=False, unique=True)
    release_id = Column(Integer, ForeignKey("script_package_releases.id"), nullable=False)
    version_id = Column(Integer, ForeignKey("script_package_versions.id"), nullable=False)
    package_hash = Column(String(64), nullable=False)
    selected_character_id = Column(String(96), nullable=False)
    idempotency_key = Column(String(96), nullable=False)
    request_hash = Column(String(64), nullable=False)
    binding_json = Column(Text, nullable=False)
    binding_hash = Column(String(64), nullable=False)

    __table_args__ = (
        # The older opening model already owns uq_package_play_actor_key.
        UniqueConstraint("owner_user_id", "idempotency_key", name="uq_package_play_actor_request"),
    )


class ScriptPackagePlayEvent(BaseSQLAlchemyModel):
    __tablename__ = "script_package_play_events"

    play_id = Column(String(37), ForeignKey("script_package_plays.play_id"), nullable=False)
    revision = Column(Integer, nullable=False)
    kind = Column(String(32), nullable=False)
    idempotency_key = Column(String(96), nullable=False)
    request_json = Column(Text, nullable=False)
    request_hash = Column(String(64), nullable=False)
    previous_event_hash = Column(String(64), nullable=False)
    state_hash = Column(String(64), nullable=False)
    event_json = Column(Text, nullable=False)
    event_hash = Column(String(64), nullable=False)

    __table_args__ = (
        UniqueConstraint("play_id", "revision", name="uq_package_play_event_revision"),
        UniqueConstraint("play_id", "idempotency_key", name="uq_package_play_event_key"),
        CheckConstraint("revision > 0", name="ck_package_play_event_revision_positive"),
        CheckConstraint("kind IN ('ACTION', 'AI_REQUEST', 'AI_RESULT')", name="ck_package_play_event_kind"),
    )


def _deny_change(mapper: Mapper, connection: Connection, target: object) -> None:
    raise ValueError("PACKAGE_PLAY_RECORD_IMMUTABLE")


for _model in (ScriptPackagePlay, ScriptPackagePlayEvent):
    event.listen(_model, "before_update", _deny_change)
    event.listen(_model, "before_delete", _deny_change)
