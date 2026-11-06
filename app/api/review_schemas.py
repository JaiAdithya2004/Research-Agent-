"""Response and request models for audit listing, review, the review queue and run detail."""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from app.audit.review import parse_correction
from app.schemas import (
    AuditStatus,
    AuditSummary,
    CitationSource,
    FetchStatus,
    Mention,
    ReviewAction,
    RunSearchMode,
    SearchMode,
    SourceType,
    Verdict,
)

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- audit list ---


class AuditListItem(_Out):
    id: str
    brand: str
    status: AuditStatus
    created_at: datetime
    completed_at: datetime | None
    search_mode: SearchMode
    mention_rate: float | None  # null until a run has finished
    flagged_count: int
    total_cost_usd: float
    degraded: bool
    total_runs: int
    failed_runs: int
    caps_hit: list[str]


class AuditListPage(_Out):
    items: list[AuditListItem]
    next_cursor: str | None


# --- review ---


class ReviewIn(BaseModel):
    """Who is reviewing is NOT part of the request: it comes from the authenticated key."""

    model_config = ConfigDict(extra="forbid")

    action: ReviewAction
    corrected_value: str | None = None
    reason: NonBlank

    @model_validator(mode="after")
    def _check_correction(self) -> Self:
        if self.action == "correct":
            if self.corrected_value is None:
                raise ValueError("corrected_value is required when action is 'correct'")
            parse_correction(self.corrected_value)  # raises ValueError with a helpful message
        elif self.corrected_value is not None:
            raise ValueError("corrected_value is only allowed when action is 'correct'")
        return self


class ReviewOut(_Out):
    id: str
    run_id: str
    audit_id: str
    action: ReviewAction
    corrected_value: str | None
    reason: str
    reviewer: str
    timestamp: datetime
    summary: AuditSummary | None  # recomputed after this review


class ReviewDetail(_Out):
    action: ReviewAction
    corrected_value: str | None
    reason: str
    reviewer: str
    timestamp: datetime


# --- run list and detail ---


class SpanOut(_Out):
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class RunSummaryOut(_Out):
    id: str
    question_idx: int
    question: str
    provider: str
    model: str
    repeat_idx: int
    search_mode: RunSearchMode
    status: Literal["pending", "ok", "failed"]
    error: str | None
    brand_mentioned: bool | None  # the effective outcome; null when there is no usable answer
    verdict_status: Literal["verified", "flagged"] | None
    reviewed: bool
    degraded: bool
    latency_ms: int | None
    cost_usd: float


class EvidenceBrief(_Out):
    source_id: str
    domain: str
    fetch_status: FetchStatus
    fetch_reason: str
    brand_on_page: bool | None  # null whenever the page could not be read
    source_type: SourceType


class CitationOut(_Out):
    url: str
    domain: str
    title: str | None
    source: CitationSource
    unverified: bool  # true when the URL appears only in the answer prose
    evidence: EvidenceBrief | None  # null: the page was never researched


class FindingOut(_Out):
    code: str
    status: Literal["verified", "flagged", "not_checked"]
    reason: str


class RunDetailOut(_Out):
    id: str
    audit_id: str
    question: str
    provider: str
    model: str
    repeat_idx: int
    search_mode: RunSearchMode
    status: Literal["pending", "ok", "failed"]
    error: str | None
    raw_answer: str | None
    mention: Mention | None
    verdict: Verdict | None
    findings: list[FindingOut]
    highlights: list[SpanOut]
    citations: list[CitationOut]
    review: ReviewDetail | None
    brand_mentioned: bool | None
    degraded: bool
    latency_ms: int | None
    cost_usd: float


# --- review queue ---


class QueueItem(_Out):
    run_id: str
    audit_id: str
    brand: str
    question: str
    provider: str
    model: str
    search_mode: RunSearchMode
    repeat_idx: int
    verdict_reason: str
    quote: str | None
    brand_mentioned: bool
    position: int | None
    raw_answer: str | None
    highlights: list[SpanOut]
    created_at: datetime


class QueuePage(_Out):
    items: list[QueueItem]
    next_cursor: str | None
