"""Load and strictly validate the labeled eval files, with errors that say what is wrong."""

import json
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.evals.models import InjectionPage, LabeledAnswer, LabeledPage

T = TypeVar("T", bound=BaseModel)


class DatasetError(Exception):
    pass


def _load(path: Path, model: type[T], what: str) -> list[T]:
    if not path.is_file():
        raise DatasetError(
            f"{what} file not found: {path}. See evals/README.md for the expected format."
        )
    try:
        raw = json.loads(path.read_text(encoding="utf8"))
    except json.JSONDecodeError as exc:
        raise DatasetError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, list) or not raw:
        raise DatasetError(f"{path} must contain a non-empty JSON array of {what} items")
    items: list[T] = []
    seen: set[str] = set()
    for index, entry in enumerate(raw):
        try:
            item = model.model_validate(entry)
        except ValidationError as exc:
            fields = "; ".join(
                f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:4]
            )
            raise DatasetError(
                f"{path} item #{index} is not a valid {what} item: {fields}"
            ) from exc
        item_id = getattr(item, "id")  # noqa: B009 - every dataset model has an id
        if item_id in seen:
            raise DatasetError(f"{path} has a duplicate id {item_id!r}")
        seen.add(item_id)
        items.append(item)
    return items


def load_answers(path: Path) -> list[LabeledAnswer]:
    return _load(path, LabeledAnswer, "labeled answer")


def load_pages(path: Path) -> list[LabeledPage]:
    return _load(path, LabeledPage, "labeled page")


def load_injection(path: Path) -> list[InjectionPage]:
    return _load(path, InjectionPage, "injection page")
