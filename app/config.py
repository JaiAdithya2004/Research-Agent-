"""Runtime settings, read from environment variables only (see .env.example).

Fields holding secrets are excluded from repr, so a Settings object that ends up in a log line
or a traceback never prints them.
"""

import os
from dataclasses import dataclass, field

from app.envfile import load_env_file


@dataclass(frozen=True)
class Settings:
    database_url: str = field(
        default="postgresql+psycopg://audit:audit@localhost:5432/audit", repr=False
    )
    redis_url: str = field(default="redis://localhost:6379/0", repr=False)
    api_key_pepper: str = field(default="", repr=False)  # keys the stored hashes of API keys
    default_cost_cap_usd: float = 0.75
    queue_backend: str = "arq"  # "arq" (production) or "inprocess" (local dev, not durable)
    rate_limit_default_per_min: int = 60
    ready_timeout_s: float = 2.0
    answer_models: str = "gemini:gemini-3.8-flash,openai:gpt-6.1-terra"
    extractor_model: str = "gemini:gemini-3.8-flash"
    extractor_prompt_version: str = "v1"  # prompts/extract_<version>.txt
    sentry_dsn: str = field(default="", repr=False)
    environment: str = "development"

    @classmethod
    def from_env(cls) -> "Settings":
        load_env_file()
        env = os.environ
        d = cls()
        return cls(
            database_url=env.get("DATABASE_URL", d.database_url),
            redis_url=env.get("REDIS_URL", d.redis_url),
            api_key_pepper=env.get("API_KEY_PEPPER", ""),
            default_cost_cap_usd=float(env.get("DEFAULT_COST_CAP_USD", d.default_cost_cap_usd)),
            queue_backend=env.get("QUEUE_BACKEND", d.queue_backend),
            rate_limit_default_per_min=int(
                env.get("RATE_LIMIT_DEFAULT_PER_MIN", d.rate_limit_default_per_min)
            ),
            ready_timeout_s=float(env.get("READY_TIMEOUT_S", d.ready_timeout_s)),
            answer_models=env.get("ANSWER_MODELS", d.answer_models),
            extractor_model=env.get("EXTRACTOR_MODEL", d.extractor_model),
            extractor_prompt_version=env.get(
                "EXTRACTOR_PROMPT_VERSION", d.extractor_prompt_version
            ),
            sentry_dsn=env.get("SENTRY_DSN", ""),
            environment=env.get("APP_ENV", d.environment),
        )
