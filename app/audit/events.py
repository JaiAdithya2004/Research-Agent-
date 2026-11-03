"""Progress events. Events carry identifiers and counts only, never answers or page text."""

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

EventType = Literal[
    "audit_started",
    "run_started",
    "answer_extracted",
    "run_verified",
    "run_failed",
    "source_fetched",
    "audit_completed",
    "audit_capped",
    "audit_failed",
]

TERMINAL_EVENTS: frozenset[str] = frozenset({"audit_completed", "audit_capped", "audit_failed"})


class Event(BaseModel):
    model_config = ConfigDict(extra="forbid")

    audit_id: str
    type: EventType
    data: dict[str, Any] = Field(default_factory=dict)
    seq: int | None = None  # assigned by the store; strictly increasing per audit


class EventSink(Protocol):
    async def emit(self, audit_id: str, type: EventType, data: dict[str, Any]) -> None: ...


class InMemoryEvents:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def emit(self, audit_id: str, type: EventType, data: dict[str, Any]) -> None:
        seq = 1 + sum(1 for e in self.events if e.audit_id == audit_id)
        self.events.append(Event(audit_id=audit_id, type=type, data=data, seq=seq))
