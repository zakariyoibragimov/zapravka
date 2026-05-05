"""client mobile password auth

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-29 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "clients",
        sa.Column("phone_verified", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column("clients", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.execute("UPDATE clients SET phone_verified = true")
    op.alter_column("clients", "phone_verified", server_default=None)


def downgrade() -> None:
    op.drop_column("clients", "password_hash")
    op.drop_column("clients", "phone_verified")
