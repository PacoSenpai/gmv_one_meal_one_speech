"""remove invitation-based registration

Revision ID: 7c4e1a2b9d30
Revises: e9b531d276c4
Create Date: 2026-10-19 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "7c4e1a2b9d30"
down_revision = "e9b531d276c4"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("invitation")


def downgrade():
    op.create_table(
        "invitation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
