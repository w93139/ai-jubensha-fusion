"""add fusion game domain

Revision ID: f7a8b9c0d1e2
Revises: cc0436694e36
"""
from alembic import op
import sqlalchemy as sa


revision = "f7a8b9c0d1e2"
down_revision = "cc0436694e36"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE scriptstatus ADD VALUE IF NOT EXISTS 'REVIEW'")
    with op.batch_alter_table("game_sessions") as batch:
        batch.add_column(sa.Column("mode", sa.String(32), server_default="SOLO_WITH_AI", nullable=False))
        batch.add_column(sa.Column("current_phase", sa.String(40), server_default="CHARACTER_SELECTION", nullable=False))
        batch.add_column(sa.Column("current_round", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("state_version", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("state_data", sa.JSON(), server_default=sa.text("'{}'::json"), nullable=False))
        batch.add_column(sa.Column("last_event_id", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("estimated_cost", sa.Float(), server_default="0", nullable=False))
        batch.add_column(sa.Column("is_read_only", sa.Boolean(), server_default=sa.false(), nullable=False))
    # 旧引擎没有足够事件来安全恢复，保留为只读历史；迁移后新建会话使用模型默认值。
    op.execute("UPDATE game_sessions SET mode = 'LEGACY', is_read_only = TRUE")

    with op.batch_alter_table("user_game_participants") as batch:
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=True)
        batch.add_column(sa.Column("character_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("participant_type", sa.String(16), server_default="AI", nullable=False))
        batch.add_column(sa.Column("is_ready", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("is_online", sa.Boolean(), server_default=sa.false(), nullable=False))
        batch.add_column(sa.Column("search_actions_remaining", sa.Integer(), server_default="2", nullable=False))
        batch.create_foreign_key("fk_participant_character", "characters", ["character_id"], ["id"], ondelete="CASCADE")
        batch.create_unique_constraint("uq_session_character", ["session_id", "character_id"])
        batch.create_check_constraint("ck_participant_type", "participant_type in ('HUMAN', 'AI')")
    op.create_index("uq_one_human_per_session", "user_game_participants", ["session_id"], unique=True, postgresql_where=sa.text("participant_type = 'HUMAN'"))

    with op.batch_alter_table("game_events") as batch:
        batch.add_column(sa.Column("event_version", sa.Integer(), server_default="1", nullable=False))
        batch.add_column(sa.Column("event_sequence", sa.Integer(), server_default="0", nullable=False))
        batch.add_column(sa.Column("visibility", sa.String(24), server_default="PUBLIC", nullable=False))
        batch.add_column(sa.Column("recipient_character_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("actor_user_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("idempotency_key", sa.String(100), nullable=True))
        batch.create_foreign_key("fk_event_recipient_character", "characters", ["recipient_character_id"], ["id"])
        batch.create_foreign_key("fk_event_actor_user", "users", ["actor_user_id"], ["id"])
    op.execute("""
        WITH numbered AS (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY timestamp, id) AS seq
            FROM game_events
        )
        UPDATE game_events SET event_sequence = numbered.seq
        FROM numbered WHERE game_events.id = numbered.id
    """)
    op.create_index("idx_game_events_session_sequence", "game_events", ["session_id", "event_sequence"], unique=True)
    op.create_index("idx_game_events_session_idempotency", "game_events", ["session_id", "idempotency_key"], unique=True)

    op.create_table(
        "participant_evidence",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column("participant_id", sa.Integer(), nullable=False),
        sa.Column("evidence_id", sa.Integer(), nullable=False),
        sa.Column("visibility", sa.String(24), server_default="CHARACTER_PRIVATE", nullable=False),
        sa.Column("discovered_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revealed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["session_id"], ["game_sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["participant_id"], ["user_game_participants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["evidence_id"], ["evidence.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "evidence_id", name="uq_session_discovered_evidence"),
    )
    op.create_index("ix_participant_evidence_session_id", "participant_evidence", ["session_id"])
    op.create_table(
        "fusion_votes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("session_id", sa.String(100), nullable=False),
        sa.Column("round_number", sa.Integer(), server_default="1", nullable=False),
        sa.Column("voter_participant_id", sa.Integer(), nullable=False),
        sa.Column("suspect_character_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["game_sessions.session_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["voter_participant_id"], ["user_game_participants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["suspect_character_id"], ["characters.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("session_id", "round_number", "voter_participant_id", name="uq_vote_per_round"),
    )


def downgrade() -> None:
    op.drop_table("fusion_votes")
    op.drop_table("participant_evidence")
    op.drop_index("idx_game_events_session_idempotency", table_name="game_events")
    op.drop_index("idx_game_events_session_sequence", table_name="game_events")
    with op.batch_alter_table("game_events") as batch:
        batch.drop_column("idempotency_key")
        batch.drop_column("actor_user_id")
        batch.drop_column("recipient_character_id")
        batch.drop_column("visibility")
        batch.drop_column("event_sequence")
        batch.drop_column("event_version")
    op.drop_index("uq_one_human_per_session", table_name="user_game_participants")
    with op.batch_alter_table("user_game_participants") as batch:
        batch.drop_constraint("ck_participant_type", type_="check")
        batch.drop_constraint("uq_session_character", type_="unique")
        batch.drop_constraint("fk_participant_character", type_="foreignkey")
        batch.drop_column("search_actions_remaining")
        batch.drop_column("is_online")
        batch.drop_column("is_ready")
        batch.drop_column("participant_type")
        batch.drop_column("character_id")
        batch.alter_column("user_id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("game_sessions") as batch:
        for name in ("is_read_only", "estimated_cost", "completion_tokens", "prompt_tokens", "last_event_id", "state_data", "state_version", "current_round", "current_phase", "mode"):
            batch.drop_column(name)
