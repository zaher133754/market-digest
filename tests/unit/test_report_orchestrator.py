from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from market_digest.reports.models import (
    ChannelDigestReport,
    ReportInput,
    ReportKind,
    ReportMessage,
    ReportPoint,
    RunTrigger,
)
from market_digest.reports.service import HealthSnapshot, ReportOrchestrator
from market_digest.reports.state import JsonStateStore, RunStatus


def _input(kind: ReportKind) -> ReportInput:
    end = datetime.now(UTC)
    return ReportInput(
        kind=kind,
        window_start=end - timedelta(hours=24),
        window_end=end,
        source_count=1,
        messages=[
            ReportMessage(
                message_ref="telegram:-1001:1",
                source_title="Источник",
                source_username="source",
                published_at=end,
                author_display_name="Автор",
                text="Сообщение рынка",
                permalink="https://t.me/source/1",
            )
        ],
        warnings=[],
    )


class FakeLoader:
    def __init__(self) -> None:
        self.calls: list[ReportKind] = []
        self.called = asyncio.Event()

    async def load(self, kind: ReportKind, *, end: datetime) -> ReportInput:
        del end
        self.calls.append(kind)
        self.called.set()
        return _input(kind)


class BlockingPipeline:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.release = asyncio.Event()
        self.error = error

    async def analyze(self, data: ReportInput) -> ChannelDigestReport:
        await self.release.wait()
        if self.error:
            raise self.error
        ref = data.messages[0].message_ref
        return ChannelDigestReport(
            overview=[ReportPoint(text="Главное", evidence_refs=[ref])],
            key_events=[],
            author_views=[],
            instruments_and_macro=[],
            declared_ideas=[],
            disagreements=[],
            watchlist=[],
            conclusion="Вывод",
        )


async def _health() -> HealthSnapshot:
    return HealthSnapshot(
        mtproto_available=True,
        codex_available=True,
        posts_source_count=3,
        chats_source_count=8,
    )


@pytest.mark.asyncio
async def test_request_acknowledges_immediately_and_prevents_overlap(settings, tmp_path) -> None:
    loader = FakeLoader()
    pipeline = BlockingPipeline()
    published: list[str] = []
    orchestrator = ReportOrchestrator(
        settings,
        loader,
        pipeline,
        JsonStateStore(tmp_path / "state.json"),
        publish=published.append,
        health_probe=_health,
    )

    first = await orchestrator.request(ReportKind.DIGEST, RunTrigger.MANUAL)
    assert first.started is True
    assert "запущен" in first.message.lower()
    await asyncio.wait_for(loader.called.wait(), timeout=1)

    second = await orchestrator.request(ReportKind.SENTIMENT, RunTrigger.SCHEDULED)
    assert second.started is False
    assert "дайджест" in second.message.lower()
    assert loader.calls == [ReportKind.DIGEST]

    pipeline.release.set()
    for _ in range(100):
        if published:
            break
        await asyncio.sleep(0.01)
    assert len(published) == 1
    await orchestrator.shutdown()


@pytest.mark.asyncio
async def test_status_contains_health_counts_schedules_and_safe_state(settings, tmp_path) -> None:
    orchestrator = ReportOrchestrator(
        settings,
        FakeLoader(),
        BlockingPipeline(),
        JsonStateStore(tmp_path / "state.json"),
        publish=lambda text: None,
        health_probe=_health,
    )

    text = await orchestrator.status_text()

    assert "MTProto: доступен" in text
    assert "Codex: доступен" in text
    assert "Посты: 3" in text
    assert "Чаты: 8" in text
    assert "15:00" in text and "16:00" in text
    assert "Europe/Samara" in text
    assert "Текущая задача: нет" in text


@pytest.mark.asyncio
async def test_failure_records_only_safe_code_and_sends_no_partial_report(
    settings, tmp_path
) -> None:
    loader = FakeLoader()
    pipeline = BlockingPipeline(error=RuntimeError("RAW SECRET MESSAGE"))
    published: list[str] = []
    store = JsonStateStore(tmp_path / "state.json")
    orchestrator = ReportOrchestrator(
        settings,
        loader,
        pipeline,
        store,
        publish=published.append,
        health_probe=_health,
    )

    await orchestrator.request(ReportKind.DIGEST, RunTrigger.MANUAL)
    await loader.called.wait()
    pipeline.release.set()
    for _ in range(50):
        state = await store.load()
        if state.digest.status is RunStatus.FAILED:
            break
        await asyncio.sleep(0)

    state = await store.load()
    assert state.digest.status is RunStatus.FAILED
    assert state.digest.error_code == "internal_error"
    assert "RAW SECRET MESSAGE" not in (tmp_path / "state.json").read_text(encoding="utf-8")
    assert len(published) == 1
    assert "RAW SECRET MESSAGE" not in published[0]
    assert "internal_error" in published[0]
    await orchestrator.shutdown()
