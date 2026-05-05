"""client profile fields

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-26 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("clients", sa.Column("first_name", sa.String(length=100), nullable=True))
    op.add_column("clients", sa.Column("last_name", sa.String(length=100), nullable=True))
    op.add_column("clients", sa.Column("patronymic", sa.String(length=100), nullable=True))
    op.add_column("clients", sa.Column("photo_data_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("clients", "photo_data_url")
    op.drop_column("clients", "patronymic")
    op.drop_column("clients", "last_name")
    op.drop_column("clients", "first_name")