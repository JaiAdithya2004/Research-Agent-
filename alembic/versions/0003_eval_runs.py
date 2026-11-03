"""eval runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03 12:41:06.383349
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column("classifier_prompt_version", sa.String(length=64), nullable=False),
        sa.Column("repeats", sa.Integer(), nullable=False),
        sa.Column("extractor_model", sa.String(length=128), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_eval_runs_prompt_version"), "eval_runs", ["prompt_version"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_eval_runs_prompt_version"), table_name="eval_runs")
    op.drop_table("eval_runs")
