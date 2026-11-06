"""Plain data records for one audit run. The database and the in-memory repo both store these."""

from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.schemas import AuditStatus, Citation, Mention, RunSearchMode, Verdict
from app.verify.verifier import VerificationReport

SearchModeOne = RunSearchMode
RunStatus = Literal["pending", "ok", "failed"]


class StepKey(BaseModel):
    """One unit of work. A step is idempotent per (question, model, repeat, search mode)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question_idx: int = Field(ge=0)
    provider: str
    model: str
    repeat_idx: int = Field(ge=0)
    search_mode: SearchModeOne


class ReviewState(BaseModel):
    """The latest human review of a run, as far as the metrics are concerned."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "correct", "reject"]
    corrected_mentioned: bool | None = None
    corrected_position: int | None = None


class RunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: str(uuid4()))
    key: StepKey
    question: str
    status: RunStatus = "pending"
    error: str | None = None
    raw_answer: str | None = None  # write-once
    raw_grounding: dict[str, Any] | None = None  # write-once
    citations: list[Citation] = Field(default_factory=list)
    mention: Mention | None = None
    verdict: Verdict | None = None
    verification: VerificationReport | None = None
    extractor_version: str | None = None
    latency_ms: int | None = None
    cost_usd: float = 0.0
    degraded: bool = False
    search_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    review: ReviewState | None = None


class AuditState(BaseModel):
    """Audit-level counters that survive a restart, so a retried job resumes where it stopped."""

    model_config = ConfigDict(extra="forbid")

    status: AuditStatus = "running"
    degraded: bool = False
    caps_hit: list[str] = Field(default_factory=list)
    total_cost_usd: float = 0.0
    search_calls: int = 0
    pages_fetched: int = 0
