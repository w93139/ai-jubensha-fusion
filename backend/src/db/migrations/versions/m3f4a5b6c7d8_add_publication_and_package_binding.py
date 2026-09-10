"""Add immutable publication approvals, releases and opening bindings.

Revision ID: m3f4a5b6c7d8
Revises: l2e3f4a5b6c7
"""
from alembic import op
import sqlalchemy as sa

revision = "m3f4a5b6c7d8"
down_revision = "l2e3f4a5b6c7"
branch_labels = None
depends_on = None


def _base():
    return [sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False)]


def _publication():
    return [sa.Column("version_id", sa.Integer(), sa.ForeignKey("script_package_versions.id"), nullable=False),
            sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("idempotency_key", sa.String(96), nullable=False),
            *[sa.Column(name, sa.String(64), nullable=False) for name in
              ("input_hash", "package_hash", "bundle_hash", "basis_hash", "source_report_hash")]]


def upgrade():
    op.create_table("script_publication_approvals", *_base(), *_publication(),
                    sa.Column("approval_hash", sa.String(64), nullable=False),
                    sa.Column("approval_json", sa.Text(), nullable=False),
                    sa.UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_approval_actor_key"))
    op.create_index("ix_script_publication_approvals_version_id", "script_publication_approvals", ["version_id"])
    op.create_table("script_package_releases", *_base(), *_publication(),
                    sa.Column("approval_id", sa.Integer(), sa.ForeignKey("script_publication_approvals.id"), nullable=False),
                    sa.Column("release_hash", sa.String(64), nullable=False),
                    sa.Column("release_json", sa.Text(), nullable=False),
                    sa.UniqueConstraint("version_id", name="uq_script_release_version"),
                    sa.UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_release_actor_key"))
    op.create_table("script_package_play_sessions", *_base(),
                    sa.Column("session_id", sa.String(40), nullable=False),
                    sa.Column("owner_user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
                    sa.Column("release_id", sa.Integer(), sa.ForeignKey("script_package_releases.id"), nullable=False),
                    sa.Column("version_id", sa.Integer(), sa.ForeignKey("script_package_versions.id"), nullable=False),
                    sa.Column("package_hash", sa.String(64), nullable=False),
                    sa.Column("selected_character_id", sa.String(96), nullable=False),
                    sa.Column("idempotency_key", sa.String(96), nullable=False),
                    sa.Column("request_hash", sa.String(64), nullable=False),
                    sa.Column("binding_json", sa.Text(), nullable=False),
                    sa.Column("binding_hash", sa.String(64), nullable=False),
                    sa.UniqueConstraint("session_id"),
                    sa.UniqueConstraint("owner_user_id", "idempotency_key", name="uq_package_play_actor_key"))


def downgrade():
    op.drop_table("script_package_play_sessions")
    op.drop_table("script_package_releases")
    op.drop_index("ix_script_publication_approvals_version_id", table_name="script_publication_approvals")
    op.drop_table("script_publication_approvals")
