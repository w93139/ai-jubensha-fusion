"""权威规则引擎需要的持久化模型。"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func

from ..base import BaseSQLAlchemyModel


class ParticipantEvidence(BaseSQLAlchemyModel):
    __tablename__ = "participant_evidence"

    session_id = Column(String(100), ForeignKey("game_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    participant_id = Column(Integer, ForeignKey("user_game_participants.id", ondelete="CASCADE"), nullable=False, index=True)
    evidence_id = Column(Integer, ForeignKey("evidence.id", ondelete="CASCADE"), nullable=False, index=True)
    visibility = Column(String(24), nullable=False, default="CHARACTER_PRIVATE")
    discovered_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revealed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("session_id", "evidence_id", name="uq_session_discovered_evidence"),
    )


class FusionVote(BaseSQLAlchemyModel):
    __tablename__ = "fusion_votes"

    session_id = Column(String(100), ForeignKey("game_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    round_number = Column(Integer, nullable=False, default=1)
    voter_participant_id = Column(Integer, ForeignKey("user_game_participants.id", ondelete="CASCADE"), nullable=False)
    suspect_character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=False)

    __table_args__ = (
        UniqueConstraint("session_id", "round_number", "voter_participant_id", name="uq_vote_per_round"),
    )
