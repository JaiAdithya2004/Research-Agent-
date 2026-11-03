"""A minimal .env loader (no new dependency): never overrides the real environment."""

from pathlib import Path

import pytest

from app.envfile import load_env_file


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf8")
    return path


def test_loads_plain_quoted_and_commented_lines(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_DOTENV", raising=False)
    for name in ("A_ONE", "A_TWO", "A_THREE", "A_FOUR"):
        monkeypatch.delenv(name, raising=False)
    path = write(
        tmp_path,
        "# comment\nA_ONE=1\n\nA_TWO=\"two words\"\nA_THREE='x'  \nexport A_FOUR=4 # not a comment char\n",
    )
    loaded = load_env_file(path)
    import os

    assert os.environ["A_ONE"] == "1" and os.environ["A_TWO"] == "two words"
    assert os.environ["A_THREE"] == "x" and os.environ["A_FOUR"] == "4 # not a comment char"
    assert set(loaded) == {"A_ONE", "A_TWO", "A_THREE", "A_FOUR"}


def test_never_overrides_and_skips_empty_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    monkeypatch.delenv("NO_DOTENV", raising=False)
    monkeypatch.setenv("B_REAL", "from-shell")
    monkeypatch.delenv("B_EMPTY", raising=False)
    load_env_file(write(tmp_path, "B_REAL=from-file\nB_EMPTY=\n"))
    assert os.environ["B_REAL"] == "from-shell" and "B_EMPTY" not in os.environ


def test_missing_file_and_garbage_lines_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NO_DOTENV", raising=False)
    assert load_env_file(tmp_path / "nope") == []
    assert load_env_file(write(tmp_path, "no equals sign\n=novalue\n 1BAD=x\n")) == []


def test_can_be_disabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.delenv("C_ONE", raising=False)
    monkeypatch.setenv("NO_DOTENV", "1")
    assert load_env_file(write(tmp_path, "C_ONE=1\n")) == [] and "C_ONE" not in os.environ
