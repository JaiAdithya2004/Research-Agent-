"""What a human review can say, and how it is read."""

from pydantic import BaseModel, ConfigDict


class Correction(BaseModel):
    model_config = ConfigDict(frozen=True)

    mentioned: bool | None = None
    position: int | None = None


def parse_correction(value: str) -> Correction:
    """Accepted forms: "mentioned", "not_mentioned", "position=<n>" (n >= 1). Else ValueError."""
    text = value.strip().lower()
    if text == "mentioned":
        return Correction(mentioned=True)
    if text == "not_mentioned":
        return Correction(mentioned=False)
    if text.startswith("position="):
        number = text.removeprefix("position=").strip()
        if number.isdigit() and int(number) >= 1:
            return Correction(position=int(number))
    raise ValueError(
        'corrected_value must be "mentioned", "not_mentioned" or "position=<n>" with n >= 1'
    )
