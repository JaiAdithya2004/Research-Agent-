"""render.yaml is deployment config: it must never carry a secret, and the free setup must not
depend on a worker or Redis."""

import re
from pathlib import Path

BLUEPRINT = (Path(__file__).resolve().parents[1] / "render.yaml").read_text(encoding="utf-8")

SECRETS = [
    "DATABASE_URL",
    "API_KEY_PEPPER",
    "GEMINI_API_KEY",
    "OPENAI_API_KEY",
    "TAVILY_API_KEY",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "SENTRY_DSN",
]


def _entry(key: str) -> str:
    for chunk in BLUEPRINT.split("- key: ")[1:]:
        if chunk.startswith(f"{key}\n"):
            return chunk
    raise AssertionError(f"{key} is not declared in render.yaml")


def test_every_secret_is_declared_but_left_for_the_dashboard() -> None:
    for key in SECRETS:
        assert "sync: false" in _entry(key), key
        assert "value:" not in _entry(key), key


def test_free_setup_runs_jobs_in_the_api_process() -> None:
    assert re.search(r"- key: QUEUE_BACKEND\n\s+value: inprocess", BLUEPRINT)
    assert "type: worker" not in BLUEPRINT
    assert "keyvalue" not in BLUEPRINT


def test_builds_the_api_image_on_the_free_plan_with_a_liveness_check() -> None:
    assert "plan: free" in BLUEPRINT
    assert "dockerfilePath: ./docker/api.Dockerfile" in BLUEPRINT
    assert "healthCheckPath: /health" in BLUEPRINT


def test_production_uses_extractor_prompt_v2() -> None:
    assert "value: v2" in _entry("EXTRACTOR_PROMPT_VERSION")
