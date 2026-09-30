"""v3 state machine — applications url·domain·submit_mode, runs result·tokens (T3.1)

§A3 v3 상태기계. 이력 run_id 는 nullable 로(사람 조작 전이). 기존 applications 는 0행 가정
(M1 이후 v2 상태로 쓴 곳이 없다) — 상태값은 옮기지 않는다.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("applications") as batch:
        batch.add_column(sa.Column("url", sa.String(), server_default="", nullable=False))
        batch.add_column(sa.Column("domain", sa.String(), server_default="", nullable=False))
        batch.add_column(
            sa.Column("submit_mode", sa.String(), server_default="dry_run", nullable=False)
        )
        batch.create_index("ix_applications_domain", ["domain"])

    with op.batch_alter_table("application_state_history") as batch:
        batch.alter_column("run_id", existing_type=sa.String(), nullable=True)

    with op.batch_alter_table("runs") as batch:
        batch.add_column(sa.Column("result", sa.String(), nullable=True))
        batch.add_column(
            sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(
            sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False)
        )
        batch.add_column(sa.Column("transcript_path", sa.String(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("transcript_path")
        batch.drop_column("output_tokens")
        batch.drop_column("input_tokens")
        batch.drop_column("result")

    # 0002 는 run_id NOT NULL — 사람 조작 전이(run 없음)는 빈 문자열로 남긴다.
    op.execute("UPDATE application_state_history SET run_id = '' WHERE run_id IS NULL")
    with op.batch_alter_table("application_state_history") as batch:
        batch.alter_column("run_id", existing_type=sa.String(), nullable=False)

    with op.batch_alter_table("applications") as batch:
        batch.drop_index("ix_applications_domain")
        batch.drop_column("submit_mode")
        batch.drop_column("domain")
        batch.drop_column("url")
