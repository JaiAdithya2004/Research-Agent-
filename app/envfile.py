"""Load a `.env` file into the process environment (stdlib only; no new dependency).

Real environment variables always win, empty values are skipped, and values are never logged.
Set NO_DOTENV=1 to turn it off (the tests do).
"""

import os
import re
from pathlib import Path

_LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
DEFAULT_PATH = Path(__file__).resolve().parents[1] / ".env"


def load_env_file(path: Path | None = None) -> list[str]:
    """Returns the names that were set (never the values)."""
    if os.environ.get("NO_DOTENV") == "1":
        return []
    target = path or DEFAULT_PATH
    try:
        text = target.read_text(encoding="utf-8-sig")
    except OSError:
        return []
    loaded: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE.match(line)
        if not match:
            continue
        name, value = match.group(1), match.group(2).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if value and name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded
