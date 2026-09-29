"""Discovery of already-subscribed Telegram broadcast channels and chats."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from telethon import functions, types, utils

from .links import build_source_link, normalize_username
from .models import TelegramSource, TelegramSourceKind

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceHandle:
    """A serializable source paired with its live Telethon input entity."""

    source: TelegramSource
    entity: Any


def classify_entity(entity: Any) -> TelegramSourceKind | None:
    """Classify accessible dialogs without relying on dialog display labels."""

    if bool(getattr(entity, "left", False)):
        return None
    if bool(getattr(entity, "deactivated", False)):
        return None
    if getattr(entity, "migrated_to", None) is not None:
        return None

    if bool(getattr(entity, "broadcast", False)):
        return TelegramSourceKind.CHANNEL
    if bool(getattr(entity, "megagroup", False)):
        return TelegramSourceKind.CHAT
    if isinstance(entity, types.Chat):
        return TelegramSourceKind.CHAT
    return None


def source_from_entity(entity: Any) -> TelegramSource | None:
    kind = classify_entity(entity)
    if kind is None:
        return None

    entity_id = int(entity.id)
    username = normalize_username(getattr(entity, "username", None))
    title = str(getattr(entity, "title", "") or "").strip()
    if not title:
        title = f"Telegram source {entity_id}"

    try:
        peer_id = int(utils.get_peer_id(entity))
    except (TypeError, ValueError):
        peer_id = (
            -1_000_000_000_000 - entity_id
            if bool(getattr(entity, "broadcast", False))
            or bool(getattr(entity, "megagroup", False))
            else -entity_id
        )

    return TelegramSource(
        entity_id=entity_id,
        peer_id=peer_id,
        kind=kind,
        title=title,
        username=username,
        link=build_source_link(username),
    )


def select_source_handles(
    entities: Iterable[Any],
    *,
    linked_discussion_ids: set[int] | frozenset[int] = frozenset(),
) -> list[SourceHandle]:
    """Pure filtering used by discovery and unit tests.

    ``linked_discussion_ids`` contains raw positive entity IDs, not marked peer
    IDs.  Broadcast channels are never removed merely because they have a
    discussion group; only the linked group is excluded.
    """

    selected: list[SourceHandle] = []
    seen_peer_ids: set[int] = set()
    for entity in entities:
        source = source_from_entity(entity)
        if source is None:
            continue
        if source.kind is TelegramSourceKind.CHAT and source.entity_id in linked_discussion_ids:
            continue
        if source.peer_id in seen_peer_ids:
            continue
        seen_peer_ids.add(source.peer_id)
        selected.append(SourceHandle(source=source, entity=entity))

    return sorted(selected, key=lambda handle: (handle.source.kind, handle.source.title.casefold()))


def apply_source_filters(
    handles: Iterable[SourceHandle],
    *,
    include_peer_ids: frozenset[int] = frozenset(),
    exclude_peer_ids: frozenset[int] = frozenset(),
    include_usernames: frozenset[str] = frozenset(),
    exclude_usernames: frozenset[str] = frozenset(),
) -> list[SourceHandle]:
    """Apply explicit technical allow/deny lists; empty includes mean all."""

    normalized_includes = {item.casefold().removeprefix("@") for item in include_usernames}
    normalized_excludes = {item.casefold().removeprefix("@") for item in exclude_usernames}
    selected: list[SourceHandle] = []
    for handle in handles:
        source = handle.source
        username = source.username.casefold() if source.username else None
        explicitly_included = source.peer_id in include_peer_ids or (
            username is not None and username in normalized_includes
        )
        include_all = not include_peer_ids and not normalized_includes
        explicitly_excluded = source.peer_id in exclude_peer_ids or (
            username is not None and username in normalized_excludes
        )
        if (include_all or explicitly_included) and not explicitly_excluded:
            selected.append(handle)
    return selected


class TelegramSourceDiscovery:
    """Discovers subscribed sources without following channel comments.

    Telethon's dialog list is the sole source list.  The adapter never asks for
    comment messages and never adds a channel's ``linked_chat_id`` to that list.
    A small metadata request is used only to remove linked discussion groups
    that are already present as dialogs.
    """

    def __init__(
        self,
        client: Any,
        *,
        exclude_linked_discussions: bool = True,
        include_peer_ids: frozenset[int] = frozenset(),
        exclude_peer_ids: frozenset[int] = frozenset(),
        include_usernames: frozenset[str] = frozenset(),
        exclude_usernames: frozenset[str] = frozenset(),
    ) -> None:
        self._client = client
        self._exclude_linked_discussions = exclude_linked_discussions
        self._include_peer_ids = include_peer_ids
        self._exclude_peer_ids = exclude_peer_ids
        self._include_usernames = include_usernames
        self._exclude_usernames = exclude_usernames

    async def discover(self) -> list[SourceHandle]:
        entities: list[Any] = []
        async for dialog in self._client.iter_dialogs(ignore_migrated=True):
            entity = getattr(dialog, "entity", None)
            if entity is not None and classify_entity(entity) is not None:
                entities.append(entity)

        linked_ids: set[int] = set()
        if self._exclude_linked_discussions:
            linked_ids = await self._find_linked_discussion_ids(entities)

        handles = apply_source_filters(
            select_source_handles(entities, linked_discussion_ids=linked_ids),
            include_peer_ids=self._include_peer_ids,
            exclude_peer_ids=self._exclude_peer_ids,
            include_usernames=self._include_usernames,
            exclude_usernames=self._exclude_usernames,
        )
        logger.info(
            "telegram_source_discovery_completed",
            extra={
                "telegram_source_count": len(handles),
                "telegram_linked_discussions_excluded": len(linked_ids),
            },
        )
        return handles

    async def _find_linked_discussion_ids(self, entities: list[Any]) -> set[int]:
        linked_ids: set[int] = set()
        for entity in entities:
            # ``has_link`` is the inexpensive Channel flag indicating a linked
            # broadcast/discussion peer.  Basic groups cannot be linked peers.
            if not isinstance(entity, types.Channel):
                continue
            if not bool(getattr(entity, "has_link", False)):
                continue

            try:
                result = await self._client(
                    functions.channels.GetFullChannelRequest(channel=entity)
                )
                partner_id = getattr(result.full_chat, "linked_chat_id", None)
                if partner_id is not None:
                    if bool(getattr(entity, "megagroup", False)):
                        linked_ids.add(int(entity.id))
                    else:
                        linked_ids.add(int(partner_id))
            except Exception as exc:  # Telethon exposes several RPC subclasses.
                logger.warning(
                    "telegram_linked_discussion_lookup_failed",
                    extra={
                        "telegram_entity_id": int(entity.id),
                        "error_type": type(exc).__name__,
                    },
                )
                # Fail closed for an ambiguous group so reader comments cannot
                # enter the corpus when Telegram refuses the metadata request.
                if bool(getattr(entity, "megagroup", False)):
                    linked_ids.add(int(entity.id))

        return linked_ids
