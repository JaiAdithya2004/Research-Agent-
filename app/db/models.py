"""SQLAlchemy 2.0 tables.

Immutability (SPEC criteria 9 and 13) is enforced by database triggers created in the
Alembic migration: runs.raw_answer and runs.raw_grounding cannot be changed once written,
and page_snapshots and reviews rows cannot be updated or deleted.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

FETCH_STATUSES = ("ok", "blocked", "unreachable", "paywalled", "too_large", "error")
SOURCE_TYPES = ("own_site", "competitor", "review", "news", "forum", "other")
CITATION_SOURCES = ("grounding_metadata", "answer_text")
AUDIT_STATUSES = ("running", "complete", "capped", "failed")
RUN_STATUSES = ("pending", "ok", "failed")
VERDICT_STATUSES = ("verified", "flagged")
REVIEW_ACTIONS = ("approve", "correct", "reject")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Audit(Base):
    __tablename__ = "audits"
    __table_args__ = (
        CheckConstraint(_in("status", AUDIT_STATUSES), name="ck_audits_status"),
        # An Idempotency-Key is scoped to the API key that sent it, so two clients can never
        # collide or see each other's audits. Rows without a key (legacy) stay globally unique.
        Index(
            "uq_audits_key_idempotency",
            "api_key_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("api_key_id IS NOT NULL"),
        ),
        Index(
            "uq_audits_idempotency_unscoped",
            "idempotency_key",
            unique=True,
            postgresql_where=text("api_key_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    # No foreign key on purpose: deleting a key must not erase the audits it made.
    api_key_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), index=True)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    brand: Mapped[str] = mapped_column(String(255), nullable=False, default="", index=True)
    caps_hit: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cost_cap_usd: Mapped[float] = mapped_column(Numeric(10, 4), nullable=False, default=0.75)
    total_cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    search_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Per-audit event counter; advanced under a row lock, so event ids are gapless and ordered.
    event_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    runs: Mapped[list["Run"]] = relationship(back_populates="audit")


class Run(Base):
    """One LLM call: question x model x repeat x search mode.

    raw_answer and raw_grounding are write-once (trigger-enforced). mention, verdict_* hold
    derived data that a newer extractor version may legitimately recompute.
    """

    __tablename__ = "runs"
    __table_args__ = (
        CheckConstraint(_in("status", RUN_STATUSES), name="ck_runs_status"),
        CheckConstraint(
            _in("search_mode", ("off", "grounded", "tavily")), name="ck_runs_search_mode"
        ),
        CheckConstraint(
            "verdict_status IS NULL OR " + _in("verdict_status", VERDICT_STATUSES),
            name="ck_runs_verdict_status",
        ),
        # Steps are idempotent per question/model/repeat (SPEC failure modes table).
        UniqueConstraint(
            "audit_id",
            "question_idx",
            "model",
            "repeat_idx",
            "search_mode",
            name="uq_runs_step",
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    audit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("audits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    question_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    model: Mapped[str] = mapped_column(String(128), nullable=False)
    repeat_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    search_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    error: Mapped[str | None] = mapped_column(Text)

    raw_answer: Mapped[str | None] = mapped_column(Text)
    raw_grounding: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    extractor_version: Mapped[str | None] = mapped_column(String(32))
    mention: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verdict_status: Mapped[str | None] = mapped_column(String(16))
    verdict_reason: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    degraded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    search_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    latency_ms: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False, default=0)
    created_at: Mapped[datetime] = _created_at()

    audit: Mapped[Audit] = relationship(back_populates="runs")
    citations: Mapped[list["Citation"]] = relationship(back_populates="run")
    reviews: Mapped[list["Review"]] = relationship(back_populates="run")


class Citation(Base):
    __tablename__ = "citations"
    __table_args__ = (CheckConstraint(_in("source", CITATION_SOURCES), name="ck_citations_source"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[Run] = relationship(back_populates="citations")


class PageSnapshot(Base):
    """Append-only record of a fetch attempt, so any score can be reproduced."""

    __tablename__ = "page_snapshots"
    __table_args__ = (CheckConstraint(_in("status", FETCH_STATUSES), name="ck_snapshots_status"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    url: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    final_url: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(String(64), index=True)


class SourceEvidence(Base):
    __tablename__ = "source_evidence"
    __table_args__ = (
        CheckConstraint(_in("fetch_status", FETCH_STATUSES), name="ck_evidence_fetch_status"),
        CheckConstraint(_in("source_type", SOURCE_TYPES), name="ck_evidence_source_type"),
        # SPEC criterion 14: unfetched pages are never "brand absent".
        CheckConstraint(
            "fetch_status = 'ok' OR brand_on_page IS NULL", name="ck_evidence_unfetched_is_null"
        ),
        CheckConstraint(
            "quote IS NULL OR brand_on_page IS TRUE", name="ck_evidence_quote_needs_brand"
        ),
        # One evidence row per URL per audit: makes research idempotent across retried jobs.
        UniqueConstraint("audit_id", "url", name="uq_evidence_audit_url"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    audit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("audits.id", ondelete="CASCADE"), nullable=False, index=True
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    final_url: Mapped[str | None] = mapped_column(Text)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    fetch_status: Mapped[str] = mapped_column(String(16), nullable=False)
    brand_on_page: Mapped[bool | None] = mapped_column(Boolean)
    quote: Mapped[str | None] = mapped_column(Text)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("page_snapshots.id", ondelete="RESTRICT")
    )
    cited_domain: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    fetch_reason: Mapped[str] = mapped_column(String(48), nullable=False, default="")
    classification: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    classification_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    brand_check_reason: Mapped[str] = mapped_column(Text, nullable=False, default="")


class Review(Base):
    """Human reviews live apart from runs so raw and derived data are never touched."""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint(_in("action", REVIEW_ACTIONS), name="ck_reviews_action"),
        CheckConstraint(
            "action <> 'correct' OR corrected_value IS NOT NULL", name="ck_reviews_correct_value"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    corrected_value: Mapped[str | None] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    reviewer: Mapped[str] = mapped_column(String(255), nullable=False)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    run: Mapped[Run] = relationship(back_populates="reviews")


class EvalRun(Base):
    """One run of the eval suites for a prompt version (all repeats). Append-only history."""

    __tablename__ = "eval_runs"

    id: Mapped[uuid.UUID] = _uuid_pk()
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    classifier_prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    repeats: Mapped[int] = mapped_column(Integer, nullable=False)
    extractor_model: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    result: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class AuditEvent(Base):
    """Durable progress events. (audit_id, seq) is the SSE event id."""

    __tablename__ = "audit_events"

    audit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("audits.id", ondelete="CASCADE"), primary_key=True
    )
    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = _created_at()


class ApiKey(Base):
    """A client credential. Only a keyed hash of the key is stored, never the key."""

    __tablename__ = "api_keys"

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)  # for recognition only
    key_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    daily_budget_usd: Mapped[float | None] = mapped_column(Numeric(10, 4))
    rate_limit_per_min: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _created_at()
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
