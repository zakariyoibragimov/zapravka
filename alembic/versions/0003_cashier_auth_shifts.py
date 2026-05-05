"""cashier auth + shifts

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # cashiers: username/password_hash
    op.add_column("cashiers", sa.Column("username", sa.String(length=64), nullable=True))
    op.add_column("cashiers", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.create_index(op.f("ix_cashiers_username"), "cashiers", ["username"], unique=True)

    # fuel_sales: cashier_id
    op.add_column("fuel_sales", sa.Column("cashier_id", sa.Integer(), nullable=True))
    op.create_index(op.f("ix_fuel_sales_cashier_id"), "fuel_sales", ["cashier_id"], unique=False)
    op.create_foreign_key(
        "fk_fuel_sales_cashier_id_cashiers",
        "fuel_sales",
        "cashiers",
        ["cashier_id"],
        ["id"],
    )

    # cashier_shifts
    op.create_table(
        "cashier_shifts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cashier_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["cashier_id"], ["cashiers.id"], name="fk_cashier_shifts_cashier_id_cashiers"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_cashier_shifts_cashier_id"), "cashier_shifts", ["cashier_id"], unique=False)
    op.create_index(op.f("ix_cashier_shifts_started_at"), "cashier_shifts", ["started_at"], unique=False)
    op.create_index(op.f("ix_cashier_shifts_ended_at"), "cashier_shifts", ["ended_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_cashier_shifts_ended_at"), table_name="cashier_shifts")
    op.drop_index(op.f("ix_cashier_shifts_started_at"), table_name="cashier_shifts")
    op.drop_index(op.f("ix_cashier_shifts_cashier_id"), table_name="cashier_shifts")
    op.drop_table("cashier_shifts")

    op.drop_constraint("fk_fuel_sales_cashier_id_cashiers", "fuel_sales", type_="foreignkey")
    op.drop_index(op.f("ix_fuel_sales_cashier_id"), table_name="fuel_sales")
    op.drop_column("fuel_sales", "cashier_id")

    op.drop_index(op.f("ix_cashiers_username"), table_name="cashiers")
    op.drop_column("cashiers", "password_hash")
    op.drop_column("cashiers", "username")
