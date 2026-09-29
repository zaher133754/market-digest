from __future__ import annotations

import pytest

from market_digest.telegram.links import (
    build_message_link,
    build_source_link,
    normalize_username,
)
from market_digest.telegram.models import TelegramSource, TelegramSourceKind


def _source(
    *,
    kind: TelegramSourceKind,
    peer_id: int,
    username: str | None = None,
) -> TelegramSource:
    return TelegramSource(
        entity_id=123456,
        peer_id=peer_id,
        kind=kind,
        title="Источник",
        username=username,
        link=build_source_link(username),
    )


def test_public_message_link_uses_username() -> None:
    source = _source(
        kind=TelegramSourceKind.CHANNEL,
        peer_id=-1_000_000_123_456,
        username="@market_news",
    )

    assert build_message_link(source, 42) == "https://t.me/market_news/42"


def test_private_channel_message_link_uses_internal_entity_id() -> None:
    source = _source(
        kind=TelegramSourceKind.CHANNEL,
        peer_id=-1_000_000_123_456,
    )

    assert build_message_link(source, 42) == "https://t.me/c/123456/42"


def test_legacy_basic_group_has_no_stable_message_link() -> None:
    source = _source(kind=TelegramSourceKind.CHAT, peer_id=-123456)

    assert build_message_link(source, 42) is None


@pytest.mark.parametrize("username", [None, "", "@bad-name", "@@name"])
def test_invalid_username_does_not_create_link(username: str | None) -> None:
    assert normalize_username(username) is None
    assert build_source_link(username) is None


def test_non_positive_message_id_is_rejected() -> None:
    source = _source(kind=TelegramSourceKind.CHANNEL, peer_id=-1_000_000_123_456)
    with pytest.raises(ValueError, match="positive"):
        build_message_link(source, 0)
