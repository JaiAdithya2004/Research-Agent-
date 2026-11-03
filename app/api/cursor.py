"""Opaque keyset cursors: (timestamp, uuid), newest first, stable under concurrent inserts."""

import base64
import uuid
from datetime import datetime

from fastapi import HTTPException


def encode_cursor(created_at: datetime, row_id: uuid.UUID) -> str:
    raw = f"{created_at.isoformat()}|{row_id}"
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        text = base64.urlsafe_b64decode(cursor.encode()).decode()
        stamp, _, ident = text.partition("|")
        return datetime.fromisoformat(stamp), uuid.UUID(ident)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(422, "invalid cursor") from exc
