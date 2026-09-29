from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from market_digest.telegram.bot import is_owner, send_in_chunks


def test_owner_check_is_fail_closed() -> None:
    assert is_owner(SimpleNamespace(from_user=SimpleNamespace(id=42)), 42)
    assert not is_owner(SimpleNamespace(from_user=SimpleNamespace(id=41)), 42)
    assert not is_owner(SimpleNamespace(from_user=None), 42)


@pytest.mark.asyncio
async def test_bot_delivery_splits_at_telegram_limit() -> None:
    calls: list[dict[str, Any]] = []

    class FakeBot:
        async def send_message(self, **kwargs: Any) -> SimpleNamespace:
            calls.append(kwargs)
            return SimpleNamespace(message_id=len(calls))

    text = "x" * 9000
    message_ids = await send_in_chunks(FakeBot(), 42, text)  # type: ignore[arg-type]

    assert message_ids == [1, 2, 3]
    assert "".join(call["text"] for call in calls) == text
    assert all(call["chat_id"] == 42 for call in calls)
    assert all(len(call["text"]) <= 4096 for call in calls)
