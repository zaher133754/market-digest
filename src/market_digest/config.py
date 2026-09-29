"""Environment-backed configuration and startup invariants."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REQUIRED_OPENAI_MODEL = "gpt-5.6-luna"
REQUIRED_REASONING_EFFORT: Literal["high"] = "high"


class Settings(BaseSettings):
    """Runtime settings loaded from environment variables or a local .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    telegram_api_id: int = Field(gt=0)
    telegram_api_hash: SecretStr
    telegram_phone: str = Field(min_length=5)
    telegram_session_path: Path = Path("/data/telegram/user.session")

    bot_token: SecretStr
    owner_telegram_id: int = Field(gt=0)

    openai_model: str = REQUIRED_OPENAI_MODEL
    openai_reasoning_effort: str = REQUIRED_REASONING_EFFORT
    research_model: Literal["gpt-6-luna", "gpt-5.6-luna"] = "gpt-6-luna"
    analyst_model: Literal["gpt-6-astra"] = "gpt-6-astra"
    analyst_fallback_model: Literal["gpt-6-sol"] = "gpt-6-sol"
    research_history_path: Path = Path("/data/state/research.json")
    analyst_char_budget: int = Field(default=180_000, ge=20_000, le=750_000)
    codex_executable: str = Field(default="codex", min_length=1)
    codex_required_version: str = Field(default="0.153.0", min_length=1)
    codex_home: Path = Path("/data/codex")
    codex_temp_root: Path = Path("/tmp/market-digest")
    codex_timeout_seconds: float = Field(default=900.0, ge=10.0, le=3600.0)
    codex_max_output_bytes: int = Field(default=16_000_000, ge=1_000, le=64_000_000)
    openai_batch_char_budget: int = Field(default=220_000, ge=20_000, le=750_000)
    openai_reduce_max_levels: int = Field(default=8, ge=1, le=20)

    app_timezone: str = "Europe/Samara"
    digest_hour: int = Field(default=15, ge=0, le=23)
    digest_minute: int = Field(default=0, ge=0, le=59)
    sentiment_hour: int = Field(default=16, ge=0, le=23)
    sentiment_minute: int = Field(default=0, ge=0, le=59)
    posts_folder_name: str = Field(default="Посты", min_length=1)
    chats_folder_name: str = Field(default="Чаты", min_length=1)
    runtime_state_path: Path = Path("/data/state/runtime.json")
    log_level: str = "INFO"

    scheduler_misfire_grace_seconds: int = Field(default=3600, ge=60)
    collector_reconnect_delay_seconds: float = Field(default=5.0, ge=1.0, le=300.0)
    telegram_send_delay_seconds: float = Field(default=0.05, ge=0.0, le=2.0)

    @model_validator(mode="after")
    def enforce_hard_invariants(self) -> Self:
        if self.openai_model != REQUIRED_OPENAI_MODEL:
            raise ValueError(
                "OPENAI_MODEL must be exactly 'gpt-5.6-luna'; model fallback is forbidden"
            )
        if self.openai_reasoning_effort != REQUIRED_REASONING_EFFORT:
            raise ValueError(
                "OPENAI_REASONING_EFFORT must be exactly 'high'; lower effort is forbidden"
            )
        try:
            ZoneInfo(self.app_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown APP_TIMEZONE: {self.app_timezone}") from exc
        return self

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
