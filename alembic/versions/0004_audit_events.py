"""audit events

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-03 12:50:40.765586
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_events",
        sa.Column("audit_id", sa.UUID(), nullable=False),
        sa.Column("seq", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["audit_id"], ["audits.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("audit_id", "seq"),
    )
    op.add_column(
        "audits", sa.Column("event_seq", sa.Integer(), server_default="0", nullable=False)
    )


def downgrade() -> None:
    op.drop_column("audits", "event_seq")
    op.drop_table("audit_events")
