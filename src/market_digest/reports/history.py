"""Atomic persistent research history; raw Telegram corpora are never stored here."""

import asyncio
import os
from datetime import timedelta
from pathlib import Path

from pydantic import TypeAdapter

from .analysis_models import AnalysisSnapshot
from .models import ReportInput


class ResearchHistory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = asyncio.Lock()

    def _read(self) -> list[AnalysisSnapshot]:
        if not self.path.exists():
            return []
        # Corrupt history is an error, not a fabricated first-ever report.
        return TypeAdapter(list[AnalysisSnapshot]).validate_json(self.path.read_bytes())

    async def previous(self, data: ReportInput) -> AnalysisSnapshot | None:
        async with self._lock:
            items = await asyncio.to_thread(self._read)
        eligible = [
            s
            for s in items
            if s.kind == data.kind
            and data.window_start - timedelta(hours=6) <= s.window_end <= data.window_start
        ]
        return max(eligible, key=lambda s: s.window_end) if eligible else None

    async def save(self, snapshot: AnalysisSnapshot) -> None:
        async with self._lock:
            await asyncio.to_thread(self._save, snapshot)

    def _save(self, snapshot: AnalysisSnapshot) -> None:
        items = self._read()
        items = [
            s
            for s in items
            if not (s.kind == snapshot.kind and s.window_end == snapshot.window_end)
        ]
        items.append(snapshot)
        newest = max(s.window_end for s in items)
        items = [s for s in items if s.window_end >= newest - timedelta(days=35)]
        items.sort(key=lambda s: s.window_end)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        payload = TypeAdapter(list[AnalysisSnapshot]).dump_json(items)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.path)

    async def recent(self) -> list[AnalysisSnapshot]:
        async with self._lock:
            return (await asyncio.to_thread(self._read))[-10:][::-1]
