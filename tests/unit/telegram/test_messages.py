from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from market_digest.telegram.messages import message_to_dto
from market_digest.telegram.models import (
    TelegramContentKind,
    TelegramSource,
    TelegramSourceKind,
)


@pytest.fixture
def source() -> TelegramSource:
    return TelegramSource(
        entity_id=123,
        peer_id=-1_000_000_000_123,
        kind=TelegramSourceKind.CHAT,
        title="Чат",
        username="market_chat",
        link="https://t.me/market_chat",
    )


def test_maps_full_text_and_author_metadata(source: TelegramSource) -> None:
    message = SimpleNamespace(
        id=77,
        date=datetime(2026, 8, 27, 8, 0, tzinfo=UTC),
        message="Рынок сегодня выглядит сильно",
        media=None,
        sender_id=99,
        sender=SimpleNamespace(
            username="trader_99",
            first_name="Иван",
            last_name="Петров",
            title=None,
        ),
        post_author=None,
        reply_to_msg_id=70,
        grouped_id=None,
        edit_date=None,
    )

    dto = message_to_dto(message, source)

    assert dto is not None
    assert dto.text == message.message
    assert dto.content_kind is TelegramContentKind.TEXT
    assert dto.author_id == 99
    assert dto.author_display_name == "Иван Петров"
    assert dto.reply_to_message_id == 70
    assert dto.idempotency_key == "telegram:-1000000000123:77"


def test_media_caption_is_kept_without_reading_media(source: TelegramSource) -> None:
    message = SimpleNamespace(
        id=78,
        date=datetime(2026, 8, 27, 8, 0),
        message="Подпись к графику",
        media=object(),
        sender_id=None,
        sender=None,
        post_author="Редакция",
        reply_to_msg_id=None,
        grouped_id=500,
        edit_date=None,
    )

    dto = message_to_dto(message, source)

    assert dto is not None
    assert dto.content_kind is TelegramContentKind.CAPTION
    assert dto.published_at.tzinfo is UTC
    assert dto.author_signature == "Редакция"
    assert dto.grouped_id == 500


@pytest.mark.parametrize("text", [None, "", "  \n"])
def test_media_without_caption_and_empty_text_are_ignored(
    source: TelegramSource, text: str | None
) -> None:
    message = SimpleNamespace(
        id=79,
        date=datetime(2026, 8, 27, 8, 0, tzinfo=UTC),
        message=text,
        media=object(),
    )

    assert message_to_dto(message, source) is None
