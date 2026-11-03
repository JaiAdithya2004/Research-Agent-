from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas import Sentiment


class MentionExtraction(BaseModel):
    """The JSON the model must return. Citations are deliberately not part of it."""

    model_config = ConfigDict(extra="forbid")

    brand_mentioned: bool
    position: int | None = Field(default=None, ge=1)
    quote: str | None = None
    competitors_seen: list[str] = Field(default_factory=list)
    sentiment: Sentiment

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.brand_mentioned and (self.position is not None or self.quote is not None):
            raise ValueError("position and quote must be null when brand_mentioned is false")
        return self
