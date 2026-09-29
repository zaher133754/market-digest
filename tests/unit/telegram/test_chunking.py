from __future__ import annotations

import pytest

from market_digest.telegram.chunking import split_telegram_text


def test_short_text_is_not_changed() -> None:
    assert split_telegram_text("Короткий дайджест") == ["Короткий дайджест"]


def test_chunks_preserve_every_character_and_respect_limit() -> None:
    text = ("Первый абзац\n\n" + "рынок растёт " * 30 + "\n") * 20

    chunks = split_telegram_text(text, limit=137)

    assert "".join(chunks) == text
    assert chunks
    assert all(1 <= len(chunk) <= 137 for chunk in chunks)


def test_overlong_token_is_hard_split() -> None:
    text = "я" * 21

    assert split_telegram_text(text, limit=10) == ["я" * 10, "я" * 10, "я"]


@pytest.mark.parametrize("limit", [0, -1])
def test_invalid_limit_is_rejected(limit: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        split_telegram_text("text", limit=limit)


def test_empty_text_produces_no_chunks() -> None:
    assert split_telegram_text("") == []
