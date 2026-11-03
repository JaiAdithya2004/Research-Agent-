"""Cost estimation and the per-audit cost cap. Search calls are billed on top of tokens."""

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_COST_CAP_USD = 0.75


class CostCapExceeded(Exception):
    def __init__(self, *, cap_usd: float, spent_usd: float) -> None:
        super().__init__(f"cost cap ${cap_usd:.4f} reached (spent ${spent_usd:.4f})")
        self.cap_usd = cap_usd
        self.spent_usd = spent_usd


class UnknownModelError(KeyError):
    """No price is configured for this model. We never guess prices."""


class ModelPricing(BaseModel):
    model_config = ConfigDict(frozen=True)

    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)
    search_per_call: float = Field(default=0, ge=0)


class Pricing:
    def __init__(self, table: dict[str, ModelPricing]) -> None:
        self._table = dict(table)

    def get(self, model: str) -> ModelPricing:
        try:
            return self._table[model]
        except KeyError:
            raise UnknownModelError(f"no pricing configured for model {model!r}") from None

    def cost(
        self, model: str, *, input_tokens: int, output_tokens: int, search_calls: int
    ) -> float:
        p = self.get(model)
        return (
            input_tokens * p.input_per_mtok / 1_000_000
            + output_tokens * p.output_per_mtok / 1_000_000
            + search_calls * p.search_per_call
        )


# Prices read from the providers' pricing pages on 2026-11-03. They change; override via config.
# Gemini 3.8 Flash input/output are the promotional rates valid through 2026-12-31
# (1.50 / 7.50 per M tokens from 2027-01-01). Search: 14 USD per 1,000 queries (Gemini),
# 10 USD per 1,000 calls (OpenAI). The free monthly Gemini search tier is ignored, so
# estimates are conservative.
DEFAULT_PRICING = Pricing(
    {
        "gemini-3.8-flash": ModelPricing(
            input_per_mtok=0.75, output_per_mtok=3.75, search_per_call=0.014
        ),
        "gpt-6.1-terra": ModelPricing(
            input_per_mtok=1.00, output_per_mtok=5.00, search_per_call=0.010
        ),
    }
)


class CostTracker:
    """Running spend for one audit.

    `record` never raises, so a paid-for result is never thrown away. `ensure_headroom` is
    checked before every call and raises once spend has reached the cap, so an audit overshoots
    by at most the calls already in flight.
    """

    def __init__(self, cap_usd: float = DEFAULT_COST_CAP_USD) -> None:
        self.cap_usd = cap_usd
        self.spent_usd = 0.0
        self.search_calls = 0

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.cap_usd - self.spent_usd)

    @property
    def exhausted(self) -> bool:
        return self.spent_usd >= self.cap_usd

    def ensure_headroom(self) -> None:
        if self.exhausted:
            raise CostCapExceeded(cap_usd=self.cap_usd, spent_usd=self.spent_usd)

    def record(self, *, cost_usd: float, search_calls: int) -> None:
        self.spent_usd += cost_usd
        self.search_calls += search_calls
