"""A fake website for the evals: real SafeFetcher code paths, no network.

The fetcher, HTML extraction, paywall detection and brand check all run for real; only the
sockets are replaced. Every eval page is served from one fake public host.
"""

from collections.abc import AsyncIterator

import httpx

from app.fetch.safe_fetch import InMemorySnapshotStore, SafeFetcher

HOST = "eval.pages.example"
PUBLIC_IP = "93.184.216.34"


class StaticSite:
    def __init__(self) -> None:
        self._pages: dict[str, tuple[int, str, bytes]] = {}

    def add(self, path: str, *, status: int, content_type: str, body: str) -> str:
        self._pages[path] = (status, content_type, body.encode("utf8"))
        return f"https://{HOST}{path}"

    async def _resolve(self, host: str, port: int) -> list[str]:
        return [PUBLIC_IP]

    async def _handle(self, request: httpx.Request) -> httpx.Response:
        page = self._pages.get(request.url.path)
        if request.url.path == "/robots.txt" or page is None:
            return httpx.Response(404)
        status, content_type, body = page

        async def stream() -> AsyncIterator[bytes]:
            yield body

        return httpx.Response(status, headers={"content-type": content_type}, content=stream())

    def fetcher(self) -> SafeFetcher:
        return SafeFetcher(
            resolver=self._resolve,
            transport=httpx.MockTransport(self._handle),
            store=InMemorySnapshotStore(),
        )
