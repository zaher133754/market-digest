"""24/7 Telethon collection with bounded backfill and idempotent delivery."""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from telethon import events

from .errors import (
    TelegramAuthorizationRequired,
    TelegramCollectorError,
    TelegramIntegrationError,
)
from .messages import ensure_utc, message_to_dto
from .models import (
    FailureNotice,
    FailureNotifier,
    MessagePersistence,
    SourceSnapshotPersistence,
)
from .sources import SourceHandle, TelegramSourceDiscovery

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TelegramCollectorConfig:
    initial_backfill_hours: int = 24
    source_refresh_seconds: int = 900
    recent_idempotency_keys: int = 50_000

    def __post_init__(self) -> None:
        if not 1 <= self.initial_backfill_hours <= 48:
            raise ValueError("initial_backfill_hours must be between 1 and 48")
        if self.source_refresh_seconds < 60:
            raise ValueError("source_refresh_seconds must be at least 60")
        if self.recent_idempotency_keys < 1:
            raise ValueError("recent_idempotency_keys must be positive")


class TelegramCollector:
    """Collects all eligible text while leaving semantics to the AI pipeline."""

    def __init__(
        self,
        client: Any,
        persist_message: MessagePersistence,
        *,
        config: TelegramCollectorConfig | None = None,
        persist_sources: SourceSnapshotPersistence | None = None,
        notify_failure: FailureNotifier | None = None,
        discovery: TelegramSourceDiscovery | None = None,
    ) -> None:
        self._client = client
        self._persist_message = persist_message
        self._persist_sources = persist_sources
        self._notify_failure = notify_failure
        self._config = config or TelegramCollectorConfig()
        self._discovery = discovery or TelegramSourceDiscovery(client)
        self._sources: dict[int, SourceHandle] = {}
        self._recent: OrderedDict[str, None] = OrderedDict()
        self._inflight: set[str] = set()
        self._dedup_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()
        self._event_handler = self._on_new_message

    @property
    def sources(self) -> tuple[SourceHandle, ...]:
        return tuple(self._sources.values())

    async def run(self) -> None:
        """Connect, backfill, and collect until disconnected or stopped."""

        refresh_task: asyncio.Task[None] | None = None
        handler_added = False
        try:
            await self._client.connect()
            if not await self._client.is_user_authorized():
                raise TelegramAuthorizationRequired(
                    "MTProto user session is not authorized; complete the "
                    "interactive login before starting the collector"
                )

            handles = await self.refresh_sources()
            self._client.add_event_handler(self._event_handler, events.NewMessage())
            handler_added = True
            # Installing the live handler before backfill deliberately permits
            # overlap.  DTO keys plus the persistence unique constraint make it
            # safe and close the otherwise unavoidable hand-off race.
            await self.collect_backfill(handles)
            refresh_task = asyncio.create_task(self._refresh_loop(), name="telegram-source-refresh")

            logger.info("telegram_collector_started")
            await self._client.run_until_disconnected()
            if not self._stop_event.is_set():
                raise TelegramCollectorError("MTProto connection was disconnected")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._report_failure("Telegram collector stopped", exc)
            if isinstance(exc, TelegramIntegrationError):
                raise
            raise TelegramCollectorError("Telegram collector stopped") from exc
        finally:
            if refresh_task is not None:
                refresh_task.cancel()
                await asyncio.gather(refresh_task, return_exceptions=True)
            if handler_added:
                self._client.remove_event_handler(self._event_handler)
            if self._client.is_connected():
                await self._client.disconnect()
            logger.info("telegram_collector_stopped")

    async def stop(self) -> None:
        self._stop_event.set()
        if self._client.is_connected():
            await self._client.disconnect()

    async def refresh_sources(self) -> list[SourceHandle]:
        handles = await self._discovery.discover()
        new_map = {handle.source.peer_id: handle for handle in handles}
        previous_ids = set(self._sources)
        self._sources = new_map
        if self._persist_sources is not None:
            await self._persist_sources([handle.source for handle in handles])

        added_ids = set(new_map) - previous_ids
        if previous_ids and added_ids:
            await self.collect_backfill([new_map[peer_id] for peer_id in added_ids])
        return handles

    async def collect_backfill(
        self,
        handles: list[SourceHandle] | tuple[SourceHandle, ...] | None = None,
        *,
        now: datetime | None = None,
    ) -> None:
        boundary = ensure_utc(now or datetime.now(UTC))
        cutoff = boundary - timedelta(hours=self._config.initial_backfill_hours)
        selected = list(handles) if handles is not None else list(self._sources.values())

        for handle in selected:
            async for message in self._client.iter_messages(handle.entity):
                message_date = getattr(message, "date", None)
                if not isinstance(message_date, datetime):
                    continue
                message_date = ensure_utc(message_date)
                if message_date < cutoff:
                    break
                if message_date > boundary:
                    continue
                dto = message_to_dto(message, handle.source)
                if dto is not None:
                    await self._deliver(dto)

        logger.info(
            "telegram_backfill_completed",
            extra={
                "telegram_source_count": len(selected),
                "telegram_backfill_hours": self._config.initial_backfill_hours,
            },
        )

    async def _on_new_message(self, event: Any) -> None:
        peer_id = getattr(event, "chat_id", None)
        if not isinstance(peer_id, int):
            return
        handle = self._sources.get(peer_id)
        if handle is None:
            return
        dto = message_to_dto(event.message, handle.source)
        if dto is None:
            return
        try:
            await self._deliver(dto)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self._report_failure("A Telegram message was not persisted", exc)

    async def _deliver(self, message: Any) -> None:
        key = message.idempotency_key
        async with self._dedup_lock:
            if key in self._recent or key in self._inflight:
                return
            self._inflight.add(key)

        succeeded = False
        try:
            await self._persist_message(message)
            succeeded = True
        finally:
            async with self._dedup_lock:
                self._inflight.discard(key)
                if succeeded:
                    self._recent[key] = None
                    self._recent.move_to_end(key)
                    while len(self._recent) > self._config.recent_idempotency_keys:
                        self._recent.popitem(last=False)

    async def _refresh_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=self._config.source_refresh_seconds,
                )
            except TimeoutError:
                try:
                    await self.refresh_sources()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    await self._report_failure("Telegram source refresh failed", exc)

    async def _report_failure(self, summary: str, exc: Exception) -> None:
        logger.error(
            "telegram_collector_failure",
            extra={"summary": summary, "error_type": type(exc).__name__},
            exc_info=exc,
        )
        if self._notify_failure is None:
            return
        try:
            await self._notify_failure(
                FailureNotice(
                    component="telegram_collector",
                    summary=summary,
                    details=type(exc).__name__,
                )
            )
        except Exception as notification_exc:
            logger.error(
                "telegram_failure_notification_failed",
                extra={"error_type": type(notification_exc).__name__},
                exc_info=notification_exc,
            )
