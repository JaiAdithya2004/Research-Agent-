"""Dataset and result models for the eval suite. See evals/README.md for the file formats."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas import FetchStatus, Sentiment, SourceType


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- datasets ----------------------------------------


class ExpectedMention(_Strict):
    brand_mentioned: bool
    position: int | None = Field(default=None, ge=1)  # checked only when given
    sentiment: Sentiment | None = None  # checked only when given


class LabeledAnswer(_Strict):
    id: str
    tag: str  # e.g. misspelling, competitor_only, negative, none
    brand: str
    aliases: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    answer: str
    expected: ExpectedMention


class ExpectedPage(_Strict):
    fetch_status: FetchStatus
    brand_on_page: bool | None  # null whenever the page cannot be read
    source_type: SourceType | None = None  # checked only when given (uses the model)


class LabeledPage(_Strict):
    id: str
    tag: str  # present, absent, paywalled, nav_footer, ...
    brand: str
    aliases: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    http_status: int = 200
    content_type: str = "text/html; charset=utf-8"
    html: str = ""
    expected: ExpectedPage

    @field_validator("http_status")
    @classmethod
    def _plausible_status(cls, v: int) -> int:
        if not 100 <= v <= 599:
            raise ValueError("http_status must be a real HTTP status code")
        return v


class InjectionPage(_Strict):
    id: str
    technique: str  # e.g. hidden_instructions, fake_system, html_comment, alt_text
    brand: str
    aliases: list[str] = Field(default_factory=list)
    competitors: list[str] = Field(default_factory=list)
    clean_html: str
    injected_html: str


# --- results ----------------------------------------


class MetricStats(_Strict):
    values: list[float]
    mean: float
    stdev: float
    min: float
    max: float


class RepeatResult(_Strict):
    repeat: int
    metrics: dict[str, float]
    llm_calls: int
    failures: list[str] = Field(default_factory=list)  # ids of items that were wrong, for debugging


class EvalResult(_Strict):
    prompt_version: str
    classifier_prompt_version: str
    repeats: int
    extractor_model: str
    started_at: datetime
    counts: dict[str, int]
    metrics: dict[str, MetricStats]
    per_repeat: list[RepeatResult]
    notes: list[str] = Field(default_factory=list)


MetricDirection = Literal["higher", "lower"]

# name -> (label, direction, kind). kind drives formatting.
METRICS: dict[str, tuple[str, MetricDirection, Literal["rate", "usd", "ms"]]] = {
    "extraction_accuracy": ("Extraction accuracy", "higher", "rate"),
    "false_mention_rate": ("False-mention rate", "lower", "rate"),
    "flag_rate": ("Flag rate", "lower", "rate"),
    "source_check_accuracy": ("Source-check accuracy", "higher", "rate"),
    "injection_pass_rate": ("Injection pass rate", "higher", "rate"),
    "total_cost_usd": ("Total cost", "lower", "usd"),
    "latency_p50_ms": ("Latency P50", "lower", "ms"),
    "latency_p90_ms": ("Latency P90", "lower", "ms"),
}
