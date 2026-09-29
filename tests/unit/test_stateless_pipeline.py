from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any

import pytest

from market_digest.errors import LunaContractError
from market_digest.reports.models import (
    ChannelDigestReport,
    ReportInput,
    ReportKind,
    ReportMessage,
    ReportPoint,
)
from market_digest.reports.pipeline import (
    DigestAnalysisResult,
    StatelessReportPipeline,
    iter_report_points,
)


def _input() -> ReportInput:
    end = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)
    return ReportInput(
        kind=ReportKind.DIGEST,
        window_start=datetime(2026, 9, 19, 15, 0, tzinfo=UTC),
        window_end=end,
        source_count=1,
        messages=[
            ReportMessage(
                message_ref=f"telegram:-1001:{index}",
                source_title="Канал",
                source_username="channel",
                published_at=end,
                author_display_name="Автор",
                text=("Сообщение " + str(index) + " ") * 12,
                permalink=f"https://t.me/channel/{index}",
            )
            for index in (1, 2)
        ],
        warnings=[],
    )


class FakeAIClient:
    def __init__(
        self,
        *,
        unknown_ref: bool = False,
        change_window: bool = False,
        cross_group_ref: bool = False,
    ) -> None:
        self.operations: list[str] = []
        self.active_calls = 0
        self.max_concurrent_calls = 0
        self.unknown_ref = unknown_ref
        self.change_window = change_window
        self.cross_group_ref = cross_group_ref

    async def parse(
        self,
        *,
        operation: str,
        system_prompt: str,
        payload: str,
        schema: type[Any],
    ) -> Any:
        del system_prompt
        self.operations.append(operation)
        self.active_calls += 1
        self.max_concurrent_calls = max(self.max_concurrent_calls, self.active_calls)
        await asyncio.sleep(0)
        body = json.loads(payload)
        refs = _collect_refs(body)
        ref = "telegram:unknown:999" if self.unknown_ref else sorted(refs)[0]
        if self.cross_group_ref and operation == "digest-reduce-1-1":
            ref = "telegram:-1001:3"
        window_end = datetime.fromisoformat(body["window_end"])
        if self.change_window:
            window_end = datetime(2026, 9, 21, 15, 0, tzinfo=UTC)
        report = ChannelDigestReport(
            overview=[ReportPoint(text="Главное", evidence_refs=[ref])],
            key_events=[],
            author_views=[],
            instruments_and_macro=[],
            declared_ideas=[],
            disagreements=[],
            watchlist=[],
            conclusion="Вывод",
        )
        self.active_calls -= 1
        return DigestAnalysisResult(
            window_start=datetime.fromisoformat(body["window_start"]),
            window_end=window_end,
            report=report,
        )


def _collect_refs(value: Any) -> set[str]:
    refs: set[str] = set()
    if isinstance(value, dict):
        if isinstance(value.get("message_ref"), str):
            refs.add(value["message_ref"])
        for item in value.values():
            refs.update(_collect_refs(item))
    elif isinstance(value, list):
        for item in value:
            refs.update(_collect_refs(item))
    return refs


@pytest.mark.asyncio
async def test_chunk_calls_are_sequential_and_reduce_preserves_evidence(settings) -> None:
    client = FakeAIClient()
    tiny_settings = settings.model_copy(update={"openai_batch_char_budget": 250})
    pipeline = StatelessReportPipeline(tiny_settings, client)
    data = _input()

    result = await pipeline.analyze(data)

    assert client.max_concurrent_calls == 1
    assert client.operations == ["digest-chunk-1", "digest-chunk-2", "digest-reduce"]
    evidence = {ref for point in iter_report_points(result) for ref in point.evidence_refs}
    assert evidence <= {message.message_ref for message in data.messages}


@pytest.mark.asyncio
async def test_unknown_evidence_reference_is_rejected(settings) -> None:
    pipeline = StatelessReportPipeline(settings, FakeAIClient(unknown_ref=True))

    with pytest.raises(LunaContractError, match="unknown evidence reference"):
        await pipeline.analyze(_input())


@pytest.mark.asyncio
async def test_changed_window_metadata_is_rejected(settings) -> None:
    pipeline = StatelessReportPipeline(settings, FakeAIClient(change_window=True))

    with pytest.raises(LunaContractError, match="changed the report time window"):
        await pipeline.analyze(_input())


@pytest.mark.asyncio
async def test_empty_input_is_not_sent_to_codex(settings) -> None:
    data = _input().model_copy(update={"messages": []})
    client = FakeAIClient()
    pipeline = StatelessReportPipeline(settings, client)

    with pytest.raises(ValueError, match="no messages"):
        await pipeline.analyze(data)

    assert client.operations == []


@pytest.mark.asyncio
async def test_hierarchical_reduce_cannot_reference_another_group(settings) -> None:
    data = _input()
    data = data.model_copy(
        update={
            "messages": [
                *data.messages,
                data.messages[0].model_copy(
                    update={"message_ref": "telegram:-1001:3", "text": "Третий блок " * 12}
                ),
            ]
        }
    )
    pipeline = StatelessReportPipeline(
        settings.model_copy(update={"openai_batch_char_budget": 250}),
        FakeAIClient(cross_group_ref=True),
    )

    with pytest.raises(LunaContractError, match="unknown evidence reference"):
        await pipeline.analyze(data)
