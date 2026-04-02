"""add_measurement_tables

Revision ID: b3c4d5e6f7a8
Revises: a1b2c3d4e5f6
Create Date: 2026-04-02 14:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b3c4d5e6f7a8"
down_revision: str | Sequence[str] | None = "a1b2c3d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "measurement_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("device_id", sa.String(64), nullable=False),
        sa.Column("mode", sa.String(8), nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=True),
        sa.Column("cycle_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("step_period_s", sa.Float(), nullable=False),
        sa.Column("sample_interval_ms", sa.Integer(), nullable=False),
        sa.Column("displacement_peak_mm", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_measurement_sessions_device_id", "measurement_sessions", ["device_id"])

    op.create_table(
        "measurement_points",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.Integer(), sa.ForeignKey("measurement_sessions.id"), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("current_pct", sa.Float(), nullable=False),
        sa.Column("distance_pct", sa.Float(), nullable=False),
        sa.Column("distance_mm", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_measurement_points_session_id", "measurement_points", ["session_id"])
    op.create_index("ix_measurement_points_timestamp", "measurement_points", ["timestamp"])


def downgrade() -> None:
    op.drop_index("ix_measurement_points_timestamp", "measurement_points")
    op.drop_index("ix_measurement_points_session_id", "measurement_points")
    op.drop_table("measurement_points")
    op.drop_index("ix_measurement_sessions_device_id", "measurement_sessions")
    op.drop_table("measurement_sessions")
