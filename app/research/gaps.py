"""Source gap report, computed in code from stored mentions. No model writes it (SPEC 17).

A domain is a gap when it was cited in answers that mention a competitor but was never cited in
any answer that mentions the brand. Frequency is the number of answers that cited the domain.
Only citations from provider grounding metadata count; prose-only URLs are unverified.
"""

from collections import defaultdict
from collections.abc import Sequence

from app.audit.metrics import run_outcome
from app.audit.records import RunRecord
from app.schemas import Mention, SourceGapEntry, SourceGapReport


def _domain(domain: str) -> str:
    return domain.strip().lower().rstrip(".").removeprefix("www.")


def _grounded_domains(mention: Mention) -> set[str]:
    return {_domain(c.domain) for c in mention.citations if c.source == "grounding_metadata"}


def compute_source_gap(mentions: Sequence[Mention]) -> SourceGapReport:
    brand_domains: set[str] = set()
    frequency: dict[str, int] = defaultdict(int)
    competitors: dict[str, set[str]] = defaultdict(set)

    for mention in mentions:
        domains = _grounded_domains(mention)
        if mention.brand_mentioned:
            brand_domains |= domains
            continue
        if mention.competitors_seen:
            for domain in domains:
                frequency[domain] += 1
                competitors[domain].update(mention.competitors_seen)

    entries = [
        SourceGapEntry(
            domain=domain,
            frequency=count,
            competitors=sorted(competitors[domain]),
        )
        for domain, count in frequency.items()
        if domain not in brand_domains
    ]
    entries.sort(key=lambda e: (-e.frequency, e.domain))
    return SourceGapReport(entries=entries)


def mentions_for_gap(runs: Sequence[RunRecord]) -> list[Mention]:
    """Mentions as the gap report should see them: brand_mentioned is the CONFIRMED outcome
    (verified or human-approved), so an unverified claim never hides a gap."""
    mentions: list[Mention] = []
    for run in runs:
        if run.status != "ok" or run.mention is None:
            continue
        mentions.append(
            run.mention.model_copy(
                update={
                    "brand_mentioned": bool(run_outcome(run)),
                    "citations": [c for c in run.citations if c.source == "grounding_metadata"],
                }
            )
        )
    return mentions
