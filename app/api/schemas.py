"""Response models. The frontend's typed client is generated from these (via OpenAPI)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.evals.models import EvalResult
from app.schemas import (
    AuditRequest,
    AuditStatus,
    AuditSummary,
    FetchStatus,
    SourceType,
)


class AuditOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    status: AuditStatus
    brand: str
    created_at: datetime
    completed_at: datetime | None
    degraded: bool
    caps_hit: list[str]
    request: AuditRequest
    summary: AuditSummary | None  # null until the first run has finished


class SourceOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    url: str
    final_url: str | None
    domain: str
    fetch_status: FetchStatus
    fetch_reason: str  # coarse on purpose: see api.public_reason
    brand_on_page: bool | None  # null whenever the page could not be read
    quote: str | None
    source_type: SourceType
    classification: str
    snapshot_id: str | None
    snapshot_fetched_at: datetime | None


class SourceDetailOut(SourceOut):
    snapshot_text: str | None  # page text, only when the page was read (status ok)
    content_hash: str | None


class GapEntryOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    domain: str
    frequency: int = Field(ge=1)
    competitors: list[str]
    source_ids: list[str]  # evidence rows for this domain, to link to the proof


class GapReportOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    entries: list[GapEntryOut]


class ErrorOut(BaseModel):
    detail: str


class EvalRunOut(EvalResult):
    """One stored eval run (all repeats) for a prompt version."""

    id: str
