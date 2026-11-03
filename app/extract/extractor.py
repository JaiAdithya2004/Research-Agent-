"""Extractor: raw answer in, validated Mention out.

The model returns JSON that is validated into MentionExtraction. Invalid output gets exactly one
repair attempt that shows the model its own output and the validation error; after that the
item is marked failed. Citations are attached from provider grounding metadata and are never
sent to, or taken from, the model.
"""

import json
import re
from pathlib import Path
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.extract.prompts import (
    PROMPTS_DIR,
    load_prompt,
    render_extract_prompt,
    render_repair_prompt,
)
from app.extract.schemas import MentionExtraction
from app.llm.types import LLMCallFailed, LLMResult
from app.schemas import Citation, Mention

MAX_ERROR_CHARS = 2000


class ExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok", "failed"]
    mention: Mention | None = None
    error: str | None = None
    prompt_version: str
    repair_attempted: bool = False
    raw_outputs: list[str] = Field(default_factory=list)  # every model reply, kept verbatim
    llm_calls: int = 0
    cost_usd: float = 0.0


class Generator(Protocol):
    async def generate(
        self, *, provider: str, model: str, prompt: str, search: bool = False
    ) -> LLMResult: ...


_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL | re.IGNORECASE)


def _unfence(text: str) -> str:
    stripped = text.strip()
    m = _FENCE.match(stripped)
    return m.group(1) if m else stripped


def _parse(text: str) -> MentionExtraction:
    """Raise ValueError (JSON problems) or ValidationError (schema problems)."""
    return MentionExtraction.model_validate(json.loads(_unfence(text)))


def _describe(exc: ValueError) -> str:
    if isinstance(exc, ValidationError):
        lines = [
            f"{'.'.join(str(p) for p in e['loc']) or '(root)'}: {e['msg']}" for e in exc.errors()
        ]
        message = "Schema validation failed:\n" + "\n".join(lines)
    else:
        message = f"Invalid JSON: {exc}"
    return message[:MAX_ERROR_CHARS]


def _canonical_competitors(seen: list[str], allowed: list[str]) -> list[str]:
    by_key = {name.casefold(): name for name in allowed}
    out: list[str] = []
    for name in seen:
        canon = by_key.get(name.strip().casefold())
        if canon is not None and canon not in out:
            out.append(canon)
    return out


class Extractor:
    def __init__(
        self,
        llm: Generator,
        *,
        provider: str,
        model: str,
        prompt_version: str = "v1",
        prompts_dir: Path = PROMPTS_DIR,
    ) -> None:
        self._llm = llm
        self._provider = provider
        self._model = model
        self.prompt_version = prompt_version
        self._extract_template = load_prompt("extract", prompt_version, prompts_dir)
        self._repair_template = load_prompt("extract_repair", prompt_version, prompts_dir)

    async def extract(
        self,
        *,
        answer: str,
        citations: list[Citation],
        brand: str,
        aliases: list[str],
        competitors: list[str],
    ) -> ExtractionResult:
        """Never raises for bad model output or a failed call; CostCapExceeded still propagates."""
        schema = MentionExtraction.model_json_schema()
        prompt = render_extract_prompt(
            self._extract_template,
            answer=answer,
            brand=brand,
            aliases=aliases,
            competitors=competitors,
            schema=schema,
        )
        outputs: list[str] = []
        cost = 0.0

        def result(status: Literal["ok", "failed"], **kw: object) -> ExtractionResult:
            return ExtractionResult(
                status=status,
                prompt_version=self.prompt_version,
                raw_outputs=outputs,
                llm_calls=len(outputs),
                cost_usd=cost,
                **kw,  # type: ignore[arg-type]
            )

        error = ""
        for attempt in (1, 2):
            try:
                reply = await self._llm.generate(
                    provider=self._provider, model=self._model, prompt=prompt, search=False
                )
            except LLMCallFailed as exc:
                return result(
                    "failed", error=f"LLM call failed: {exc}", repair_attempted=attempt == 2
                )
            outputs.append(reply.text)
            cost += reply.cost_usd
            try:
                parsed = _parse(reply.text)
            except ValueError as exc:
                error = _describe(exc)
                if attempt == 2:
                    break
                # The repair prompt repeats the original task, then shows the model its own
                # output and the validation error.
                prompt = (
                    prompt
                    + "\n\n"
                    + render_repair_prompt(
                        self._repair_template,
                        previous_output=reply.text,
                        error=error,
                        schema=schema,
                    )
                )
                continue
            mention = Mention(
                brand_mentioned=parsed.brand_mentioned,
                position=parsed.position,
                quote=parsed.quote,
                competitors_seen=_canonical_competitors(parsed.competitors_seen, competitors),
                citations=citations,
                sentiment=parsed.sentiment,
            )
            return result("ok", mention=mention, repair_attempted=attempt == 2)

        return result("failed", error=error, repair_attempted=True)
