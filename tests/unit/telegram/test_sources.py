from __future__ import annotations

from types import SimpleNamespace

from market_digest.telegram.models import TelegramSourceKind
from market_digest.telegram.sources import (
    classify_entity,
    select_source_handles,
    source_from_entity,
)


def _channel(entity_id: int, title: str, **overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "id": entity_id,
        "title": title,
        "username": None,
        "broadcast": True,
        "megagroup": False,
        "left": False,
        "deactivated": False,
        "migrated_to": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _megagroup(entity_id: int, title: str, **overrides: object) -> SimpleNamespace:
    return _channel(
        entity_id,
        title,
        broadcast=False,
        megagroup=True,
        **overrides,
    )


def test_broadcast_and_megagroup_are_classified_separately() -> None:
    assert classify_entity(_channel(1, "Новости")) is TelegramSourceKind.CHANNEL
    assert classify_entity(_megagroup(2, "Трейдеры")) is TelegramSourceKind.CHAT


def test_users_left_channels_and_migrated_groups_are_not_sources() -> None:
    user = SimpleNamespace(id=5, first_name="User")

    assert classify_entity(user) is None
    assert classify_entity(_channel(1, "left", left=True)) is None
    assert classify_entity(_megagroup(2, "old", migrated_to=object())) is None


def test_source_has_marked_peer_id_and_public_link() -> None:
    source = source_from_entity(_channel(123, "Новости", username="news_feed"))

    assert source is not None
    assert source.entity_id == 123
    assert source.peer_id == -1_000_000_000_123
    assert source.link == "https://t.me/news_feed"


def test_linked_discussion_group_is_excluded_but_channel_is_kept() -> None:
    channel = _channel(10, "Авторский канал")
    discussion = _megagroup(11, "Комментарии")
    regular_chat = _megagroup(12, "Рынок сегодня")

    handles = select_source_handles(
        [discussion, channel, regular_chat],
        linked_discussion_ids={11},
    )

    assert {handle.source.entity_id for handle in handles} == {10, 12}
    assert {handle.source.kind for handle in handles} == {
        TelegramSourceKind.CHANNEL,
        TelegramSourceKind.CHAT,
    }


def test_duplicate_dialog_peer_is_returned_once() -> None:
    entity = _channel(10, "Новости")

    assert len(select_source_handles([entity, entity])) == 1
