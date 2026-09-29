"""Resolve report sources from native Telegram dialog folders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from telethon import functions

from .models import TelegramSourceKind
from .sources import SourceHandle, source_from_entity


class TelegramFolderNotFound(RuntimeError):
    """Raised when an expected native Telegram folder is absent."""


@dataclass(frozen=True, slots=True)
class FolderSnapshot:
    requested_name: str
    resolved_title: str
    sources: tuple[SourceHandle, ...]
    ignored_titles: tuple[str, ...]


def normalize_folder_title(value: object) -> str:
    raw = getattr(value, "text", value)
    return str(raw or "").strip().casefold()


class TelegramFolderSelector:
    def __init__(self, client: Any) -> None:
        self._client = client

    async def select(
        self,
        folder_name: str,
        expected_kind: TelegramSourceKind,
    ) -> FolderSnapshot:
        response = await self._client(functions.messages.GetDialogFiltersRequest())
        filters = response if isinstance(response, list) else getattr(response, "filters", [])
        wanted = normalize_folder_title(folder_name)
        folder = next(
            (
                item
                for item in filters
                if normalize_folder_title(getattr(item, "title", "")) == wanted
            ),
            None,
        )
        if folder is None:
            raise TelegramFolderNotFound(f"Telegram folder not found: {folder_name}")

        excluded_ids: set[int] = set()
        for peer in getattr(folder, "exclude_peers", ()) or ():
            entity = await self._client.get_entity(peer)
            source = source_from_entity(entity)
            if source is not None:
                excluded_ids.add(source.peer_id)

        selected: dict[int, SourceHandle] = {}
        ignored: set[str] = set()
        peers = [
            *(getattr(folder, "pinned_peers", ()) or ()),
            *(getattr(folder, "include_peers", ()) or ()),
        ]
        for peer in peers:
            entity = await self._client.get_entity(peer)
            source = source_from_entity(entity)
            if source is None:
                continue
            if source.peer_id in excluded_ids:
                continue
            if source.kind is not expected_kind:
                ignored.add(source.title)
                continue
            selected.setdefault(source.peer_id, SourceHandle(source=source, entity=entity))

        sources = tuple(sorted(selected.values(), key=lambda item: item.source.title.casefold()))
        title = getattr(folder, "title", "")
        resolved_title = str(getattr(title, "text", None) or title).strip()
        return FolderSnapshot(
            requested_name=folder_name,
            resolved_title=resolved_title,
            sources=sources,
            ignored_titles=tuple(sorted(ignored, key=str.casefold)),
        )
