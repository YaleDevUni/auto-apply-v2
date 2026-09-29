"""initial schema — applications · application_state_history · runs (T0.2)

SQLite(D3) 초기 스키마. §A3·§A7·§A8·§A9 테이블은 해당 마일스톤에서 리비전을 더한다.

Revision ID: 0001
Revises:
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "applications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("last_event_id", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_applications_last_event_id", "applications", ["last_event_id"])

    op.create_table(
        "application_state_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("application_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_application_state_history_application_id",
        "application_state_history",
        ["application_id"],
    )

    op.create_table(
        "runs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("application_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["application_id"], ["applications.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runs_application_id", "runs", ["application_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_application_id", table_name="runs")
    op.drop_table("runs")
    op.drop_index(
        "ix_application_state_history_application_id", table_name="application_state_history"
    )
    op.drop_table("application_state_history")
    op.drop_index("ix_applications_last_event_id", table_name="applications")
    op.drop_table("applications")
