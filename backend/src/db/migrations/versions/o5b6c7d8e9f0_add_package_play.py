"""Add independent text-play bindings and immutable accounting events.

Revision ID: o5b6c7d8e9f0
Revises: n4a5b6c7d8e9
"""
from alembic import op
import sqlalchemy as sa


revision = "o5b6c7d8e9f0"
down_revision = "n4a5b6c7d8e9"
branch_labels = None
depends_on = None


def _base() -> list:
    return [sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False)]


def upgrade() -> None:
    op.create_table("script_package_plays", *_base(),
                    sa.Column("play_id", sa.String(37), nullable=False),
                    sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("opening_session_id", sa.String(40),
                              sa.ForeignKey("script_package_play_sessions.session_id"), nullable=False),
                    sa.Column("release_id", sa.Integer(), sa.ForeignKey("script_package_releases.id"), nullable=False),
                    sa.Column("version_id", sa.Integer(), sa.ForeignKey("script_package_versions.id"), nullable=False),
                    sa.Column("package_hash", sa.String(64), nullable=False),
                    sa.Column("selected_character_id", sa.String(96), nullable=False),
                    sa.Column("idempotency_key", sa.String(96), nullable=False),
                    sa.Column("request_hash", sa.String(64), nullable=False),
                    sa.Column("binding_json", sa.Text(), nullable=False),
                    sa.Column("binding_hash", sa.String(64), nullable=False),
                    sa.UniqueConstraint("play_id"),
                    sa.UniqueConstraint("opening_session_id"),
                    sa.UniqueConstraint("owner_user_id", "idempotency_key", name="uq_package_play_actor_request"))
    op.create_table("script_package_play_events", *_base(),
                    sa.Column("play_id", sa.String(37), sa.ForeignKey("script_package_plays.play_id"), nullable=False),
                    sa.Column("revision", sa.Integer(), nullable=False),
                    sa.Column("kind", sa.String(32), nullable=False),
                    sa.Column("idempotency_key", sa.String(96), nullable=False),
                    sa.Column("request_json", sa.Text(), nullable=False),
                    *[sa.Column(name, sa.String(64), nullable=False) for name in
                      ("request_hash", "previous_event_hash", "state_hash", "event_hash")],
                    sa.Column("event_json", sa.Text(), nullable=False),
                    sa.UniqueConstraint("play_id", "revision", name="uq_package_play_event_revision"),
                    sa.UniqueConstraint("play_id", "idempotency_key", name="uq_package_play_event_key"),
                    sa.CheckConstraint("revision > 0", name="ck_package_play_event_revision_positive"),
                    sa.CheckConstraint("kind IN ('ACTION', 'AI_REQUEST', 'AI_RESULT')", name="ck_package_play_event_kind"))


def downgrade() -> None:
    op.drop_table("script_package_play_events")
    op.drop_table("script_package_plays")
