"""add fuel_prices table

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-26 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fuel_prices",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(20), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("price_per_liter", sa.Numeric(8, 2), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code"),
    )

    # Стандартные виды топлива
    op.execute("""
        INSERT INTO fuel_prices (code, name, price_per_liter, is_active) VALUES
        ('ai92',   'Бензин АИ-92',  55.00, true),
        ('ai95',   'Бензин АИ-95',  60.00, true),
        ('ai98',   'Бензин АИ-98',  68.00, true),
        ('diesel', 'Дизель',        65.00, true),
        ('gas',    'Газ (LPG)',      32.00, true)
    """)


def downgrade() -> None:
    op.drop_table("fuel_prices")
