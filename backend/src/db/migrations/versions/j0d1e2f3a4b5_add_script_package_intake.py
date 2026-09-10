"""add private candidate versions and deterministic import records

Revision ID: j0d1e2f3a4b5
Revises: i9c0d1e2f3a4
"""
from alembic import op
import sqlalchemy as sa


revision = "j0d1e2f3a4b5"
down_revision = "i9c0d1e2f3a4"
branch_labels = None
depends_on = None


def _base_columns() -> list[sa.Column]:
    return [sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "script_package_versions", *_base_columns(),
        sa.Column("script_key", sa.String(96), nullable=False),
        sa.Column("content_version", sa.String(96), nullable=False),
        sa.Column("package_hash", sa.String(64), nullable=False),
        sa.Column("manifest_hash", sa.String(64), nullable=False),
        sa.Column("contract_version", sa.String(64), nullable=False),
        sa.Column("canonical_version", sa.String(64), nullable=False),
        sa.Column("package_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("script_key", "content_version", name="uq_script_package_content_version"),
    )
    op.create_table(
        "script_import_jobs", *_base_columns(),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("script_package_versions.id")),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("step", sa.String(32), nullable=False),
        sa.Column("validator_version", sa.String(64), nullable=False),
        sa.Column("report_hash", sa.String(64), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_import_actor_key"),
        sa.CheckConstraint("(status = 'SUCCEEDED' AND version_id IS NOT NULL) OR "
                           "(status = 'BLOCKED' AND version_id IS NULL)", name="ck_script_import_outcome"),
        sa.CheckConstraint("step = 'DETERMINISTIC_VALIDATION'", name="ck_script_import_step"),
    )


def downgrade() -> None:
    op.drop_table("script_import_jobs")
    op.drop_table("script_package_versions")
