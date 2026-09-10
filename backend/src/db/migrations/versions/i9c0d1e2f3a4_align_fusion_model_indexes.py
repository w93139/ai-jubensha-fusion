"""align fusion models, indexes, and script foreign key

Revision ID: i9c0d1e2f3a4
Revises: h8b9c0d1e2f3
"""
from alembic import op
import sqlalchemy as sa


revision = "i9c0d1e2f3a4"
down_revision = "h8b9c0d1e2f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_fusion_votes_session_id", "fusion_votes", ["session_id"])
    op.create_index(
        "ix_participant_evidence_evidence_id",
        "participant_evidence",
        ["evidence_id"],
    )
    op.create_index(
        "ix_participant_evidence_participant_id",
        "participant_evidence",
        ["participant_id"],
    )
    op.create_index(
        "ix_user_game_participants_character_id",
        "user_game_participants",
        ["character_id"],
    )
    op.create_foreign_key(
        "fk_game_sessions_script_id",
        "game_sessions",
        "scripts",
        ["script_id"],
        ["id"],
    )
    op.alter_column(
        "game_sessions",
        "mode",
        existing_type=sa.String(length=32),
        comment="游戏模式",
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "game_sessions",
        "mode",
        existing_type=sa.String(length=32),
        comment=None,
        existing_nullable=False,
    )
    op.drop_constraint(
        "fk_game_sessions_script_id", "game_sessions", type_="foreignkey"
    )
    op.drop_index(
        "ix_user_game_participants_character_id",
        table_name="user_game_participants",
    )
    op.drop_index(
        "ix_participant_evidence_participant_id",
        table_name="participant_evidence",
    )
    op.drop_index(
        "ix_participant_evidence_evidence_id",
        table_name="participant_evidence",
    )
    op.drop_index("ix_fusion_votes_session_id", table_name="fusion_votes")
