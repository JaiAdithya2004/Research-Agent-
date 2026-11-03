"""Run the eval suites against a prompt version and aggregate across repeats.

Everything goes through the real pipeline (extractor, verifier, safe fetcher, page check,
source classifier). Only the model client and the network are injected, so tests can fake them.
"""

import math
import statistics
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from app.audit.metrics import percentile
from app.evals.models import (
    EvalResult,
    InjectionPage,
    LabeledAnswer,
    LabeledPage,
    MetricStats,
    RepeatResult,
)
from app.evals.sitefake import StaticSite
from app.extract.extractor import Extractor, Generator
from app.extract.prompts import PROMPTS_DIR
from app.llm.cost import CostTracker
from app.llm.types import LLMResult
from app.research.classifier import SourceClassifier
from app.research.researcher import ResearchCaps, ResearchedPage, research_citations
from app.schemas import Citation
from app.verify.verifier import verify_mention

EVAL_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_UNLIMITED = ResearchCaps(max_fetches=10_000, max_per_domain=10_000)


class MeteredLLM:
    """Wraps a model client and records every call's cost and latency."""

    def __init__(self, inner: Generator) -> None:
        self._inner = inner
        self.costs: list[float] = []
        self.latencies: list[int] = []

    async def generate(
        self, *, provider: str, model: str, prompt: str, search: bool = False
    ) -> LLMResult:
        result = await self._inner.generate(
            provider=provider, model=model, prompt=prompt, search=search
        )
        self.costs.append(result.cost_usd)
        self.latencies.append(result.latency_ms)
        return result

    @property
    def calls(self) -> int:
        return len(self.costs)


def _stats(values: list[float]) -> MetricStats:
    return MetricStats(
        values=values,
        mean=statistics.fmean(values),
        stdev=statistics.pstdev(values) if len(values) > 1 else 0.0,
        min=min(values),
        max=max(values),
    )


def classifier_version(prompt_version: str, prompts_dir: Path) -> str:
    """Use the matching classifier prompt when one exists, otherwise v1."""
    exists = (prompts_dir / f"classify_source_{prompt_version}.txt").is_file()
    return prompt_version if exists else "v1"


# ---------------------------------------------------------------------------------------- suites


async def eval_answers(
    items: list[LabeledAnswer], extractor: Extractor
) -> tuple[dict[str, float], list[str]]:
    correct = flagged = failed = usable = 0
    negatives = false_mentions = 0
    wrong: list[str] = []
    for item in items:
        result = await extractor.extract(
            answer=item.answer,
            citations=[],
            brand=item.brand,
            aliases=item.aliases,
            competitors=item.competitors,
        )
        if not item.expected.brand_mentioned:
            negatives += 1
        if result.status != "ok" or result.mention is None:
            failed += 1
            wrong.append(f"{item.id} (extraction failed)")
            continue
        usable += 1
        mention = result.mention
        if not item.expected.brand_mentioned and mention.brand_mentioned:
            false_mentions += 1
        ok = mention.brand_mentioned == item.expected.brand_mentioned
        if ok and item.expected.position is not None:
            ok = mention.position == item.expected.position
        if ok and item.expected.sentiment is not None:
            ok = mention.sentiment == item.expected.sentiment
        if ok:
            correct += 1
        else:
            wrong.append(item.id)
        report = verify_mention(
            mention,
            raw_answer=item.answer,
            brand=item.brand,
            aliases=item.aliases,
            competitors=item.competitors,
            now=EVAL_NOW,
        )
        flagged += report.verdict.status == "flagged"
    total = len(items)
    return (
        {
            "extraction_accuracy": correct / total if total else 0.0,
            "false_mention_rate": false_mentions / negatives if negatives else 0.0,
            "flag_rate": flagged / usable if usable else 0.0,
        },
        wrong,
    )


async def _research_one(
    site: StaticSite,
    path: str,
    *,
    http_status: int,
    content_type: str,
    html: str,
    brand: str,
    aliases: list[str],
    competitors: list[str],
    classifier: SourceClassifier,
) -> ResearchedPage | None:
    url = site.add(path, status=http_status, content_type=content_type, body=html)
    citation = Citation(
        url=url,
        domain=url.split("//")[1].split("/")[0],
        title=None,
        source="grounding_metadata",
        retrieved_at=EVAL_NOW,
    )
    result = await research_citations(
        [citation],
        brand=brand,
        aliases=aliases,
        competitors=competitors,
        fetcher=site.fetcher(),
        classifier=classifier,
        tracker=CostTracker(cap_usd=1e9),
        caps=_UNLIMITED,
        now=lambda: EVAL_NOW,
    )
    return result.pages[0] if result.pages else None


async def eval_pages(
    items: list[LabeledPage], classifier: SourceClassifier
) -> tuple[dict[str, float], list[str]]:
    correct = 0
    wrong: list[str] = []
    for item in items:
        site = StaticSite()
        page = await _research_one(
            site,
            f"/{item.id}",
            http_status=item.http_status,
            content_type=item.content_type,
            html=item.html,
            brand=item.brand,
            aliases=item.aliases,
            competitors=item.competitors,
            classifier=classifier,
        )
        ok = (
            page is not None
            and page.evidence.fetch_status == item.expected.fetch_status
            and page.evidence.brand_on_page == item.expected.brand_on_page
            and (
                item.expected.source_type is None
                or page.evidence.source_type == item.expected.source_type
            )
        )
        if ok:
            correct += 1
        else:
            wrong.append(item.id)
    return {"source_check_accuracy": correct / len(items) if items else 0.0}, wrong


def _fingerprint(page: ResearchedPage | None) -> tuple[object, ...]:
    if page is None:
        return ("no-result",)
    e = page.evidence
    return (e.fetch_status, e.brand_on_page, e.quote, e.source_type)


async def _research_html(
    site: StaticSite, item: InjectionPage, suffix: str, html: str, classifier: SourceClassifier
) -> ResearchedPage | None:
    return await _research_one(
        site,
        f"/{item.id}-{suffix}",
        http_status=200,
        content_type="text/html; charset=utf-8",
        html=html,
        brand=item.brand,
        aliases=item.aliases,
        competitors=item.competitors,
        classifier=classifier,
    )


async def eval_injection(
    items: list[InjectionPage], classifier: SourceClassifier
) -> tuple[dict[str, float], list[str]]:
    passed = 0
    wrong: list[str] = []
    for item in items:
        site = StaticSite()
        clean = await _research_html(site, item, "clean", item.clean_html, classifier)
        injected = await _research_html(site, item, "injected", item.injected_html, classifier)
        if _fingerprint(clean) == _fingerprint(injected):
            passed += 1
        else:
            wrong.append(f"{item.id} ({item.technique})")
    return {"injection_pass_rate": passed / len(items) if items else 0.0}, wrong


# ---------------------------------------------------------------------------------------- driver


async def run_eval(
    *,
    llm_factory: Callable[[], Generator],
    provider: str,
    model: str,
    prompt_version: str,
    repeats: int,
    answers: list[LabeledAnswer] | None = None,
    pages: list[LabeledPage] | None = None,
    injection: list[InjectionPage] | None = None,
    prompts_dir: Path = PROMPTS_DIR,
) -> EvalResult:
    """Run every provided suite `repeats` times. `llm_factory` is called once per repeat."""
    cls_version = classifier_version(prompt_version, prompts_dir)
    started = datetime.now(UTC)
    per_repeat: list[RepeatResult] = []
    for repeat in range(repeats):
        metered = MeteredLLM(llm_factory())
        extractor = Extractor(
            metered,
            provider=provider,
            model=model,
            prompt_version=prompt_version,
            prompts_dir=prompts_dir,
        )
        classifier = SourceClassifier(
            metered,
            provider=provider,
            model=model,
            prompt_version=cls_version,
            prompts_dir=prompts_dir,
        )
        metrics: dict[str, float] = {}
        failures: list[str] = []
        for suite_metrics, wrong in (
            ([await eval_answers(answers, extractor)] if answers else [])
            + ([await eval_pages(pages, classifier)] if pages else [])
            + ([await eval_injection(injection, classifier)] if injection else [])
        ):
            metrics.update(suite_metrics)
            failures.extend(wrong)
        metrics["total_cost_usd"] = math.fsum(metered.costs)
        metrics["latency_p50_ms"] = float(percentile(metered.latencies, 50))
        metrics["latency_p90_ms"] = float(percentile(metered.latencies, 90))
        per_repeat.append(
            RepeatResult(repeat=repeat, metrics=metrics, llm_calls=metered.calls, failures=failures)
        )

    names = list(per_repeat[0].metrics) if per_repeat else []
    return EvalResult(
        prompt_version=prompt_version,
        classifier_prompt_version=cls_version,
        repeats=repeats,
        extractor_model=f"{provider}:{model}",
        started_at=started,
        counts={
            "answers": len(answers or []),
            "pages": len(pages or []),
            "injection": len(injection or []),
        },
        metrics={n: _stats([r.metrics[n] for r in per_repeat]) for n in names},
        per_repeat=per_repeat,
    )
