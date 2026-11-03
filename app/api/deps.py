import logging
from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.authenticator import Principal
from app.ratelimit import enforce
from app.worker.queue import AuditQueue

logger = logging.getLogger("app.api")

__all__ = ["Principal", "QueueDep", "SessionDep", "get_queue", "get_session", "require_api_key"]


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_queue(request: Request) -> AuditQueue:
    queue: AuditQueue = request.app.state.queue
    return queue


async def require_api_key(
    request: Request,
    response: Response,
    x_api_key: str | None = Header(default=None),
) -> Principal:
    """Authenticate, then rate limit. Every failure to authenticate looks the same."""
    key = (x_api_key or "").strip()
    principal: Principal | None = await request.app.state.authenticator(key) if key else None
    if principal is None:
        raise HTTPException(status_code=401, detail="missing or invalid API key")
    await enforce(
        request.app.state.rate_limiter,
        principal,
        request.app.state.settings.rate_limit_default_per_min,
        response,
    )
    return principal


SessionDep = Depends(get_session)
QueueDep = Depends(get_queue)
