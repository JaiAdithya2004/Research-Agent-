"""Pydantic models from SPEC.md Inputs and Outputs.

Literal types are exported so the DB layer can reuse the same value sets.
"""

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

SearchMode = Literal["off", "grounded", "both"]
ResearchDepth = Literal["none", "cited_pages"]
CitationSource = Literal["grounding_metadata", "answer_text"]
Sentiment = Literal["pos", "neu", "neg"]
FetchStatus = Literal["ok", "blocked", "unreachable", "paywalled", "too_large", "error"]
SourceType = Literal["own_site", "competitor", "review", "news", "forum", "other"]
VerdictStatus = Literal["verified", "flagged"]
ReviewAction = Literal["accept", "correct", "reject"]
AuditStatus = Literal["running", "complete", "capped", "failed"]

NonBlank = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AuditRequest(_Model):
    brand: NonBlank
    aliases: list[NonBlank] = Field(default_factory=list)
    category: NonBlank
    competitors: list[NonBlank] = Field(default_factory=list)
    questions: list[NonBlank] = Field(min_length=1, max_length=10)
    repeats: int = Field(default=3, ge=1, le=5)
    search_mode: SearchMode = "grounded"
    research_depth: ResearchDepth = "cited_pages"


class Citation(_Model):
    url: NonBlank
    domain: NonBlank
    title: str | None = None
    source: CitationSource
    retrieved_at: datetime

    @property
    def unverified(self) -> bool:
        """A URL that only appears in prose is an unverified citation (SPEC criterion 2)."""
        return self.source == "answer_text"


class Mention(_Model):
    brand_mentioned: bool
    position: int | None = Field(default=None, ge=1)
    quote: str | None = None
    competitors_seen: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)
    sentiment: Sentiment

    @model_validator(mode="after")
    def _position_needs_mention(self) -> Self:
        if not self.brand_mentioned and (self.position is not None or self.quote is not None):
            raise ValueError("position and quote require brand_mentioned=True")
        return self


class SourceEvidence(_Model):
    url: NonBlank
    final_url: str | None = None
    fetch_status: FetchStatus
    brand_on_page: bool | None
    quote: str | None = None
    source_type: SourceType
    snapshot_id: str | None = None

    @model_validator(mode="after")
    def _unfetched_is_never_absent(self) -> Self:
        # SPEC criterion 14: a page we could not fetch is never "brand absent".
        if self.fetch_status != "ok" and self.brand_on_page is not None:
            raise ValueError("brand_on_page must be null unless fetch_status is 'ok'")
        if self.quote is not None and self.brand_on_page is not True:
            raise ValueError("quote requires brand_on_page=True")
        return self


class Verdict(_Model):
    status: VerdictStatus
    reason: NonBlank


class Review(_Model):
    action: ReviewAction
    corrected_value: str | None = None
    reason: NonBlank
    reviewer: NonBlank
    timestamp: datetime

    @model_validator(mode="after")
    def _correct_needs_value(self) -> Self:
        if self.action == "correct" and not self.corrected_value:
            raise ValueError("action 'correct' requires corrected_value")
        return self


class AuditSummary(_Model):
    mention_rate: float = Field(ge=0, le=1)
    avg_position: float | None = Field(default=None, ge=1)
    stability: float = Field(ge=0, le=1)
    flagged_count: int = Field(ge=0)
    total_cost_usd: float = Field(ge=0)
    search_calls: int = Field(ge=0)
    pages_fetched: int = Field(ge=0)
    latency_p50_ms: int = Field(ge=0)
    latency_p90_ms: int = Field(ge=0)
    status: AuditStatus
    degraded: bool = False
    mode_delta: float | None = Field(default=None, ge=-1, le=1)

    @model_validator(mode="after")
    def _p90_not_below_p50(self) -> Self:
        if self.latency_p90_ms < self.latency_p50_ms:
            raise ValueError("latency_p90_ms must be >= latency_p50_ms")
        return self


class SourceGapEntry(_Model):
    domain: NonBlank
    frequency: int = Field(ge=1)
    competitors: list[NonBlank] = Field(default_factory=list)


class SourceGapReport(_Model):
    """Domains cited for competitors but not for the brand, ranked by frequency."""

    entries: list[SourceGapEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _ranked(self) -> Self:
        freqs = [e.frequency for e in self.entries]
        if freqs != sorted(freqs, reverse=True):
            raise ValueError("entries must be ranked by descending frequency")
        return self
