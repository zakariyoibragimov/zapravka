"""receipt cancellations and action logs

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-27 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("fuel_sales", sa.Column("check_id", sa.String(length=100), nullable=True))
    op.create_index(op.f("ix_fuel_sales_check_id"), "fuel_sales", ["check_id"], unique=False)

    op.create_table(
        "receipt_cancellations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("original_check_id", sa.String(length=100), nullable=False),
        sa.Column("canceled_check_id", sa.String(length=100), nullable=False),
        sa.Column("cashier_id", sa.Integer(), nullable=True),
        sa.Column("client_id", sa.Integer(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["cashier_id"], ["cashiers.id"], name="fk_receipt_cancellations_cashier_id_cashiers"),
        sa.ForeignKeyConstraint(["client_id"], ["clients.id"], name="fk_receipt_cancellations_client_id_clients"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_receipt_cancellations_original_check_id"), "receipt_cancellations", ["original_check_id"], unique=True)
    op.create_index(op.f("ix_receipt_cancellations_canceled_check_id"), "receipt_cancellations", ["canceled_check_id"], unique=True)
    op.create_index(op.f("ix_receipt_cancellations_cashier_id"), "receipt_cancellations", ["cashier_id"], unique=False)
    op.create_index(op.f("ix_receipt_cancellations_client_id"), "receipt_cancellations", ["client_id"], unique=False)
    op.create_index(op.f("ix_receipt_cancellations_created_at"), "receipt_cancellations", ["created_at"], unique=False)

    op.create_table(
        "action_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("actor_type", sa.String(length=20), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("actor_name", sa.String(length=200), nullable=False),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.Integer(), nullable=True),
        sa.Column("check_id", sa.String(length=100), nullable=True),
        sa.Column("details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_action_logs_actor_type"), "action_logs", ["actor_type"], unique=False)
    op.create_index(op.f("ix_action_logs_actor_id"), "action_logs", ["actor_id"], unique=False)
    op.create_index(op.f("ix_action_logs_actor_name"), "action_logs", ["actor_name"], unique=False)
    op.create_index(op.f("ix_action_logs_action"), "action_logs", ["action"], unique=False)
    op.create_index(op.f("ix_action_logs_entity_type"), "action_logs", ["entity_type"], unique=False)
    op.create_index(op.f("ix_action_logs_entity_id"), "action_logs", ["entity_id"], unique=False)
    op.create_index(op.f("ix_action_logs_check_id"), "action_logs", ["check_id"], unique=False)
    op.create_index(op.f("ix_action_logs_created_at"), "action_logs", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_action_logs_created_at"), table_name="action_logs")
    op.drop_index(op.f("ix_action_logs_check_id"), table_name="action_logs")
    op.drop_index(op.f("ix_action_logs_entity_id"), table_name="action_logs")
    op.drop_index(op.f("ix_action_logs_entity_type"), table_name="action_logs")
    op.drop_index(op.f("ix_action_logs_action"), table_name="action_logs")
    op.drop_index(op.f("ix_action_logs_actor_name"), table_name="action_logs")
    op.drop_index(op.f("ix_action_logs_actor_id"), table_name="action_logs")
    op.drop_index(op.f("ix_action_logs_actor_type"), table_name="action_logs")
    op.drop_table("action_logs")

    op.drop_index(op.f("ix_receipt_cancellations_created_at"), table_name="receipt_cancellations")
    op.drop_index(op.f("ix_receipt_cancellations_client_id"), table_name="receipt_cancellations")
    op.drop_index(op.f("ix_receipt_cancellations_cashier_id"), table_name="receipt_cancellations")
    op.drop_index(op.f("ix_receipt_cancellations_canceled_check_id"), table_name="receipt_cancellations")
    op.drop_index(op.f("ix_receipt_cancellations_original_check_id"), table_name="receipt_cancellations")
    op.drop_table("receipt_cancellations")

    op.drop_index(op.f("ix_fuel_sales_check_id"), table_name="fuel_sales")
    op.drop_column("fuel_sales", "check_id")