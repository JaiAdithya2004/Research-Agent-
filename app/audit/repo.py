"""Persistence interface for audits, plus an in-memory implementation used by unit tests."""

from typing import Protocol

from app.audit.records import AuditState, RunRecord, StepKey
from app.research.researcher import ResearchedPage


class AuditRepo(Protocol):
    async def get_run(self, audit_id: str, key: StepKey) -> RunRecord | None: ...

    async def save_run(self, audit_id: str, record: RunRecord) -> None:
        """Insert or update. raw_answer and raw_grounding are write-once: changing one raises."""
        ...

    async def list_runs(self, audit_id: str) -> list[RunRecord]: ...

    async def load_evidence(self, audit_id: str) -> list[ResearchedPage]: ...

    async def save_evidence(self, audit_id: str, pages: list[ResearchedPage]) -> None:
        """Insert-if-absent by URL, so a retried job never duplicates evidence."""
        ...

    async def load_state(self, audit_id: str) -> AuditState: ...

    async def save_state(self, audit_id: str, state: AuditState) -> None: ...


def check_write_once(existing: RunRecord | None, new: RunRecord) -> None:
    if existing is None:
        return
    if existing.raw_answer is not None and new.raw_answer != existing.raw_answer:
        raise ValueError("runs.raw_answer is write-once")
    if existing.raw_grounding is not None and new.raw_grounding != existing.raw_grounding:
        raise ValueError("runs.raw_grounding is write-once")


class InMemoryAuditRepo:
    def __init__(self) -> None:
        self._runs: dict[str, dict[StepKey, RunRecord]] = {}
        self._evidence: dict[str, dict[str, ResearchedPage]] = {}
        self._state: dict[str, AuditState] = {}

    async def get_run(self, audit_id: str, key: StepKey) -> RunRecord | None:
        return self._runs.get(audit_id, {}).get(key)

    async def save_run(self, audit_id: str, record: RunRecord) -> None:
        runs = self._runs.setdefault(audit_id, {})
        check_write_once(runs.get(record.key), record)
        runs[record.key] = record.model_copy(deep=True)

    async def list_runs(self, audit_id: str) -> list[RunRecord]:
        return list(self._runs.get(audit_id, {}).values())

    async def load_evidence(self, audit_id: str) -> list[ResearchedPage]:
        return list(self._evidence.get(audit_id, {}).values())

    async def save_evidence(self, audit_id: str, pages: list[ResearchedPage]) -> None:
        store = self._evidence.setdefault(audit_id, {})
        for page in pages:
            store.setdefault(page.evidence.url, page.model_copy(deep=True))

    async def load_state(self, audit_id: str) -> AuditState:
        return self._state.get(audit_id, AuditState()).model_copy(deep=True)

    async def save_state(self, audit_id: str, state: AuditState) -> None:
        self._state[audit_id] = state.model_copy(deep=True)

    # --- synchronous helpers for tests ---
    def runs_of(self, audit_id: str) -> list[RunRecord]:
        return list(self._runs.get(audit_id, {}).values())

    def evidence_of(self, audit_id: str) -> list[ResearchedPage]:
        return list(self._evidence.get(audit_id, {}).values())

    def audit_state(self, audit_id: str) -> AuditState:
        return self._state.get(audit_id, AuditState())
