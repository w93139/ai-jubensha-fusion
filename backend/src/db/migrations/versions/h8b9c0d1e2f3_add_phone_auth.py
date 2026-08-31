"""add phone authentication

Revision ID: h8b9c0d1e2f3
Revises: f7a8b9c0d1e2
"""
from alembic import op
import sqlalchemy as sa

revision = "h8b9c0d1e2f3"
down_revision = "f7a8b9c0d1e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.add_column(sa.Column("phone", sa.String(length=20), nullable=True, comment="手机号"))
        batch.create_index("ix_users_phone", ["phone"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_phone")
        batch.drop_column("phone")
