"""initial schema

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE TYPE client_level_enum AS ENUM ('bronze', 'silver', 'gold')")
    op.execute("CREATE TYPE transaction_type_enum AS ENUM ('accrual', 'redemption')")
    op.execute("CREATE TYPE location_enum AS ENUM ('fuel', 'base')")
    op.execute("CREATE TYPE location_enum2 AS ENUM ('fuel', 'base')")
    op.execute("CREATE TYPE client_level_enum2 AS ENUM ('bronze', 'silver', 'gold')")
    op.execute("CREATE TYPE accrual_type_enum AS ENUM ('percent', 'bonus_per_liter', 'fixed')")

    op.create_table(
        "clients",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("phone", sa.String(20), nullable=False),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("bonus_balance", sa.Numeric(12, 2), nullable=False, server_default="0"),
        sa.Column("level", sa.Enum("bronze", "silver", "gold", name="client_level_enum"), nullable=False, server_default="bronze"),
        sa.Column("total_spent", sa.Numeric(14, 2), nullable=False, server_default="0"),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("reg_date", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("phone"),
    )
    op.create_index("ix_clients_phone", "clients", ["phone"])

    op.create_table(
        "cashiers",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("azs_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "transactions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("type", sa.Enum("accrual", "redemption", name="transaction_type_enum"), nullable=False),
        sa.Column("amount_bonus", sa.Numeric(12, 2), nullable=False),
        sa.Column("purchase_amount", sa.Numeric(12, 2), nullable=True),
        sa.Column("location", sa.Enum("fuel", "base", name="location_enum"), nullable=False),
        sa.Column("fuel_liters", sa.Numeric(10, 3), nullable=True),
        sa.Column("cashier_id", sa.BigInteger(), nullable=True),
        sa.Column("check_id", sa.String(100), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.ForeignKeyConstraint(["cashier_id"], ["cashiers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("check_id"),
    )
    op.create_index("ix_transactions_client_id", "transactions", ["client_id"])
    op.create_index("ix_transactions_ts", "transactions", ["ts"])
    op.create_index("ix_transactions_check_id", "transactions", ["check_id"])

    op.create_table(
        "accrual_rules",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("location", sa.Enum("fuel", "base", name="location_enum2"), nullable=False),
        sa.Column("client_level", sa.Enum("bronze", "silver", "gold", name="client_level_enum2"), nullable=False),
        sa.Column("accrual_type", sa.Enum("percent", "bonus_per_liter", "fixed", name="accrual_type_enum"), nullable=False),
        sa.Column("accrual_value", sa.Numeric(10, 4), nullable=False),
        sa.Column("min_purchase", sa.Numeric(10, 2), nullable=False, server_default="0"),
        sa.Column("active_from", sa.Date(), nullable=False),
        sa.Column("active_to", sa.Date(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "campaigns",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("multiplier", sa.Numeric(5, 2), nullable=False, server_default="1"),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("days_of_week", sa.String(20), nullable=True),
        sa.Column("hours_start", sa.Integer(), nullable=True),
        sa.Column("hours_end", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "bonus_expiry",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("bonus_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("expiry_date", sa.Date(), nullable=False),
        sa.Column("remaining", sa.Numeric(12, 2), nullable=False),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bonus_expiry_client_id", "bonus_expiry", ["client_id"])
    op.create_index("ix_bonus_expiry_expiry_date", "bonus_expiry", ["expiry_date"])

    op.create_table(
        "fuel_sales",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("sale_date", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("cashier_name", sa.String(200), nullable=True),
        sa.Column("fuel_type", sa.String(50), nullable=True),
        sa.Column("liters", sa.Numeric(10, 3), nullable=False),
        sa.Column("price_per_liter", sa.Numeric(8, 2), nullable=False),
        sa.Column("total_rub", sa.Numeric(12, 2), nullable=False),
        sa.Column("client_id", sa.BigInteger(), nullable=True),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fuel_sales_sale_date", "fuel_sales", ["sale_date"])


def downgrade() -> None:
    op.drop_table("fuel_sales")
    op.drop_table("bonus_expiry")
    op.drop_table("campaigns")
    op.drop_table("accrual_rules")
    op.drop_table("transactions")
    op.drop_table("cashiers")
    op.drop_table("clients")
    op.execute("DROP TYPE IF EXISTS accrual_type_enum")
    op.execute("DROP TYPE IF EXISTS client_level_enum2")
    op.execute("DROP TYPE IF EXISTS location_enum2")
    op.execute("DROP TYPE IF EXISTS location_enum")
    op.execute("DROP TYPE IF EXISTS transaction_type_enum")
    op.execute("DROP TYPE IF EXISTS client_level_enum")
