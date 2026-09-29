"""MTProto user-session creation and explicit first-login flow."""

from __future__ import annotations

import asyncio
import getpass
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from .errors import TelegramAuthorizationRequired

SecretReader = Callable[[str], Awaitable[str]]


@dataclass(frozen=True, slots=True)
class MTProtoSessionConfig:
    api_id: int
    api_hash: str
    phone: str
    session_path: str

    def __post_init__(self) -> None:
        if self.api_id <= 0:
            raise ValueError("Telegram api_id must be positive")
        if not self.api_hash.strip():
            raise ValueError("Telegram api_hash cannot be empty")
        if not self.phone.strip():
            raise ValueError("Telegram phone cannot be empty")
        if not self.session_path.strip():
            raise ValueError("Telegram session_path cannot be empty")


def create_mtproto_client(config: MTProtoSessionConfig) -> TelegramClient:
    session_path = Path(config.session_path)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    return TelegramClient(str(session_path), config.api_id, config.api_hash)


class MTProtoUserSession:
    """Owns one Telethon user client and never falls back to a bot session."""

    def __init__(
        self,
        config: MTProtoSessionConfig,
        *,
        client: Any | None = None,
    ) -> None:
        self.config = config
        self.client = client if client is not None else create_mtproto_client(config)

    async def connect_authorized(self) -> Any:
        await self.client.connect()
        if not await self.client.is_user_authorized():
            raise TelegramAuthorizationRequired(
                "MTProto user session is not authorized. Run the interactive "
                "Telegram login command once with a TTY, then restart the service."
            )
        return self.client

    async def login_interactively(
        self,
        *,
        code_reader: SecretReader | None = None,
        password_reader: SecretReader | None = None,
    ) -> None:
        """Perform Telegram's code and optional 2FA flow without logging secrets."""

        await self.client.connect()
        if await self.client.is_user_authorized():
            return

        code_reader = code_reader or _input_reader
        password_reader = password_reader or _password_reader
        sent_code = await self.client.send_code_request(self.config.phone)
        code = (await code_reader("Telegram login code: ")).strip()
        if not code:
            raise TelegramAuthorizationRequired("Telegram login code cannot be empty")

        try:
            await self.client.sign_in(
                phone=self.config.phone,
                code=code,
                phone_code_hash=sent_code.phone_code_hash,
            )
        except SessionPasswordNeededError:
            password = await password_reader("Telegram 2FA password: ")
            if not password:
                raise TelegramAuthorizationRequired(
                    "Telegram 2FA password cannot be empty"
                ) from None
            await self.client.sign_in(password=password)

        if not await self.client.is_user_authorized():
            raise TelegramAuthorizationRequired(
                "Telegram did not authorize the MTProto user session"
            )

    async def disconnect(self) -> None:
        await self.client.disconnect()


async def _input_reader(prompt: str) -> str:
    return await asyncio.to_thread(input, prompt)


async def _password_reader(prompt: str) -> str:
    return await asyncio.to_thread(getpass.getpass, prompt)
