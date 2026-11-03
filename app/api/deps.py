import hmac
from collections.abc import AsyncIterator

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.worker.queue import AuditQueue


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_queue(request: Request) -> AuditQueue:
    queue: AuditQueue = request.app.state.queue
    return queue


async def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> None:
    expected: str | None = request.app.state.settings.api_key
    if not expected or not x_api_key or not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="missing or invalid API key")


SessionDep = Depends(get_session)
QueueDep = Depends(get_queue)
