"""Load a transient, exact 24-hour Telegram corpus for one report."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from market_digest.reports.models import ReportInput, ReportKind, ReportMessage
from market_digest.services.windows import exact_24_hour_window

from .folders import TelegramFolderNotFound, TelegramFolderSelector
from .messages import ensure_utc, message_to_dto
from .models import TelegramSourceKind


class TelegramWindowLoader:
    """Read report messages directly from the configured native folder."""

    def __init__(
        self,
        client: Any,
        selector: TelegramFolderSelector,
        *,
        posts_folder_name: str,
        chats_folder_name: str,
    ) -> None:
        self._client = client
        self._selector = selector
        self._posts_folder_name = posts_folder_name
        self._chats_folder_name = chats_folder_name

    async def load(self, kind: ReportKind, *, end: datetime) -> ReportInput:
        if end.tzinfo is None or end.utcoffset() is None:
            raise ValueError("Report window end must be timezone-aware")

        if kind is ReportKind.DIGEST:
            folder_name = self._posts_folder_name
            source_kind = TelegramSourceKind.CHANNEL
        else:
            folder_name = self._chats_folder_name
            source_kind = TelegramSourceKind.CHAT

        snapshot = await self._selector.select(folder_name, source_kind)
        start, normalized_end = exact_24_hour_window(end)
        start_utc = start.astimezone(UTC)
        end_utc = normalized_end.astimezone(UTC)
        messages: list[ReportMessage] = []

        for handle in snapshot.sources:
            async for raw_message in self._client.iter_messages(
                handle.entity,
                # Telethon treats offset_date as exclusive. Telegram message
                # timestamps have second precision, so widen the request by
                # one second and enforce the exact inclusive end below.
                offset_date=end_utc + timedelta(seconds=1),
            ):
                raw_date = getattr(raw_message, "date", None)
                if not isinstance(raw_date, datetime):
                    continue
                published_at = ensure_utc(raw_date)
                if published_at > end_utc:
                    continue
                if published_at <= start_utc:
                    break

                dto = message_to_dto(raw_message, handle.source)
                if dto is None:
                    continue
                author = (
                    dto.author_display_name
                    or dto.author_signature
                    or dto.author_username
                    or dto.source.title
                )
                messages.append(
                    ReportMessage(
                        message_ref=dto.idempotency_key,
                        source_title=dto.source.title,
                        source_username=dto.source.username,
                        published_at=dto.published_at,
                        author_display_name=author,
                        text=dto.text,
                        permalink=dto.telegram_link,
                        source_kind="channel" if kind is ReportKind.DIGEST else "chat",
                    )
                )

        messages.sort(key=lambda item: (item.published_at, item.message_ref))
        warnings = [
            (f"В папке «{snapshot.resolved_title}» пропущен источник неподходящего типа: {title}")
            for title in snapshot.ignored_titles
        ]
        return ReportInput(
            kind=kind,
            window_start=start,
            window_end=normalized_end,
            source_count=len(snapshot.sources),
            messages=messages,
            warnings=warnings,
        )

    async def load_combined(self, kind: ReportKind, *, end: datetime) -> ReportInput:
        """Read both existing folders for cross-source analysis without changing collection."""
        parts: list[ReportInput] = []
        warnings: list[str] = []
        for source_kind in (ReportKind.DIGEST, ReportKind.SENTIMENT):
            try:
                parts.append(await self.load(source_kind, end=end))
            except TelegramFolderNotFound:
                folder = (
                    self._posts_folder_name
                    if source_kind is ReportKind.DIGEST
                    else self._chats_folder_name
                )
                warnings.append(
                    f"Папка «{folder}» отсутствует; сравнение каналов и чатов ограничено."
                )
        start, end = exact_24_hour_window(end)
        messages = {m.message_ref: m for part in parts for m in part.messages}
        return ReportInput(
            kind=kind,
            window_start=start,
            window_end=end,
            source_count=sum(p.source_count for p in parts),
            messages=sorted(messages.values(), key=lambda m: (m.published_at, m.message_ref)),
            warnings=warnings + [w for p in parts for w in p.warnings],
        )
