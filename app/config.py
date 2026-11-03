"""Runtime settings, read from environment variables only (see .env.example)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg://audit:audit@localhost:5432/audit"
    redis_url: str = "redis://localhost:6379/0"
    api_key: str | None = None  # static dev key; replaced by hashed DB keys in the auth phase
    api_key_name: str = "api-key"  # recorded as the reviewer for actions made with this key
    default_cost_cap_usd: float = 0.75
    queue_backend: str = "arq"  # "arq" (production) or "inprocess" (local dev, not durable)
    answer_models: str = "gemini:gemini-3.8-flash,openai:gpt-6.1-terra"
    extractor_model: str = "gemini:gemini-3.8-flash"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            database_url=env.get("DATABASE_URL", cls.database_url),
            redis_url=env.get("REDIS_URL", cls.redis_url),
            api_key=env.get("API_KEY") or None,
            api_key_name=env.get("API_KEY_NAME", cls.api_key_name),
            default_cost_cap_usd=float(env.get("DEFAULT_COST_CAP_USD", cls.default_cost_cap_usd)),
            queue_backend=env.get("QUEUE_BACKEND", cls.queue_backend),
            answer_models=env.get("ANSWER_MODELS", cls.answer_models),
            extractor_model=env.get("EXTRACTOR_MODEL", cls.extractor_model),
        )
