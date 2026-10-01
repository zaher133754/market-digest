"""Owner-only aiogram 3 delivery bot and failure notifier."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BotCommand,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    TelegramObject,
)

from .chunking import split_telegram_text
from .errors import TelegramBotError
from .models import DigestBotBackend, FailureNotice

logger = logging.getLogger(__name__)

DIGEST_NOW_BUTTON = "Дайджест сейчас"
MARKET_SENTIMENT_BUTTON = "Настроение сейчас"
STATUS_BUTTON = "Статус"
MAIN_MENU_BUTTONS = frozenset(
    {
        DIGEST_NOW_BUTTON,
        MARKET_SENTIMENT_BUTTON,
        STATUS_BUTTON,
    }
)


@dataclass(frozen=True, slots=True)
class TelegramBotConfig:
    token: str
    owner_telegram_id: int

    def __post_init__(self) -> None:
        if not self.token.strip():
            raise ValueError("Telegram bot token cannot be empty")
        if self.owner_telegram_id <= 0:
            raise ValueError("owner_telegram_id must be positive")


def is_owner(message: Any, owner_telegram_id: int) -> bool:
    user = getattr(message, "from_user", None)
    return getattr(user, "id", None) == owner_telegram_id


def build_main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=DIGEST_NOW_BUTTON),
                KeyboardButton(text=MARKET_SENTIMENT_BUTTON),
            ],
            [KeyboardButton(text=STATUS_BUTTON)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


async def show_start_menu(message: Message) -> None:
    await message.answer(
        "Личный рыночный дайджест запущен.\n\n"
        "Кнопки запускают отчёты за последние 24 часа.\n"
        "/status — состояние сервисов и расписания",
        reply_markup=build_main_menu_keyboard(),
    )


async def handle_main_menu_action(
    message: Message,
    backend: DigestBotBackend,
) -> None:
    providers: dict[str, Callable[[], Awaitable[str]]] = {
        DIGEST_NOW_BUTTON: backend.request_digest,
        MARKET_SENTIMENT_BUTTON: backend.request_sentiment,
        STATUS_BUTTON: backend.status_text,
    }
    if message.text not in providers:
        return
    await answer_in_chunks(message, await providers[message.text]())


class OwnerOnlyMiddleware(BaseMiddleware):
    """Reject every bot message that did not originate from the configured owner."""

    def __init__(self, owner_telegram_id: int) -> None:
        self._owner_telegram_id = owner_telegram_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not is_owner(event, self._owner_telegram_id):
            logger.warning(
                "telegram_bot_unauthorized_access",
                extra={"telegram_user_id": getattr(getattr(event, "from_user", None), "id", None)},
            )
            if isinstance(event, Message):
                await event.answer("Доступ запрещён.")
            return None
        return await handler(event, data)


async def answer_in_chunks(message: Message, text: str) -> None:
    chunks = split_telegram_text(text)
    if not chunks:
        chunks = ["Нет данных."]
    for chunk in chunks:
        await message.answer(chunk)


async def send_in_chunks(bot: Bot, chat_id: int, text: str) -> list[int]:
    chunks = split_telegram_text(text)
    if not chunks:
        chunks = ["Нет данных."]
    message_ids: list[int] = []
    for chunk in chunks:
        sent = await bot.send_message(chat_id=chat_id, text=chunk)
        message_id = getattr(sent, "message_id", None)
        if isinstance(message_id, int):
            message_ids.append(message_id)
    return message_ids


class TelegramDigestBot:
    """Thin transport over application-provided status/digest/source use-cases."""

    def __init__(
        self,
        config: TelegramBotConfig,
        backend: DigestBotBackend,
        *,
        bot: Bot | None = None,
        dispatcher: Dispatcher | None = None,
    ) -> None:
        self.config = config
        self.backend = backend
        self.bot = bot or Bot(token=config.token)
        self.dispatcher = dispatcher or Dispatcher()
        self.router = Router(name="market-digest-owner-bot")
        self.router.message.middleware(OwnerOnlyMiddleware(config.owner_telegram_id))
        self._register_handlers()
        self.dispatcher.include_router(self.router)

    def _register_handlers(self) -> None:
        @self.router.message(CommandStart())
        async def start_handler(message: Message) -> None:
            await show_start_menu(message)

        @self.router.message(Command("status"))
        async def status_handler(message: Message) -> None:
            await self._answer_from_backend(message, self.backend.status_text)

        @self.router.message(Command("history"))
        async def history_handler(message: Message) -> None:
            await self._answer_from_backend(message, self.backend.history_text)

        @self.router.message(Command("sources"))
        async def sources_handler(message: Message) -> None:
            await self._answer_from_backend(message, self.backend.sources_text)

        @self.router.message(F.text.in_(MAIN_MENU_BUTTONS))
        async def main_menu_handler(message: Message) -> None:
            try:
                await handle_main_menu_action(message, self.backend)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "telegram_bot_control_failed",
                    extra={"error_type": type(exc).__name__},
                    exc_info=exc,
                )
                await answer_in_chunks(
                    message,
                    "Действие временно не выполнено. Ошибка уже зафиксирована.",
                )

    async def _answer_from_backend(
        self,
        message: Message,
        provider: Callable[[], Awaitable[str | None]],
        *,
        empty_text: str = "Нет данных.",
    ) -> None:
        try:
            text = await provider()
            await answer_in_chunks(message, text if text else empty_text)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "telegram_bot_command_failed",
                extra={"error_type": type(exc).__name__},
                exc_info=exc,
            )
            await answer_in_chunks(
                message,
                "Команда временно не выполнена. Ошибка уже зафиксирована.",
            )

    async def run(self) -> None:
        try:
            await self.bot.set_my_commands(
                [
                    BotCommand(command="start", description="Справка"),
                    BotCommand(command="status", description="Состояние системы"),
                    BotCommand(command="history", description="История аналитических записок"),
                    BotCommand(command="sources", description="Все источники последней записки"),
                ]
            )
            logger.info("telegram_bot_polling_started")
            await self.dispatcher.start_polling(
                self.bot,
                allowed_updates=self.dispatcher.resolve_used_update_types(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "telegram_bot_polling_failed",
                extra={"error_type": type(exc).__name__},
                exc_info=exc,
            )
            raise TelegramBotError("Telegram bot polling failed") from exc
        finally:
            await self.bot.session.close()
            logger.info("telegram_bot_polling_stopped")

    async def notify_failure(self, notice: FailureNotice) -> None:
        """Send a safe operational alert to the owner, split if necessary."""

        lines = [
            "⚠️ Сбой рыночного дайджеста",
            f"Компонент: {notice.component}",
            f"Время UTC: {notice.occurred_at.isoformat()}",
            f"Причина: {notice.summary}",
        ]
        if notice.details:
            lines.append(f"Детали: {notice.details}")
        await send_in_chunks(
            self.bot,
            self.config.owner_telegram_id,
            "\n".join(lines),
        )

    async def publish_digest(self, text: str) -> list[int]:
        """Deliver a verified digest and return Telegram message identifiers."""

        return await send_in_chunks(
            self.bot,
            self.config.owner_telegram_id,
            text,
        )

    async def publish_failure(self, text: str) -> list[int]:
        """Try a fresh Bot session if the polling Bot cannot deliver an alert."""

        try:
            return await self.publish_digest(text)
        except Exception as exc:
            logger.warning(
                "telegram_failure_notice_primary_send_failed",
                extra={"error_type": type(exc).__name__},
            )
        async with Bot(token=self.config.token) as fallback:
            return await send_in_chunks(fallback, self.config.owner_telegram_id, text)


class OwnerFailureNotifier:
    """Callable protocol adapter suitable for collector and AI workers."""

    def __init__(self, telegram_bot: TelegramDigestBot) -> None:
        self._telegram_bot = telegram_bot

    async def __call__(self, notice: FailureNotice) -> None:
        await self._telegram_bot.notify_failure(notice)
