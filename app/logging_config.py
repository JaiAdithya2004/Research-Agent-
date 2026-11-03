"""Structured JSON logs that cannot contain secrets.

Two layers:
  1. The code never logs credentials in the first place (a test proves it, with redaction OFF).
  2. A redaction layer is a safety net: every log record's message is scrubbed as the record is
     created, and every formatted traceback is scrubbed again, so a future mistake or a
     third-party library still cannot print a key.
"""

import contextvars
import json
import logging
import os
import re
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

REDACTED = "[REDACTED]"
_MIN_SECRET_LENGTH = 8  # shorter env values ("true", "dev") are not secrets worth masking
_SECRET_ENV_NAME = re.compile(r"(KEY|SECRET|TOKEN|PASSWORD|PEPPER|DSN|CREDENTIAL)", re.IGNORECASE)

_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # our own API keys
    (re.compile(r"\bava_[A-Za-z0-9_\-]{8,}"), REDACTED),
    # Authorization / X-API-Key style header values
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 " + REDACTED),
    (
        re.compile(
            r"(?i)(x-api-key|api[-_]?key|authorization)(['\"]?\s*[:=]\s*['\"]?)[^\s'\",;}]+"
        ),
        r"\1\2" + REDACTED,
    ),
    # passwords inside connection URLs: scheme://user:password@host
    (re.compile(r"(?i)(\b[a-z][a-z0-9+.\-]*://[^\s:/@]*:)[^\s@/]+(@)"), r"\1" + REDACTED + r"\2"),
    # provider-style keys
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), REDACTED),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"), REDACTED),
]


def env_secrets(environ: dict[str, str] | None = None) -> list[str]:
    env = environ if environ is not None else dict(os.environ)
    return sorted(
        {v for k, v in env.items() if _SECRET_ENV_NAME.search(k) and len(v) >= _MIN_SECRET_LENGTH},
        key=len,
        reverse=True,
    )


def redact(text: str, extra_secrets: Iterable[str] = ()) -> str:
    """Mask credentials in `text`. Idempotent. Exact secret values are masked first."""
    secrets_ = sorted(
        {s for s in (*env_secrets(), *extra_secrets) if len(s) >= _MIN_SECRET_LENGTH},
        key=len,
        reverse=True,
    )
    for secret in secrets_:
        text = text.replace(secret, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# --- context: fields attached to every record emitted while they are bound ---

_context: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "log_context", default=None
)


def bind_log_context(**fields: Any) -> contextvars.Token[dict[str, Any] | None]:
    current = _context.get() or {}
    return _context.set({**current, **{k: v for k, v in fields.items() if v is not None}})


def reset_log_context(token: contextvars.Token[dict[str, Any] | None]) -> None:
    _context.reset(token)


# --- redaction as the record is created ---

_original_factory = logging.getLogRecordFactory()
_installed_factory: Any = None
_extra_secrets: list[str] = []


def _redacting_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    record = _original_factory(*args, **kwargs)
    try:
        message = record.getMessage()
    except Exception:  # a broken format string must not break logging
        message = str(record.msg)
    record.msg = redact(message, _extra_secrets)
    record.args = ()
    for name, value in (_context.get() or {}).items():
        if not hasattr(record, name):
            setattr(record, name, value)
    return record


def install_redaction(extra_secrets: Iterable[str] = ()) -> None:
    """Idempotent. Scrubs every record from every logger, including third-party ones."""
    global _installed_factory
    _extra_secrets[:] = list(extra_secrets)
    if _installed_factory is None:
        _installed_factory = _redacting_factory
        logging.setLogRecordFactory(_redacting_factory)


def uninstall_redaction() -> None:
    global _installed_factory
    if _installed_factory is not None:
        logging.setLogRecordFactory(_original_factory)
        _installed_factory = None


class RedactingFormatter(logging.Formatter):
    """Scrubs the final text too, which includes formatted exception tracebacks."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record), _extra_secrets)


_STANDARD = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(RedactingFormatter):
    """One JSON object per line with the fields the spec lists: audit_id, question_id, model, url,
    attempt, latency_ms, cost_usd (whichever apply), plus any other `extra` fields."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return redact(json.dumps(payload, default=str, ensure_ascii=False), _extra_secrets)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Call once at process start (API and worker)."""
    install_redaction()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        JsonFormatter() if json_output else RedactingFormatter("%(levelname)s %(name)s %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # Libraries that can echo headers, bodies or SQL parameters stay quiet unless asked.
    for noisy in ("httpx", "httpcore", "sqlalchemy.engine", "arq.worker"):
        logging.getLogger(noisy).setLevel(max(logging.getLogger().level, logging.WARNING))
