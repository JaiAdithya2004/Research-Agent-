"""Versioned prompt files: prompts/<name>_<version>.txt at the repository root."""

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from app.extract.schemas import MentionExtraction

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

_TOKEN = re.compile(r"<<([A-Z_]+)>>")


def load_prompt(name: str, version: str, prompts_dir: Path = PROMPTS_DIR) -> str:
    path = prompts_dir / f"{name}_{version}.txt"
    if not path.is_file():
        raise FileNotFoundError(f"prompt file not found: {path}")
    return path.read_text(encoding="utf8")


def fill(template: str, values: dict[str, str]) -> str:
    """Replace <<TOKEN>> placeholders in a single pass.

    Inserted values are never rescanned, so untrusted text that happens to contain a token
    such as <<BRAND>> cannot inject anything.
    """
    return _TOKEN.sub(
        lambda m: values[m.group(1)] if m.group(1) in values else m.group(0), template
    )


def _nonce(text: str) -> str:
    return hashlib.sha256(text.encode("utf8")).hexdigest()[:8]


def schema_text(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2)


def render_extract_prompt(
    template: str,
    *,
    answer: str,
    brand: str,
    aliases: list[str],
    competitors: list[str],
    schema: dict[str, Any] | None = None,
) -> str:
    # Markers carry a hash of the answer, so the answer cannot contain its own closing marker.
    nonce = _nonce(answer)
    if schema is None:
        schema = MentionExtraction.model_json_schema()
    return fill(
        template,
        {
            "BRAND": brand,
            "ALIASES": ", ".join(aliases) or "(none)",
            "COMPETITORS": ", ".join(competitors) or "(none)",
            "ANSWER_OPEN": f"<<<ANSWER-{nonce}>>>",
            "ANSWER": answer,
            "ANSWER_CLOSE": f"<<<END-ANSWER-{nonce}>>>",
            "SCHEMA": schema_text(schema),
        },
    )


def render_repair_prompt(
    template: str, *, previous_output: str, error: str, schema: dict[str, Any]
) -> str:
    nonce = _nonce(previous_output)
    return fill(
        template,
        {
            "ERROR": error,
            "PREVIOUS_OPEN": f"<<<PREVIOUS-{nonce}>>>",
            "PREVIOUS_OUTPUT": previous_output,
            "PREVIOUS_CLOSE": f"<<<END-PREVIOUS-{nonce}>>>",
            "SCHEMA": schema_text(schema),
        },
    )
