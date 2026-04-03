"""add_baseline_distance_to_sessions

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-04-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e7f8a9b0c1d2"
down_revision: str | Sequence[str] | None = "d6e7f8a9b0c1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "measurement_sessions",
        sa.Column("baseline_distance_mm", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("measurement_sessions", "baseline_distance_mm")
