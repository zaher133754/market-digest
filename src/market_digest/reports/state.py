"""Atomic persistence of metadata-only runtime status."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from pydantic import Field, ValidationError, model_validator

from .models import RunTrigger, StrictModel


class RunStatus(StrEnum):
    NEVER = "never"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunRecord(StrictModel):
    status: RunStatus = RunStatus.NEVER
    trigger: RunTrigger | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, ge=0)
    source_count: int = Field(default=0, ge=0)
    message_count: int = Field(default=0, ge=0)
    error_code: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_timestamps(self) -> RunRecord:
        for value in (self.started_at, self.finished_at):
            if value is not None and (value.tzinfo is None or value.utcoffset() is None):
                raise ValueError("run timestamps must be timezone-aware")
        return self


class RuntimeState(StrictModel):
    version: int = 1
    digest: RunRecord = Field(default_factory=RunRecord)
    sentiment: RunRecord = Field(default_factory=RunRecord)


class JsonStateStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = asyncio.Lock()

    async def load(self) -> RuntimeState:
        async with self._lock:
            state = await asyncio.to_thread(self._load_sync)
        return _recover_interrupted(state)

    async def save(self, state: RuntimeState) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save_sync, state)

    def _load_sync(self) -> RuntimeState:
        if not self._path.is_file():
            return RuntimeState()
        try:
            return RuntimeState.model_validate_json(self._path.read_bytes())
        except (OSError, ValueError, ValidationError):
            return RuntimeState()

    def _save_sync(self, state: RuntimeState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._path.with_suffix(".tmp")
        payload = state.model_dump_json(indent=2).encode("utf-8")
        try:
            descriptor = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                0o600,
            )
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _recover_interrupted(state: RuntimeState) -> RuntimeState:
    now = datetime.now(UTC)

    def recover(record: RunRecord) -> RunRecord:
        if record.status is not RunStatus.RUNNING:
            return record
        duration = None
        if record.started_at is not None:
            duration = max(0.0, (now - record.started_at.astimezone(UTC)).total_seconds())
        return record.model_copy(
            update={
                "status": RunStatus.FAILED,
                "finished_at": now,
                "duration_seconds": duration,
                "error_code": "interrupted",
            }
        )

    return state.model_copy(
        update={
            "digest": recover(state.digest),
            "sentiment": recover(state.sentiment),
        }
    )
