"""Operational endpoints: liveness is /health (in main.py); readiness is /ready."""

import asyncio
import logging
from typing import Any, Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import text

logger = logging.getLogger("app.api")
router = APIRouter()

CheckResult = Literal["ok", "failed", "timeout", "not_used"]


class ReadyResponse(BaseModel):
    status: Literal["ready", "unavailable"]
    checks: dict[str, CheckResult]


async def _check(name: str, coro: Any, timeout_s: float) -> CheckResult:
    """Run one dependency check. Only a fixed label is returned: never an exception message,
    a host name or a connection string."""
    try:
        await asyncio.wait_for(coro, timeout=timeout_s)
        return "ok"
    except TimeoutError:
        logger.warning("ready_check_timeout", extra={"check": name})
        return "timeout"
    except Exception as exc:
        logger.warning("ready_check_failed", extra={"check": name, "error": type(exc).__name__})
        return "failed"


@router.get(
    "/ready",
    response_model=ReadyResponse,
    responses={503: {"model": ReadyResponse, "description": "A dependency is unavailable"}},
    summary="Readiness: can this instance do its job (database and Redis)?",
)
async def ready(request: Request) -> JSONResponse:
    state = request.app.state
    timeout = state.settings.ready_timeout_s

    async def db() -> None:
        async with state.session_factory() as session:
            await session.execute(text("SELECT 1"))

    checks: dict[str, CheckResult] = {"db": await _check("db", db(), timeout)}
    ping = getattr(state.queue, "ping", None)
    checks["redis"] = await _check("redis", ping(), timeout) if ping else "not_used"

    ok = all(v in ("ok", "not_used") for v in checks.values())
    body = ReadyResponse(status="ready" if ok else "unavailable", checks=checks)
    return JSONResponse(body.model_dump(), status_code=200 if ok else 503)
