import importlib

import pytest

PACKAGES = ["llm", "extract", "verify", "fetch", "research", "api", "db", "worker"]


@pytest.mark.parametrize("name", PACKAGES)
def test_app_subpackage_imports(name: str) -> None:
    assert importlib.import_module(f"app.{name}") is not None


def test_the_api_launcher_asks_uvicorn_for_the_selector_loop_on_windows() -> None:
    from app.api.__main__ import loop_setting

    assert loop_setting("win32") == "asyncio:SelectorEventLoop"
    assert loop_setting("linux") == "asyncio"


def test_uvicorn_accepts_the_selector_loop_setting() -> None:
    import asyncio

    import uvicorn

    config = uvicorn.Config("app.api.main:app", loop="asyncio:SelectorEventLoop")
    factory = config.get_loop_factory()
    assert factory is not None and factory is asyncio.SelectorEventLoop
