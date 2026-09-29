from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from market_digest.telegram.collector import (
    TelegramCollector,
    TelegramCollectorConfig,
)
from market_digest.telegram.models import CollectedTelegramMessage
from market_digest.telegram.sources import SourceHandle, source_from_entity


class FakeClient:
    def __init__(self, messages: list[Any]) -> None:
        self.messages = messages

    async def iter_messages(self, entity: Any) -> AsyncIterator[Any]:
        del entity
        for message in self.messages:
            yield message


def _handle() -> SourceHandle:
    entity = SimpleNamespace(
        id=123,
        title="Рынок",
        username="market",
        broadcast=True,
        megagroup=False,
        left=False,
        deactivated=False,
        migrated_to=None,
    )
    source = source_from_entity(entity)
    assert source is not None
    return SourceHandle(source=source, entity=entity)


def _message(message_id: int, date: datetime, text: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        date=date,
        message=text,
        media=None,
        sender_id=None,
        sender=None,
        post_author=None,
        reply_to_msg_id=None,
        grouped_id=None,
        edit_date=None,
    )


@pytest.mark.asyncio
async def test_backfill_is_bounded_inclusive_and_idempotent() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    messages = [
        _message(3, now - timedelta(hours=1), "Новый"),
        _message(2, now - timedelta(hours=24), "На границе"),
        _message(1, now - timedelta(hours=24, seconds=1), "Старый"),
    ]
    persisted: list[CollectedTelegramMessage] = []

    async def persist(message: CollectedTelegramMessage) -> bool:
        persisted.append(message)
        return True

    collector = TelegramCollector(
        FakeClient(messages),
        persist,
        config=TelegramCollectorConfig(initial_backfill_hours=24),
    )
    handle = _handle()

    await collector.collect_backfill([handle], now=now)
    await collector.collect_backfill([handle], now=now)

    assert [message.telegram_message_id for message in persisted] == [3, 2]


@pytest.mark.asyncio
async def test_backfill_does_not_silently_truncate_long_text() -> None:
    now = datetime(2026, 8, 27, 12, 0, tzinfo=UTC)
    long_text = "данные " * 100_000
    messages = [_message(1, now, long_text)]
    persisted: list[CollectedTelegramMessage] = []

    async def persist(message: CollectedTelegramMessage) -> bool:
        persisted.append(message)
        return True

    collector = TelegramCollector(FakeClient(messages), persist)
    await collector.collect_backfill([_handle()], now=now)

    assert len(persisted) == 1
    assert persisted[0].text == long_text


@pytest.mark.parametrize("hours", [0, 49])
def test_backfill_outside_mvp_window_is_rejected(hours: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 48"):
        TelegramCollectorConfig(initial_backfill_hours=hours)
