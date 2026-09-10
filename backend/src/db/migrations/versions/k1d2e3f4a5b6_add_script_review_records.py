"""add immutable manual audit reports and finding dispositions

Revision ID: k1d2e3f4a5b6
Revises: j0d1e2f3a4b5
"""
from alembic import op
import sqlalchemy as sa

revision = "k1d2e3f4a5b6"
down_revision = "j0d1e2f3a4b5"
branch_labels = None
depends_on = None


def _base_columns() -> list[sa.Column]:
    return [sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "script_audit_records", *_base_columns(),
        sa.Column("version_id", sa.Integer(), sa.ForeignKey("script_package_versions.id"), nullable=False),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("package_hash", sa.String(64), nullable=False),
        sa.Column("bundle_hash", sa.String(64), nullable=False),
        sa.Column("source_report_hash", sa.String(64), nullable=False),
        sa.Column("audit_hash", sa.String(64), nullable=False),
        sa.Column("audit_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_audit_actor_key"),
    )
    op.create_index("ix_script_audit_records_version_id", "script_audit_records", ["version_id"])
    op.create_table(
        "script_finding_dispositions", *_base_columns(),
        sa.Column("audit_id", sa.Integer(), sa.ForeignKey("script_audit_records.id"), nullable=False),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("finding_id", sa.String(96), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("disposition_hash", sa.String(64), nullable=False),
        sa.Column("disposition_json", sa.Text(), nullable=False),
        sa.UniqueConstraint("submitted_by", "idempotency_key", name="uq_script_disposition_actor_key"),
        sa.UniqueConstraint("audit_id", "revision", name="uq_script_disposition_revision"),
        sa.CheckConstraint("revision > 0", name="ck_script_disposition_revision"),
        sa.CheckConstraint("status IN ('OPEN', 'ACKNOWLEDGED', 'DISMISSED')", name="ck_script_disposition_status"),
    )


def downgrade() -> None:
    op.drop_table("script_finding_dispositions")
    op.drop_index("ix_script_audit_records_version_id", table_name="script_audit_records")
    op.drop_table("script_audit_records")
