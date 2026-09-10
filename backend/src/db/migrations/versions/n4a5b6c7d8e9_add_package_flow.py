"""Add explicit rules previews and immutable action history.

Revision ID: n4a5b6c7d8e9
Revises: m3f4a5b6c7d8
"""
from alembic import op
import sqlalchemy as sa

revision = "n4a5b6c7d8e9"
down_revision = "m3f4a5b6c7d8"
branch_labels = None
depends_on = None


def _base():
    return [sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False)]


def upgrade():
    op.create_table("script_package_flows", *_base(),
                    sa.Column("flow_id", sa.String(37), nullable=False),
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
                    sa.UniqueConstraint("flow_id"),
                    sa.UniqueConstraint("opening_session_id"),
                    sa.UniqueConstraint("owner_user_id", "idempotency_key", name="uq_package_flow_actor_key"))
    op.create_table("script_package_flow_actions", *_base(),
                    sa.Column("flow_id", sa.String(37), sa.ForeignKey("script_package_flows.flow_id"), nullable=False),
                    sa.Column("revision", sa.Integer(), nullable=False),
                    sa.Column("idempotency_key", sa.String(96), nullable=False),
                    sa.Column("request_json", sa.Text(), nullable=False),
                    *[sa.Column(name, sa.String(64), nullable=False) for name in
                      ("request_hash", "previous_event_hash", "state_hash", "event_hash")],
                    sa.Column("event_json", sa.Text(), nullable=False),
                    sa.CheckConstraint("revision > 0", name="ck_package_flow_action_revision_positive"),
                    sa.UniqueConstraint("flow_id", "revision", name="uq_package_flow_action_revision"),
                    sa.UniqueConstraint("flow_id", "idempotency_key", name="uq_package_flow_action_key"))


def downgrade():
    op.drop_table("script_package_flow_actions")
    op.drop_table("script_package_flows")
