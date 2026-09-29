"""Mapping of Telethon message-like objects to transport-neutral DTOs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from .links import build_message_link, normalize_username
from .models import (
    CollectedTelegramMessage,
    TelegramContentKind,
    TelegramSource,
)


def ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        # Telegram dates are UTC.  This branch also makes simple test doubles
        # deterministic while keeping every persisted timestamp aware.
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def message_to_dto(
    message: Any,
    source: TelegramSource,
) -> CollectedTelegramMessage | None:
    """Map only ordinary text and captions; never inspect media bytes."""

    text = getattr(message, "message", None)
    if not isinstance(text, str) or not text.strip():
        return None

    message_id = getattr(message, "id", None)
    published_at = getattr(message, "date", None)
    if not isinstance(message_id, int) or message_id <= 0:
        return None
    if not isinstance(published_at, datetime):
        return None

    sender = getattr(message, "sender", None)
    author_username = normalize_username(getattr(sender, "username", None))
    author_display_name = _display_name(sender)
    edited_at = getattr(message, "edit_date", None)
    if not isinstance(edited_at, datetime):
        edited_at = None

    return CollectedTelegramMessage(
        source=source,
        telegram_message_id=message_id,
        published_at=ensure_utc(published_at),
        text=text,
        content_kind=(
            TelegramContentKind.CAPTION
            if getattr(message, "media", None) is not None
            else TelegramContentKind.TEXT
        ),
        telegram_link=build_message_link(source, message_id),
        author_id=_optional_int(getattr(message, "sender_id", None)),
        author_username=author_username,
        author_display_name=author_display_name,
        author_signature=_optional_string(getattr(message, "post_author", None)),
        reply_to_message_id=_optional_int(getattr(message, "reply_to_msg_id", None)),
        grouped_id=_optional_int(getattr(message, "grouped_id", None)),
        edited_at=ensure_utc(edited_at) if edited_at is not None else None,
    )


def _display_name(sender: Any) -> str | None:
    if sender is None:
        return None
    title = _optional_string(getattr(sender, "title", None))
    if title:
        return title
    parts = [
        _optional_string(getattr(sender, "first_name", None)),
        _optional_string(getattr(sender, "last_name", None)),
    ]
    full_name = " ".join(part for part in parts if part)
    return full_name or None


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None
