from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from market_digest.config import Settings


def _values() -> dict[str, object]:
    return {
        "telegram_api_id": 123,
        "telegram_api_hash": "hash",
        "telegram_phone": "+79990000000",
        "bot_token": "token",
        "owner_telegram_id": 1,
    }


@pytest.mark.parametrize(
    ("field", "value"),
    [("openai_model", "gpt-5.6-sol"), ("openai_reasoning_effort", "medium")],
)
def test_settings_reject_any_luna_fallback(field: str, value: str) -> None:
    values = _values()
    values[field] = value
    with pytest.raises(ValidationError, match="forbidden"):
        Settings(**values)  # type: ignore[arg-type]


def test_settings_do_not_require_an_api_key() -> None:
    settings = Settings(**_values())  # type: ignore[arg-type]
    assert settings.openai_model == "gpt-5.6-luna"
    assert settings.openai_reasoning_effort == "high"
    assert settings.codex_executable == "codex"


def test_stateless_defaults() -> None:
    settings = Settings(**_values())  # type: ignore[arg-type]
    assert settings.posts_folder_name == "Посты"
    assert settings.chats_folder_name == "Чаты"
    assert (settings.digest_hour, settings.digest_minute) == (15, 0)
    assert (settings.sentiment_hour, settings.sentiment_minute) == (16, 0)
    assert settings.runtime_state_path == Path("/data/state/runtime.json")
    assert not hasattr(settings, "database_url")
