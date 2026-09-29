from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from market_digest.reports.models import RunTrigger
from market_digest.reports.state import (
    JsonStateStore,
    RunRecord,
    RunStatus,
    RuntimeState,
)


@pytest.mark.asyncio
async def test_state_round_trip_uses_atomic_sibling_file(tmp_path) -> None:
    state_path = tmp_path / "nested" / "runtime.json"
    store = JsonStateStore(state_path)
    state = RuntimeState(
        digest=RunRecord(
            status=RunStatus.SUCCEEDED,
            trigger=RunTrigger.MANUAL,
            started_at=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
            finished_at=datetime(2026, 9, 20, 10, 1, tzinfo=UTC),
            duration_seconds=60,
            source_count=3,
            message_count=45,
        )
    )

    await store.save(state)

    assert await store.load() == state
    assert not state_path.with_suffix(".tmp").exists()
    assert "Сообщение" not in state_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_corrupt_state_recovers_to_never(tmp_path) -> None:
    state_path = tmp_path / "runtime.json"
    state_path.write_text("{broken", encoding="utf-8")

    recovered = await JsonStateStore(state_path).load()

    assert recovered.digest.status is RunStatus.NEVER
    assert recovered.sentiment.status is RunStatus.NEVER


@pytest.mark.asyncio
async def test_interrupted_running_record_recovers_as_safe_failure(tmp_path) -> None:
    state_path = tmp_path / "runtime.json"
    store = JsonStateStore(state_path)
    await store.save(
        RuntimeState(
            sentiment=RunRecord(
                status=RunStatus.RUNNING,
                trigger=RunTrigger.SCHEDULED,
                started_at=datetime(2026, 9, 20, 16, 0, tzinfo=UTC),
                source_count=8,
                message_count=100,
            )
        )
    )

    recovered = await store.load()

    assert recovered.sentiment.status is RunStatus.FAILED
    assert recovered.sentiment.error_code == "interrupted"
    assert recovered.sentiment.finished_at is not None
    assert recovered.sentiment.trigger is RunTrigger.SCHEDULED
    assert recovered.sentiment.source_count == 8


def test_runtime_state_rejects_message_content() -> None:
    with pytest.raises(ValidationError):
        RuntimeState.model_validate(
            {
                "digest": {"status": "never", "message_text": "секрет"},
                "sentiment": {"status": "never"},
            }
        )
