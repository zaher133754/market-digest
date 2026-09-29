"""Telegram MTProto collector and owner-only Bot API delivery adapter."""

from .bot import (
    OwnerFailureNotifier,
    TelegramBotConfig,
    TelegramDigestBot,
    answer_in_chunks,
    send_in_chunks,
)
from .chunking import TELEGRAM_TEXT_LIMIT, split_telegram_text
from .collector import TelegramCollector, TelegramCollectorConfig
from .errors import (
    TelegramAuthorizationRequired,
    TelegramBotError,
    TelegramCollectorError,
    TelegramIntegrationError,
)
from .links import build_message_link, build_source_link
from .messages import message_to_dto
from .models import (
    CollectedTelegramMessage,
    DigestBotBackend,
    FailureNotice,
    FailureNotifier,
    MessagePersistence,
    SourceSnapshotPersistence,
    TelegramContentKind,
    TelegramSource,
    TelegramSourceKind,
)
from .session import MTProtoSessionConfig, MTProtoUserSession, create_mtproto_client
from .sources import SourceHandle, TelegramSourceDiscovery

__all__ = [
    "TELEGRAM_TEXT_LIMIT",
    "CollectedTelegramMessage",
    "DigestBotBackend",
    "FailureNotice",
    "FailureNotifier",
    "MTProtoSessionConfig",
    "MTProtoUserSession",
    "MessagePersistence",
    "OwnerFailureNotifier",
    "SourceHandle",
    "SourceSnapshotPersistence",
    "TelegramAuthorizationRequired",
    "TelegramBotConfig",
    "TelegramBotError",
    "TelegramCollector",
    "TelegramCollectorConfig",
    "TelegramCollectorError",
    "TelegramContentKind",
    "TelegramDigestBot",
    "TelegramIntegrationError",
    "TelegramSource",
    "TelegramSourceDiscovery",
    "TelegramSourceKind",
    "answer_in_chunks",
    "build_message_link",
    "build_source_link",
    "create_mtproto_client",
    "message_to_dto",
    "send_in_chunks",
    "split_telegram_text",
]
