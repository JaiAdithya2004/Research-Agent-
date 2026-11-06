"""Builds the real dependencies (model clients, fetcher, config) from environment settings."""

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.audit.events import EventSink, InMemoryEvents
from app.audit.runner import AuditConfig, ModelTarget
from app.config import Settings
from app.extract.extractor import Generator
from app.extract.prompts import load_prompt
from app.fetch.safe_fetch import SafeFetcher, SnapshotStore
from app.llm.client import LLMClient
from app.llm.cost import CostTracker
from app.research.researcher import Fetcher
from app.worker.jobs import Runtime

_KEY_ENV = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY"}


def parse_targets(spec: str) -> list[ModelTarget]:
    targets: list[ModelTarget] = []
    for item in spec.split(","):
        provider, _, model = item.strip().partition(":")
        if provider and model:
            targets.append(ModelTarget(provider, model))
    return targets


def configured_targets(settings: Settings) -> list[ModelTarget]:
    """Answer models whose provider has an API key in the environment."""
    return [
        t for t in parse_targets(settings.answer_models) if os.environ.get(_KEY_ENV[t.provider])
    ]


def build_runtime(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    events: EventSink | None = None,
) -> Runtime:
    extractor = parse_targets(settings.extractor_model)[0]
    # Fail at startup, not inside a worker, if the chosen prompt version has no files.
    load_prompt("extract", settings.extractor_prompt_version)
    load_prompt("extract_repair", settings.extractor_prompt_version)
    targets = configured_targets(settings) or [extractor]

    def make_llm(tracker: CostTracker) -> Generator:
        return LLMClient.from_env(cost_cap_usd=tracker.cap_usd, tracker=tracker)

    def make_fetcher(store: SnapshotStore) -> Fetcher:
        return SafeFetcher(store=store)

    return Runtime(
        session_factory=session_factory,
        make_llm=make_llm,
        make_fetcher=make_fetcher,
        config=AuditConfig(
            targets=targets,
            extractor=extractor,
            extractor_prompt_version=settings.extractor_prompt_version,
        ),
        events=events or InMemoryEvents(),
    )
