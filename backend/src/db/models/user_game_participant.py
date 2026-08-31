"""用户游戏参与关系模型"""
from sqlalchemy import Column, Integer, String, ForeignKey, Boolean, DateTime, UniqueConstraint, CheckConstraint, Index, text
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from ..base import BaseSQLAlchemyModel

class UserGameParticipant(BaseSQLAlchemyModel):
    """用户参与游戏会话的关系"""
    __tablename__ = "user_game_participants"

    session_id = Column(String(100), ForeignKey("game_sessions.session_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    character_id = Column(Integer, ForeignKey("characters.id", ondelete="CASCADE"), nullable=True, index=True)
    character_name = Column(String(100), nullable=True)
    participant_type = Column(String(16), nullable=False, default="AI")
    is_ready = Column(Boolean, default=False, nullable=False)
    is_online = Column(Boolean, default=False, nullable=False)
    search_actions_remaining = Column(Integer, default=2, nullable=False)
    joined_at = Column(DateTime(timezone=True), server_default=func.now())
    left_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, default=True)

    # relationships
    user = relationship("User")
    # game_session backref via GameSession.events is not needed here

    def to_dict(self):
        data = super().to_dict()
        return data

    def __repr__(self):
        return f"<UserGameParticipant(session_id={self.session_id}, user_id={self.user_id}, character={self.character_name})>"

    __table_args__ = (
        UniqueConstraint("session_id", "character_id", name="uq_session_character"),
        CheckConstraint("participant_type in ('HUMAN', 'AI')", name="ck_participant_type"),
        Index(
            "uq_one_human_per_session",
            "session_id",
            unique=True,
            postgresql_where=text("participant_type = 'HUMAN'"),
            sqlite_where=text("participant_type = 'HUMAN'"),
        ),
    )
