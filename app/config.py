"""Runtime settings, read from environment variables only (see .env.example).

Settings is a frozen Pydantic model, so a bad value stops the process at startup with a message
that names the environment variable. Fields holding secrets are excluded from repr, so a Settings
object that ends up in a log line or a traceback never prints them.
"""

import os
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.envfile import load_env_file


class ConfigError(Exception):
    """The environment holds a value the app cannot run with. Never contains the value itself."""


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True)

    database_url: str = Field(
        default="postgresql+psycopg://audit:audit@localhost:5432/audit", repr=False, min_length=1
    )
    redis_url: str = Field(default="redis://localhost:6379/0", repr=False, min_length=1)
    api_key_pepper: str = Field(default="", repr=False)  # keys the stored hashes of API keys
    default_cost_cap_usd: float = Field(default=0.75, gt=0)
    # "inprocess" is for local dev and the Render free tier: jobs are not durable.
    queue_backend: Literal["arq", "inprocess"] = "arq"
    rate_limit_default_per_min: int = Field(default=60, gt=0)
    ready_timeout_s: float = Field(default=2.0, gt=0)
    answer_models: str = "gemini:gemini-3.8-flash,openai:gpt-6.1-terra"
    extractor_model: str = "gemini:gemini-3.8-flash"
    extractor_prompt_version: str = "v1"  # prompts/extract_<version>.txt
    sentry_dsn: str = Field(default="", repr=False)
    environment: str = "development"

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file()
        env = os.environ
        names = {
            "database_url": "DATABASE_URL",
            "redis_url": "REDIS_URL",
            "api_key_pepper": "API_KEY_PEPPER",
            "default_cost_cap_usd": "DEFAULT_COST_CAP_USD",
            "queue_backend": "QUEUE_BACKEND",
            "rate_limit_default_per_min": "RATE_LIMIT_DEFAULT_PER_MIN",
            "ready_timeout_s": "READY_TIMEOUT_S",
            "answer_models": "ANSWER_MODELS",
            "extractor_model": "EXTRACTOR_MODEL",
            "extractor_prompt_version": "EXTRACTOR_PROMPT_VERSION",
            "sentry_dsn": "SENTRY_DSN",
            "environment": "APP_ENV",
        }
        values = {field: env[name] for field, name in names.items() if name in env}
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            bad = sorted({names[str(e["loc"][0])] for e in exc.errors() if e["loc"]})
            raise ConfigError(f"invalid environment settings: {', '.join(bad)}") from None
