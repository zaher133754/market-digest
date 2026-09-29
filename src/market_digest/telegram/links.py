"""Pure Telegram link helpers."""

from __future__ import annotations

import re

from .models import TelegramSource, TelegramSourceKind

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,}$")


def normalize_username(username: str | None) -> str | None:
    if username is None:
        return None
    value = username.strip().removeprefix("@").strip()
    return value if _USERNAME_RE.fullmatch(value) else None


def build_source_link(username: str | None) -> str | None:
    normalized = normalize_username(username)
    return f"https://t.me/{normalized}" if normalized else None


def build_message_link(source: TelegramSource, message_id: int) -> str | None:
    """Build a public or private channel/supergroup message link.

    Telegram does not expose stable ``t.me/c`` links for legacy basic groups.
    """

    if message_id <= 0:
        raise ValueError("message_id must be positive")

    username = normalize_username(source.username)
    if username:
        return f"https://t.me/{username}/{message_id}"
    if source.kind in {TelegramSourceKind.CHANNEL, TelegramSourceKind.CHAT}:
        # ``t.me/c`` works for channels and channel-backed supergroups.  Basic
        # groups have a marked peer id in the -1.. range instead of -100.. .
        if str(source.peer_id).startswith("-100"):
            return f"https://t.me/c/{source.entity_id}/{message_id}"
    return None
