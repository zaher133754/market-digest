"""Transport-neutral DTOs and ports for the Telegram integration.

Nothing in this module imports Telethon, aiogram, SQLAlchemy, or application ORM
models.  The application layer can therefore implement the protocols below
without making the Telegram adapter depend on persistence details.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol


class TelegramSourceKind(StrEnum):
    """A source's role in the product."""

    CHANNEL = "channel"
    CHAT = "chat"


class TelegramContentKind(StrEnum):
    """Text Telegram exposes without downloading or interpreting media."""

    TEXT = "text"
    CAPTION = "caption"


@dataclass(frozen=True, slots=True)
class TelegramSource:
    """Serializable view of a subscribed Telegram channel or chat."""

    entity_id: int
    peer_id: int
    kind: TelegramSourceKind
    title: str
    username: str | None = None
    link: str | None = None

    def __post_init__(self) -> None:
        if self.entity_id <= 0:
            raise ValueError("entity_id must be a positive Telegram entity id")
        if not self.title.strip():
            raise ValueError("source title cannot be empty")


@dataclass(frozen=True, slots=True)
class CollectedTelegramMessage:
    """A complete text/caption payload ready for idempotent persistence."""

    source: TelegramSource
    telegram_message_id: int
    published_at: datetime
    text: str
    content_kind: TelegramContentKind
    telegram_link: str | None
    author_id: int | None = None
    author_username: str | None = None
    author_display_name: str | None = None
    author_signature: str | None = None
    reply_to_message_id: int | None = None
    grouped_id: int | None = None
    edited_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.telegram_message_id <= 0:
            raise ValueError("telegram_message_id must be positive")
        if not self.text.strip():
            raise ValueError("message text/caption cannot be empty")
        if self.published_at.tzinfo is None:
            raise ValueError("published_at must be timezone-aware")
        if self.edited_at is not None and self.edited_at.tzinfo is None:
            raise ValueError("edited_at must be timezone-aware")

    @property
    def idempotency_key(self) -> str:
        """Stable key that the persistence adapter must constrain uniquely."""

        return f"telegram:{self.source.peer_id}:{self.telegram_message_id}"


@dataclass(frozen=True, slots=True)
class FailureNotice:
    """Safe, user-facing failure information.

    Do not put secrets, Telegram authorization codes, tokens, or raw request
    payloads in ``details``.
    """

    component: str
    summary: str
    details: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class MessagePersistence(Protocol):
    """Application use-case for durable, idempotent ingestion.

    Implementations must treat ``message.idempotency_key`` as a unique key and
    return ``True`` only when a new row was inserted.  Re-delivery is expected
    during the live/backfill hand-off and after service restarts.
    """

    async def __call__(self, message: CollectedTelegramMessage) -> bool: ...


class SourceSnapshotPersistence(Protocol):
    """Persists the complete currently eligible source snapshot."""

    async def __call__(self, sources: Sequence[TelegramSource]) -> None: ...


class FailureNotifier(Protocol):
    async def __call__(self, notice: FailureNotice) -> None: ...


class DigestBotBackend(Protocol):
    """Use-cases exposed by the owner-only delivery bot."""

    async def status_text(self) -> str: ...

    async def request_digest(self) -> str: ...

    async def request_sentiment(self) -> str: ...

    async def history_text(self) -> str: ...

    async def sources_text(self) -> str: ...
