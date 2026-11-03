"""The orchestrator.

For each repeat, question, model and search mode ("a step"): ask the model the way a user would,
extract the mention, verify it, then run the source-evidence step on its citations, and store
everything. Guarantees:

  * Idempotent per step. A finished step is skipped; a step whose model answer was stored but not
    yet processed resumes from that stored answer, so a retried job never pays twice.
  * One failure never kills the audit: a provider that is down, an extraction that stays invalid
    or an unexpected bug marks that run failed and the audit continues.
  * Bounded: a max-steps guard, the cost cap, and the research caps. Hitting one stops the work,
    keeps everything finished so far, and sets the status to "capped". Never an error.
  * Raw answers and grounding metadata are stored as received and never overwritten.
"""

import logging
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.audit.events import EventSink, EventType
from app.audit.metrics import compute_summary, final_status
from app.audit.records import AuditState, RunRecord, SearchModeOne, StepKey
from app.audit.repo import AuditRepo
from app.extract.extractor import Extractor, Generator
from app.llm.cost import CostCapExceeded, CostTracker
from app.llm.types import LLMCallFailed, LLMResult
from app.logging_config import bind_log_context, reset_log_context
from app.observability import get_tracer
from app.research.classifier import SourceClassifier
from app.research.researcher import (
    Fetcher,
    ResearchBudget,
    ResearchCaps,
    ResearchedPage,
    research_citations,
)
from app.schemas import AuditRequest, AuditStatus, AuditSummary, Citation
from app.verify.verifier import url_key, verify_mention

logger = logging.getLogger("app.audit")

DEFAULT_MAX_STEPS = 300
# Caps that describe work skipped because of a limit that a bigger budget could lift. They are
# re-evaluated when a capped audit is resumed; research caps stay (those URLs were already skipped).
_REEVALUATED_CAPS = {"cost_cap", "max_steps"}


@dataclass(frozen=True)
class ModelTarget:
    provider: str
    model: str


@dataclass
class AuditConfig:
    targets: list[ModelTarget]
    extractor: ModelTarget
    max_steps: int = DEFAULT_MAX_STEPS
    circuit_threshold: int = 3  # consecutive failures before a provider is skipped
    research_caps: ResearchCaps = field(default_factory=ResearchCaps)
    extractor_prompt_version: str = "v1"


@dataclass
class AuditDeps:
    llm: Generator
    tracker: CostTracker
    fetcher: Fetcher
    classifier: SourceClassifier
    repo: AuditRepo
    events: EventSink
    config: AuditConfig
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


@dataclass
class AuditOutcome:
    status: AuditStatus
    summary: AuditSummary
    state: AuditState


def plan_steps(request: AuditRequest, targets: list[ModelTarget]) -> list[StepKey]:
    by_request: dict[str, list[SearchModeOne]] = {
        "off": ["off"],
        "grounded": ["grounded"],
        "both": ["off", "grounded"],
    }
    modes = by_request[request.search_mode]
    # Repeat-major, so an audit that is capped part-way still covers every question.
    return [
        StepKey(question_idx=q, provider=t.provider, model=t.model, repeat_idx=r, search_mode=mode)
        for r in range(request.repeats)
        for q in range(len(request.questions))
        for t in targets
        for mode in modes
    ]


def _grounded(citations: list[Citation]) -> list[Citation]:
    return [c for c in citations if c.source == "grounding_metadata"]


async def run_audit(audit_id: str, request: AuditRequest, deps: AuditDeps) -> AuditOutcome:
    """Run (or resume) an audit as one Langfuse trace, with structured logs tagged by audit id."""
    token = bind_log_context(audit_id=audit_id)
    try:
        with get_tracer().span(
            "audit",
            kind="trace",
            audit_id=audit_id,
            brand=request.brand,
            search_mode=request.search_mode,
            questions=len(request.questions),
            repeats=request.repeats,
        ) as span:
            outcome = await _run_audit(audit_id, request, deps)
            span.update(
                status=outcome.status,
                total_cost_usd=outcome.summary.total_cost_usd,
                total_runs=outcome.summary.total_runs,
                failed_runs=outcome.summary.failed_runs,
                caps_hit=",".join(outcome.summary.caps_hit),
            )
            return outcome
    finally:
        reset_log_context(token)
        get_tracer().flush()


async def _run_audit(audit_id: str, request: AuditRequest, deps: AuditDeps) -> AuditOutcome:
    cfg, repo, tracker = deps.config, deps.repo, deps.tracker

    state = await repo.load_state(audit_id)
    tracker.spent_usd = max(tracker.spent_usd, state.total_cost_usd)
    tracker.search_calls = max(tracker.search_calls, state.search_calls)

    researched: dict[str, ResearchedPage] = {
        url_key(p.evidence.url): p for p in await repo.load_evidence(audit_id)
    }
    budget = ResearchBudget(fetches_used=state.pages_fetched)
    for page in researched.values():
        if not page.from_cache:
            budget.per_domain[page.cited_domain] += 1

    caps_hit = [c for c in state.caps_hit if c not in _REEVALUATED_CAPS]
    degraded = state.degraded
    extractor = Extractor(
        deps.llm,
        provider=cfg.extractor.provider,
        model=cfg.extractor.model,
        prompt_version=cfg.extractor_prompt_version,
    )

    def add_cap(name: str) -> None:
        if name not in caps_hit:
            caps_hit.append(name)

    async def emit(type: EventType, data: dict[str, Any]) -> None:
        await deps.events.emit(audit_id, type, data)

    async def checkpoint(status: AuditStatus = "running") -> AuditState:
        saved = AuditState(
            status=status,
            degraded=degraded,
            caps_hit=list(caps_hit),
            total_cost_usd=tracker.spent_usd,
            search_calls=tracker.search_calls,
            pages_fetched=budget.fetches_used,
        )
        await repo.save_state(audit_id, saved)
        return saved

    steps = plan_steps(request, cfg.targets)
    if len(steps) > cfg.max_steps:
        steps = steps[: cfg.max_steps]
        add_cap("max_steps")

    already_started = bool(await repo.list_runs(audit_id))
    await emit(
        "audit_resumed" if already_started else "audit_started", {"planned_steps": len(steps)}
    )
    streak: Counter[str] = Counter()
    down: set[str] = set()

    for key in steps:
        existing = await repo.get_run(audit_id, key)
        if existing is not None and existing.status in ("ok", "failed"):
            continue  # finished in an earlier attempt: no new model, search or fetch call
        if tracker.exhausted:
            add_cap("cost_cap")
            break

        if key.provider in down:
            await repo.save_run(
                audit_id,
                RunRecord(
                    key=key,
                    question=request.questions[key.question_idx],
                    status="failed",
                    error=f"skipped: provider {key.provider} is down "
                    f"({cfg.circuit_threshold} consecutive failures)",
                ),
            )
            continue

        step_token = bind_log_context(
            question_id=key.question_idx, model=key.model, provider=key.provider
        )
        try:
            with get_tracer().span(
                "run",
                question_idx=key.question_idx,
                provider=key.provider,
                model=key.model,
                search_mode=key.search_mode,
                repeat=key.repeat_idx,
            ):
                record = await _run_step(
                    audit_id, request, key, existing, deps, extractor, emit_event=emit
                )
        except CostCapExceeded:
            add_cap("cost_cap")
            await checkpoint()
            break
        except Exception as exc:  # noqa: BLE001 - one bad step must not kill the audit
            logger.warning("step_internal_error", extra={"error": type(exc).__name__})
            record = RunRecord(
                key=key,
                question=request.questions[key.question_idx],
                status="failed",
                error=f"internal error: {type(exc).__name__}",
            )
            prior = await repo.get_run(audit_id, key)
            if prior is not None:  # keep the stored raw answer if the answer had been saved
                record = prior.model_copy(update={"status": "failed", "error": record.error})
            await repo.save_run(audit_id, record)
            await emit("run_failed", {"step": key.model_dump(), "reason": "internal_error"})
        finally:
            reset_log_context(step_token)

        if record.error and record.error.startswith("model call failed"):
            streak[key.provider] += 1
            if streak[key.provider] >= cfg.circuit_threshold:
                down.add(key.provider)
        elif record.status != "pending":
            streak[key.provider] = 0
        degraded = degraded or record.degraded

        if record.status == "ok" and request.research_depth == "cited_pages":
            new = [c for c in _grounded(record.citations) if url_key(c.url) not in researched]
            if new:
                result = await research_citations(
                    new,
                    brand=request.brand,
                    aliases=request.aliases,
                    competitors=request.competitors,
                    fetcher=deps.fetcher,
                    classifier=deps.classifier,
                    tracker=tracker,
                    caps=cfg.research_caps,
                    budget=budget,
                    now=deps.now,
                )
                for cap in result.caps_hit:
                    add_cap(cap)
                if result.pages:
                    await repo.save_evidence(audit_id, result.pages)
                for page in result.pages:
                    researched[url_key(page.evidence.url)] = page
                    await emit(
                        "source_fetched",
                        {
                            "url": page.evidence.url,
                            "fetch_status": page.evidence.fetch_status,
                            "brand_on_page": page.evidence.brand_on_page,
                            "source_type": page.evidence.source_type,
                        },
                    )
        await checkpoint()

    runs = await repo.list_runs(audit_id)
    status = final_status(runs, caps_hit)
    final = await checkpoint(status)
    summary = compute_summary(runs, search_mode=request.search_mode, state=final)
    terminal_by_status: dict[AuditStatus, EventType] = {
        "complete": "audit_completed",
        "capped": "audit_capped",
        "failed": "audit_failed",
        "running": "audit_failed",
    }
    terminal = terminal_by_status[status]
    await emit(
        terminal,
        {
            "status": status,
            "total_runs": summary.total_runs,
            "failed_runs": summary.failed_runs,
            "caps_hit": list(caps_hit),
        },
    )
    return AuditOutcome(status=status, summary=summary, state=final)


async def _run_step(
    audit_id: str,
    request: AuditRequest,
    key: StepKey,
    existing: RunRecord | None,
    deps: AuditDeps,
    extractor: Extractor,
    *,
    emit_event: Callable[[EventType, dict[str, Any]], Any],
) -> RunRecord:
    repo = deps.repo
    question = request.questions[key.question_idx]
    step = key.model_dump()

    if existing is not None and existing.status == "pending" and existing.raw_answer is not None:
        record = existing  # resume: the model already answered; do not ask (or pay) again
    else:
        await emit_event("run_started", {"step": step})
        try:
            result: LLMResult = await deps.llm.generate(
                provider=key.provider,
                model=key.model,
                prompt=question,
                search=key.search_mode == "grounded",
            )
        except LLMCallFailed as exc:
            failed = RunRecord(
                key=key,
                question=question,
                status="failed",
                error=f"model call failed after {exc.attempts} attempt(s)",
            )
            await repo.save_run(audit_id, failed)
            await emit_event("run_failed", {"step": step, "reason": "model_call_failed"})
            return failed
        record = RunRecord(
            key=key,
            question=question,
            status="pending",
            raw_answer=result.text,
            raw_grounding=result.raw_response,
            citations=_grounded(result.citations),
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
            degraded=result.degraded,
            search_calls=result.search_calls,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
        )
        await repo.save_run(audit_id, record)  # saved before any further spending

    extraction = await extractor.extract(
        answer=record.raw_answer or "",
        citations=record.citations,
        brand=request.brand,
        aliases=request.aliases,
        competitors=request.competitors,
    )
    record = record.model_copy(
        update={
            "cost_usd": record.cost_usd + extraction.cost_usd,
            "extractor_version": extraction.prompt_version,
        }
    )
    if extraction.status != "ok" or extraction.mention is None:
        record = record.model_copy(
            update={
                "status": "failed",
                "error": f"extraction failed: {extraction.error or 'no mention returned'}",
            }
        )
        await repo.save_run(audit_id, record)
        await emit_event("run_failed", {"step": step, "reason": "extraction_failed"})
        return record
    await emit_event("answer_extracted", {"step": step})

    report = verify_mention(
        extraction.mention,
        raw_answer=record.raw_answer or "",
        brand=request.brand,
        aliases=request.aliases,
        competitors=request.competitors,
        now=deps.now(),
    )
    record = record.model_copy(
        update={
            "status": "ok",
            "mention": extraction.mention,
            "verdict": report.verdict,
            "verification": report,
            "citations": [*record.citations, *report.prose_citations],
        }
    )
    await repo.save_run(audit_id, record)
    await emit_event("run_verified", {"step": step, "verdict": report.verdict.status})
    return record
