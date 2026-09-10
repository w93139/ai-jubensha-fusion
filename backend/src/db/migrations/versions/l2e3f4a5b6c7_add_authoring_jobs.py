"""add persistent, fenced authoring jobs and at-most-once call ledger

Revision ID: l2e3f4a5b6c7
Revises: k1d2e3f4a5b6
"""
from alembic import op
import sqlalchemy as sa

revision = "l2e3f4a5b6c7"
down_revision = "k1d2e3f4a5b6"
branch_labels = None
depends_on = None


def _base_columns():
    return [sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False)]


def upgrade():
    op.create_table(
        "authoring_jobs", *_base_columns(),
        sa.Column("submitted_by", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("idempotency_key", sa.String(96), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False), sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("context_json", sa.Text(), nullable=False), sa.Column("context_hash", sa.String(64), nullable=False),
        sa.Column("model_snapshot_json", sa.Text(), nullable=False), sa.Column("model_snapshot_hash", sa.String(64), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False), sa.Column("step", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("lease_token", sa.String(64), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("candidate_version_id", sa.Integer(), sa.ForeignKey("script_package_versions.id"), nullable=True),
        sa.Column("source_report_hash", sa.String(64), nullable=True), sa.Column("error_code", sa.String(96), nullable=True),
        sa.Column("state_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("submitted_by", "idempotency_key", name="uq_authoring_job_actor_key"),
        sa.CheckConstraint("state IN ('QUEUED','RUNNING','NEEDS_RECONCILIATION','BLOCKED','COMPLETED','CANCELLED')", name="ck_authoring_job_state"),
        sa.CheckConstraint("step IN ('COMPILE','CANDIDATE','AUDIT','DONE')", name="ck_authoring_job_step"),
        sa.CheckConstraint("revision >= 0", name="ck_authoring_job_revision"),
    )
    op.create_table(
        "authoring_attempts", *_base_columns(),
        sa.Column("job_id", sa.Integer(), sa.ForeignKey("authoring_jobs.id"), nullable=False),
        sa.Column("step", sa.String(16), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("prepared_json", sa.Text(), nullable=False), sa.Column("prepared_hash", sa.String(64), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=True), sa.Column("output_hash", sa.String(64), nullable=True),
        sa.Column("receipt_json", sa.Text(), nullable=True), sa.Column("receipt_hash", sa.String(64), nullable=True),
        sa.Column("error_code", sa.String(96), nullable=True), sa.Column("state_hash", sa.String(64), nullable=False),
        sa.UniqueConstraint("job_id", "step", name="uq_authoring_attempt_job_step"),
        sa.CheckConstraint("step IN ('COMPILE','AUDIT')", name="ck_authoring_attempt_step"),
        sa.CheckConstraint("status IN ('RESERVED','IN_FLIGHT','SUCCEEDED','FAILED','UNKNOWN')", name="ck_authoring_attempt_status"),
    )
    op.create_index("ix_authoring_attempts_job_id", "authoring_attempts", ["job_id"])


def downgrade():
    op.drop_index("ix_authoring_attempts_job_id", table_name="authoring_attempts")
    op.drop_table("authoring_attempts")
    op.drop_table("authoring_jobs")
