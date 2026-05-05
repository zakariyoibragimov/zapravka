"""news items broadcast

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "news_items",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_news_items_is_active"), "news_items", ["is_active"], unique=False)
    op.create_index(op.f("ix_news_items_created_at"), "news_items", ["created_at"], unique=False)
    op.create_index(op.f("ix_news_items_published_at"), "news_items", ["published_at"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_news_items_published_at"), table_name="news_items")
    op.drop_index(op.f("ix_news_items_created_at"), table_name="news_items")
    op.drop_index(op.f("ix_news_items_is_active"), table_name="news_items")
    op.drop_table("news_items")