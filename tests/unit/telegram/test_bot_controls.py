from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from market_digest.telegram import bot as bot_module


@dataclass
class FakeMessage:
    text: str
    answers: list[tuple[str, dict[str, Any]]] = field(default_factory=list)

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append((text, kwargs))


@dataclass
class FakeBackend:
    calls: list[str] = field(default_factory=list)

    async def request_digest(self) -> str:
        self.calls.append("request_digest")
        return "Дайджест сформирован"

    async def request_sentiment(self) -> str:
        self.calls.append("request_sentiment")
        return "Настроение: умеренно позитивное"

    async def status_text(self) -> str:
        self.calls.append("status_text")
        return "Все сервисы работают"


def test_main_menu_is_persistent_and_contains_exact_controls() -> None:
    keyboard = bot_module.build_main_menu_keyboard()

    assert keyboard.is_persistent is True
    assert [[button.text for button in row] for row in keyboard.keyboard] == [
        ["Дайджест сейчас", "Настроение сейчас"],
        ["Статус"],
    ]


@pytest.mark.asyncio
async def test_start_displays_persistent_main_menu() -> None:
    message = FakeMessage(text="/start")

    await bot_module.show_start_menu(message)  # type: ignore[arg-type]

    assert len(message.answers) == 1
    answer_text, kwargs = message.answers[0]
    assert answer_text.startswith("Личный рыночный дайджест запущен.")
    assert kwargs["reply_markup"].is_persistent is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("button_text", "expected_call", "expected_answer"),
    [
        ("Дайджест сейчас", "request_digest", "Дайджест сформирован"),
        (
            "Настроение сейчас",
            "request_sentiment",
            "Настроение: умеренно позитивное",
        ),
        ("Статус", "status_text", "Все сервисы работают"),
    ],
)
async def test_main_menu_button_returns_immediate_backend_text(
    button_text: str,
    expected_call: str,
    expected_answer: str,
) -> None:
    message = FakeMessage(text=button_text)
    backend = FakeBackend()

    await bot_module.handle_main_menu_action(  # type: ignore[arg-type]
        message,
        backend,
    )

    assert backend.calls == [expected_call]
    assert message.answers == [(expected_answer, {})]
