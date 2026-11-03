"""Source-type classification.

Rules decide the obvious cases from the domain alone (the brand's own site, a competitor's site,
well-known review, forum and news sites). A model is used only for the rest, with these limits:

  * the page text goes inside a delimited data block, in a call with NO tools;
  * the model is never told which brand is being audited, so there is nothing to steer toward;
  * its output must validate against an enum-only schema (review, news, forum, other);
  * anything invalid falls back to "other", recorded as a fallback and never echoed back.
"""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from app.extract.extractor import Generator, _unfence
from app.extract.prompts import PROMPTS_DIR, _nonce, fill, load_prompt, schema_text
from app.llm.types import LLMCallFailed
from app.schemas import SourceType
from app.verify.verifier import normalize

ModelSourceType = Literal["review", "news", "forum", "other"]
Method = Literal["rules", "model", "fallback"]

DEFAULT_MAX_CHARS = 6000

_REVIEW_SITES = (
    "g2.com", "capterra.com", "getapp.com", "softwareadvice.com", "trustradius.com",
    "trustpilot.com", "sitejabber.com", "producthunt.com",
)  # fmt: skip
_FORUM_SITES = (
    "reddit.com", "quora.com", "stackoverflow.com", "stackexchange.com", "news.ycombinator.com",
    "discourse.org",
)  # fmt: skip
_NEWS_SITES = (
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "nytimes.com", "wsj.com", "bloomberg.com",
    "techcrunch.com", "theverge.com", "wired.com", "cnbc.com", "theguardian.com",
)  # fmt: skip
_FORUM_LABELS = {"forum", "forums", "community", "discuss"}


class SourceTypeOutput(BaseModel):
    """The only thing the model may return."""

    model_config = ConfigDict(extra="forbid")

    source_type: ModelSourceType


class SourceClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: SourceType
    method: Method
    reason: str


def _host(domain: str) -> str:
    return domain.strip().lower().rstrip(".").removeprefix("www.")


def _compact(text: str) -> str:
    return normalize(text).replace(" ", "")


def _on(host: str, sites: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in sites)


def _names_site(host: str, names: list[str]) -> bool:
    compacts = {c for c in (_compact(n) for n in names) if c}
    return bool(compacts & set(host.split("."))) or _compact(host) in compacts


def rule_source_type(
    domain: str, brand: str, aliases: list[str], competitors: list[str]
) -> SourceType | None:
    host = _host(domain)
    if _names_site(host, [brand, *aliases]):
        return "own_site"
    if _names_site(host, competitors):
        return "competitor"
    if _on(host, _REVIEW_SITES):
        return "review"
    if _on(host, _FORUM_SITES) or host.split(".")[0] in _FORUM_LABELS:
        return "forum"
    if _on(host, _NEWS_SITES):
        return "news"
    return None


def render_classify_prompt(
    template: str, *, domain: str, page_text: str, max_chars: int = DEFAULT_MAX_CHARS
) -> str:
    excerpt = page_text[:max_chars]
    nonce = _nonce(domain + "\n" + excerpt)  # a hash of the data: the data cannot forge its end
    return fill(
        template,
        {
            "DATA_OPEN": f"<<<DATA-{nonce}>>>",
            "DOMAIN": domain,
            "PAGE_TEXT": excerpt,
            "DATA_CLOSE": f"<<<END-DATA-{nonce}>>>",
            "SCHEMA": schema_text(SourceTypeOutput.model_json_schema()),
        },
    )


class SourceClassifier:
    def __init__(
        self,
        llm: Generator | None,
        *,
        provider: str,
        model: str,
        prompt_version: str = "v1",
        prompts_dir: Path = PROMPTS_DIR,
        max_chars: int = DEFAULT_MAX_CHARS,
    ) -> None:
        self._llm = llm
        self._provider = provider
        self._model = model
        self.prompt_version = prompt_version
        self._max_chars = max_chars
        self._template = load_prompt("classify_source", prompt_version, prompts_dir)

    async def classify(
        self,
        *,
        domain: str,
        page_text: str | None,
        brand: str,
        aliases: list[str],
        competitors: list[str],
    ) -> SourceClassification:
        """Never raises for bad model output. CostCapExceeded still propagates."""
        rule = rule_source_type(domain, brand, aliases, competitors)
        if rule is not None:
            return SourceClassification(
                source_type=rule, method="rules", reason=f"decided from the domain {domain}"
            )
        if page_text is None or not page_text.strip():
            return _fallback("no readable page text to classify")
        if self._llm is None:
            return _fallback("no model configured")

        prompt = render_classify_prompt(
            self._template, domain=domain, page_text=page_text, max_chars=self._max_chars
        )
        try:
            reply = await self._llm.generate(
                provider=self._provider, model=self._model, prompt=prompt, search=False
            )
        except LLMCallFailed:
            return _fallback("the model call failed")
        try:
            parsed = SourceTypeOutput.model_validate(json.loads(_unfence(reply.text)))
        except ValueError:
            # The model's own text is deliberately not included here.
            return _fallback("the model output did not match the schema")
        return SourceClassification(
            source_type=parsed.source_type, method="model", reason="classified by the model"
        )


def _fallback(reason: str) -> SourceClassification:
    return SourceClassification(source_type="other", method="fallback", reason=reason)
