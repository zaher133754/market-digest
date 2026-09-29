from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from market_digest.config import Settings


@pytest.fixture
def tmp_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    if os.name != "nt":
        return tmp_path_factory.mktemp("market-digest")
    # Inherit workspace ACLs: Windows mkdir(mode=0700) excludes the sandbox SID.
    path = Path.cwd() / ".test-analytics-files" / uuid.uuid4().hex
    path.mkdir(parents=True)
    return path


@pytest.fixture
def settings() -> Settings:
    return Settings(
        telegram_api_id=12345,
        telegram_api_hash="hash",
        telegram_phone="+79990000000",
        bot_token="bot-token",
        owner_telegram_id=42,
        openai_model="gpt-5.6-luna",
        openai_reasoning_effort="high",
        codex_home="/tmp/test-codex-home",
        codex_temp_root=Path.cwd() / ".test-codex-tmp",
        runtime_state_path=Path.cwd() / ".test-runtime-state.json",
    )
