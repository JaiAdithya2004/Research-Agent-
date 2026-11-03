"""api keys

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-03 13:26:31.330456
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("key_prefix", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("daily_budget_usd", sa.Numeric(precision=10, scale=4), nullable=True),
        sa.Column("rate_limit_per_min", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key_hash"),
    )
    op.add_column("audits", sa.Column("api_key_id", sa.UUID(), nullable=True))
    op.drop_constraint(op.f("uq_audits_idempotency_key"), "audits", type_="unique")
    op.create_index(op.f("ix_audits_api_key_id"), "audits", ["api_key_id"], unique=False)
    op.create_index(
        "uq_audits_idempotency_unscoped",
        "audits",
        ["idempotency_key"],
        unique=True,
        postgresql_where=sa.text("api_key_id IS NULL"),
    )
    op.create_index(
        "uq_audits_key_idempotency",
        "audits",
        ["api_key_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("api_key_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_audits_key_idempotency",
        table_name="audits",
        postgresql_where=sa.text("api_key_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_audits_idempotency_unscoped",
        table_name="audits",
        postgresql_where=sa.text("api_key_id IS NULL"),
    )
    op.drop_index(op.f("ix_audits_api_key_id"), table_name="audits")
    op.create_unique_constraint(
        op.f("uq_audits_idempotency_key"),
        "audits",
        ["idempotency_key"],
        postgresql_nulls_not_distinct=False,
    )
    op.drop_column("audits", "api_key_id")
    op.drop_table("api_keys")
