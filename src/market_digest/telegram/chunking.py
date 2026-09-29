"""Telegram-safe plain-text splitting."""

from __future__ import annotations

TELEGRAM_TEXT_LIMIT = 4096


def split_telegram_text(
    text: str,
    *,
    limit: int = TELEGRAM_TEXT_LIMIT,
) -> list[str]:
    """Split text without dropping content and keep every chunk under ``limit``.

    Paragraph, line, and whitespace boundaries are preferred in that order.
    A single overlong token is hard-split.  The delivery bot sends plain text,
    so no HTML/Markdown entity expansion has to be accounted for.
    """

    if limit < 1:
        raise ValueError("limit must be positive")
    if not text:
        return []

    remaining = text
    chunks: list[str] = []
    while len(remaining) > limit:
        window = remaining[: limit + 1]
        cut = _best_boundary(window, limit)
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]

    if remaining:
        chunks.append(remaining)
    return chunks


def _best_boundary(window: str, limit: int) -> int:
    # Avoid tiny chunks when a much later hard boundary is available.
    lower_bound = max(1, limit // 2)
    for separator in ("\n\n", "\n", " "):
        index = window.rfind(separator, lower_bound, limit + 1)
        if index >= lower_bound:
            return index + len(separator)
    return limit
