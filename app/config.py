"""Runtime settings, read from environment variables only (see .env.example)."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "postgresql+psycopg://audit:audit@localhost:5432/audit"
    redis_url: str = "redis://localhost:6379/0"
    api_key: str | None = None  # static dev key; replaced by hashed DB keys in the auth phase
    default_cost_cap_usd: float = 0.75
    answer_models: str = "gemini:gemini-3.8-flash,openai:gpt-6.1-terra"
    extractor_model: str = "gemini:gemini-3.8-flash"

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ
        return cls(
            database_url=env.get("DATABASE_URL", cls.database_url),
            redis_url=env.get("REDIS_URL", cls.redis_url),
            api_key=env.get("API_KEY") or None,
            default_cost_cap_usd=float(env.get("DEFAULT_COST_CAP_USD", cls.default_cost_cap_usd)),
            answer_models=env.get("ANSWER_MODELS", cls.answer_models),
            extractor_model=env.get("EXTRACTOR_MODEL", cls.extractor_model),
        )
