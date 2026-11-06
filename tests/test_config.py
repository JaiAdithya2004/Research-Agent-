"""Settings are validated at startup, with errors that name the environment variable."""

import pytest

from app.config import ConfigError, Settings


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NO_DOTENV", "1")  # never read the developer's .env
    for name in (
        "DATABASE_URL", "REDIS_URL", "API_KEY_PEPPER", "DEFAULT_COST_CAP_USD", "QUEUE_BACKEND",
        "RATE_LIMIT_DEFAULT_PER_MIN", "READY_TIMEOUT_S", "APP_ENV",
    ):  # fmt: skip
        monkeypatch.delenv(name, raising=False)


def test_defaults_and_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings.from_env().queue_backend == "arq"
    monkeypatch.setenv("DEFAULT_COST_CAP_USD", "1.5")
    monkeypatch.setenv("QUEUE_BACKEND", "inprocess")
    s = Settings.from_env()
    assert s.default_cost_cap_usd == 1.5 and s.queue_backend == "inprocess"


def test_a_bad_value_names_its_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEFAULT_COST_CAP_USD", "lots")
    with pytest.raises(ConfigError, match="DEFAULT_COST_CAP_USD"):
        Settings.from_env()


def test_an_unknown_queue_backend_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUEUE_BACKEND", "carrier-pigeon")
    with pytest.raises(ConfigError, match="QUEUE_BACKEND"):
        Settings.from_env()


@pytest.mark.parametrize(
    "name", ["DEFAULT_COST_CAP_USD", "RATE_LIMIT_DEFAULT_PER_MIN", "READY_TIMEOUT_S"]
)
def test_zero_or_negative_limits_are_rejected(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    monkeypatch.setenv(name, "0")
    with pytest.raises(ConfigError, match=name):
        Settings.from_env()


def test_the_error_never_echoes_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("QUEUE_BACKEND", "bad")
    with pytest.raises(ConfigError) as info:
        Settings.from_env()
    assert "postgresql" not in str(info.value)


def test_settings_are_frozen_and_hide_secrets_in_repr() -> None:
    s = Settings(api_key_pepper="pepper-secret", database_url="postgresql+psycopg://u:pw@h/db")
    assert "pepper-secret" not in repr(s) and "pw@h" not in repr(s)
    with pytest.raises(Exception):  # noqa: B017 - pydantic raises ValidationError for frozen models
        s.environment = "production"
