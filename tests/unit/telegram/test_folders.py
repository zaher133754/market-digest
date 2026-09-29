from __future__ import annotations

from types import SimpleNamespace

import pytest

from market_digest.telegram.folders import (
    TelegramFolderNotFound,
    TelegramFolderSelector,
    normalize_folder_title,
)
from market_digest.telegram.models import TelegramSourceKind


def _channel(entity_id: int, title: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=entity_id,
        title=title,
        username=None,
        broadcast=True,
        megagroup=False,
        left=False,
        deactivated=False,
        migrated_to=None,
    )


def _chat(entity_id: int, title: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=entity_id,
        title=title,
        username=None,
        broadcast=False,
        megagroup=True,
        left=False,
        deactivated=False,
        migrated_to=None,
    )


class FakeClient:
    def __init__(self, filters: list[object]) -> None:
        self.filters = filters

    async def __call__(self, request: object) -> object:
        return SimpleNamespace(filters=self.filters)

    async def get_entity(self, peer: object) -> object:
        return peer


def test_folder_titles_support_strings_and_text_objects() -> None:
    assert normalize_folder_title("  ПОСТЫ ") == "посты"
    assert normalize_folder_title(SimpleNamespace(text=" Чаты ")) == "чаты"


@pytest.mark.asyncio
async def test_folder_sources_are_deduplicated_excluded_and_type_checked() -> None:
    channel_a = _channel(1, "Канал A")
    channel_b = _channel(2, "Канал B")
    excluded = _channel(3, "Исключённый")
    chat = _chat(4, "Группа не того типа")
    folder = SimpleNamespace(
        title=SimpleNamespace(text=" Посты "),
        pinned_peers=[channel_a],
        include_peers=[channel_a, channel_b, excluded, chat],
        exclude_peers=[excluded],
    )

    snapshot = await TelegramFolderSelector(FakeClient([folder])).select(
        "ПОСТЫ", TelegramSourceKind.CHANNEL
    )

    assert [item.source.title for item in snapshot.sources] == ["Канал A", "Канал B"]
    assert snapshot.ignored_titles == ("Группа не того типа",)


@pytest.mark.asyncio
async def test_missing_folder_has_safe_error() -> None:
    selector = TelegramFolderSelector(FakeClient([]))

    with pytest.raises(TelegramFolderNotFound, match="Посты"):
        await selector.select("Посты", TelegramSourceKind.CHANNEL)
