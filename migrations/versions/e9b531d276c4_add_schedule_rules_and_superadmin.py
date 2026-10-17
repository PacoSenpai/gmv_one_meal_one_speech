"""add recurring schedule rules and protected superadmin

Revision ID: e9b531d276c4
Revises: ff79c99766f1
Create Date: 2026-10-17 17:00:00.000000

"""

from datetime import time

from alembic import op
import sqlalchemy as sa


revision = "e9b531d276c4"
down_revision = "ff79c99766f1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "user",
        sa.Column("is_superadmin", sa.Boolean(), server_default=sa.false(), nullable=False),
    )

    op.create_table(
        "schedule_setting",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("default_time", sa.Time(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "schedule_rule",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("occurrence", sa.Integer(), nullable=False),
        sa.CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_schedule_rule_weekday"),
        sa.CheckConstraint("occurrence >= 1 AND occurrence <= 5", name="ck_schedule_rule_occurrence"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("weekday", "occurrence", name="uq_schedule_rule_weekday_occurrence"),
    )

    op.bulk_insert(
        sa.table("schedule_setting", sa.column("id", sa.Integer), sa.column("default_time", sa.Time)),
        [{"id": 1, "default_time": time(13, 30)}],
    )
    op.bulk_insert(
        sa.table(
            "schedule_rule",
            sa.column("weekday", sa.Integer),
            sa.column("occurrence", sa.Integer),
        ),
        [{"weekday": 1, "occurrence": 1}],
    )

    user_table = sa.table(
        "user",
        sa.column("id", sa.Integer),
        sa.column("role", sa.String),
        sa.column("is_active", sa.Boolean),
        sa.column("is_superadmin", sa.Boolean),
    )
    first_admin_id = op.get_bind().execute(
        sa.select(user_table.c.id)
        .where(user_table.c.role == "admin", user_table.c.is_active.is_(True))
        .order_by(user_table.c.id.asc())
        .limit(1)
    ).scalar_one_or_none()
    if first_admin_id is not None:
        op.get_bind().execute(
            user_table.update()
            .where(user_table.c.id == first_admin_id)
            .values(is_superadmin=True)
        )


def downgrade():
    op.drop_table("schedule_rule")
    op.drop_table("schedule_setting")
    op.drop_column("user", "is_superadmin")
