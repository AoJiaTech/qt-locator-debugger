"""add_port_config_to_devices

Revision ID: a1b2c3d4e5f6
Revises: 19637f07036c
Create Date: 2026-04-02 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "19637f07036c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("port", sa.String(32), nullable=True))
    op.add_column("devices", sa.Column("baudrate", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("bytesize", sa.Integer(), nullable=True))
    op.add_column("devices", sa.Column("parity", sa.String(4), nullable=True))
    op.add_column("devices", sa.Column("stopbits", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("devices", "stopbits")
    op.drop_column("devices", "parity")
    op.drop_column("devices", "bytesize")
    op.drop_column("devices", "baudrate")
    op.drop_column("devices", "port")
