from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from market_digest.reports.models import ReportKind
from market_digest.telegram.folders import FolderSnapshot
from market_digest.telegram.models import TelegramSourceKind
from market_digest.telegram.sources import source_from_entity
from market_digest.telegram.window import TelegramWindowLoader


def _entity(kind: TelegramSourceKind) -> SimpleNamespace:
    return SimpleNamespace(
        id=123,
        title="Источник",
        username="market_source",
        broadcast=kind is TelegramSourceKind.CHANNEL,
        megagroup=kind is TelegramSourceKind.CHAT,
        left=False,
        deactivated=False,
        migrated_to=None,
    )


def _message(message_id: int, published_at: datetime, text: str = "Текст") -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        date=published_at,
        message=text,
        media=None,
        sender_id=55,
        sender=SimpleNamespace(
            username="author",
            first_name="Иван",
            last_name="Петров",
            title=None,
        ),
        post_author=None,
        reply_to_msg_id=None,
        grouped_id=None,
        edit_date=None,
    )


class FakeSelector:
    def __init__(self, entity: object, ignored_titles: tuple[str, ...] = ()) -> None:
        source = source_from_entity(entity)
        assert source is not None
        self._snapshot = FolderSnapshot(
            requested_name="Посты",
            resolved_title="Посты",
            sources=(SimpleNamespace(source=source, entity=entity),),
            ignored_titles=ignored_titles,
        )
        self.calls: list[tuple[str, TelegramSourceKind]] = []

    async def select(self, folder_name: str, expected_kind: TelegramSourceKind) -> FolderSnapshot:
        self.calls.append((folder_name, expected_kind))
        return self._snapshot


class FakeClient:
    def __init__(self, messages: list[object]) -> None:
        self.messages = messages
        self.calls: list[tuple[object, datetime]] = []

    def iter_messages(self, entity: object, *, offset_date: datetime):
        self.calls.append((entity, offset_date))

        async def iterator():
            for message in self.messages:
                yield message

        return iterator()


@pytest.mark.asyncio
async def test_loads_exact_open_closed_24_hour_window_and_sorts_oldest_first() -> None:
    end = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)
    start = end - timedelta(hours=24)
    entity = _entity(TelegramSourceKind.CHANNEL)
    selector = FakeSelector(entity, ignored_titles=("Неподходящий чат",))
    client = FakeClient(
        [
            _message(5, end + timedelta(seconds=1), "Слишком новый"),
            _message(4, end, "Верхняя граница"),
            _message(3, start + timedelta(seconds=1), "Внутри окна"),
            _message(2, start, "Нижняя граница"),
            _message(1, start - timedelta(seconds=1), "Слишком старый"),
        ]
    )
    loader = TelegramWindowLoader(
        client,
        selector,
        posts_folder_name="Посты",
        chats_folder_name="Чаты",
    )

    result = await loader.load(ReportKind.DIGEST, end=end)

    assert selector.calls == [("Посты", TelegramSourceKind.CHANNEL)]
    assert client.calls == [(entity, end + timedelta(seconds=1))]
    assert result.window_start == start
    assert result.window_end == end
    assert result.source_count == 1
    assert [message.text for message in result.messages] == [
        "Внутри окна",
        "Верхняя граница",
    ]
    assert result.messages[0].message_ref.endswith(":3")
    assert result.messages[0].author_display_name == "Иван Петров"
    assert result.warnings == [
        "В папке «Посты» пропущен источник неподходящего типа: Неподходящий чат"
    ]


@pytest.mark.asyncio
async def test_sentiment_uses_chats_folder_and_safe_author_fallback() -> None:
    end = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
    entity = _entity(TelegramSourceKind.CHAT)
    selector = FakeSelector(entity)
    message = _message(9, end)
    message.sender = None
    message.post_author = None
    loader = TelegramWindowLoader(
        FakeClient([message]),
        selector,
        posts_folder_name="Посты",
        chats_folder_name="Чаты",
    )

    result = await loader.load(ReportKind.SENTIMENT, end=end)

    assert selector.calls == [("Чаты", TelegramSourceKind.CHAT)]
    assert result.messages[0].author_display_name == "Источник"


@pytest.mark.asyncio
async def test_empty_folder_returns_empty_report_input() -> None:
    end = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)
    entity = _entity(TelegramSourceKind.CHANNEL)
    selector = FakeSelector(entity)
    selector._snapshot = FolderSnapshot("Посты", "Посты", (), ())
    client = FakeClient([])
    loader = TelegramWindowLoader(
        client,
        selector,
        posts_folder_name="Посты",
        chats_folder_name="Чаты",
    )

    result = await loader.load(ReportKind.DIGEST, end=end)

    assert result.source_count == 0
    assert result.messages == []
    assert client.calls == []


@pytest.mark.asyncio
async def test_naive_window_end_is_rejected_before_telegram_calls() -> None:
    entity = _entity(TelegramSourceKind.CHANNEL)
    selector = FakeSelector(entity)
    loader = TelegramWindowLoader(
        FakeClient([]),
        selector,
        posts_folder_name="Посты",
        chats_folder_name="Чаты",
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        await loader.load(ReportKind.DIGEST, end=datetime(2026, 9, 20, 15, 0))

    assert selector.calls == []
