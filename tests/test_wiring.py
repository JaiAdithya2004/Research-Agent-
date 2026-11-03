import importlib

import pytest

PACKAGES = ["llm", "extract", "verify", "fetch", "research", "api", "db", "worker"]


@pytest.mark.parametrize("name", PACKAGES)
def test_app_subpackage_imports(name: str) -> None:
    assert importlib.import_module(f"app.{name}") is not None
