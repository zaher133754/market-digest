"""Sequential, stateless chunk/reduce analysis for Telegram reports."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Sequence
from datetime import datetime

from market_digest.ai.client import StructuredAIClient
from market_digest.config import Settings
from market_digest.errors import LunaContractError

from .models import (
    ChannelDigestReport,
    MarketMoodReport,
    ReportInput,
    ReportKind,
    ReportMessage,
    ReportPoint,
    StrictModel,
)
from .prompts import (
    DIGEST_CHUNK_PROMPT,
    DIGEST_REDUCE_PROMPT,
    SENTIMENT_CHUNK_PROMPT,
    SENTIMENT_REDUCE_PROMPT,
)


class DigestAnalysisResult(StrictModel):
    window_start: datetime
    window_end: datetime
    report: ChannelDigestReport


class MoodAnalysisResult(StrictModel):
    window_start: datetime
    window_end: datetime
    report: MarketMoodReport


type AnalysisEnvelope = DigestAnalysisResult | MoodAnalysisResult
type Report = ChannelDigestReport | MarketMoodReport


def iter_report_points(report: Report) -> Iterable[ReportPoint]:
    if isinstance(report, ChannelDigestReport):
        for field in (
            report.overview,
            report.key_events,
            report.author_views,
            report.instruments_and_macro,
            report.declared_ideas,
            report.disagreements,
            report.watchlist,
        ):
            yield from field
        return
    for field in (
        report.positive,
        report.negative,
        report.themes,
        report.expectations,
        report.disagreements,
        report.extremes,
    ):
        yield from field


class StatelessReportPipeline:
    def __init__(self, settings: Settings, client: StructuredAIClient) -> None:
        self._settings = settings
        self._client = client

    async def analyze(self, data: ReportInput) -> Report:
        if not data.messages:
            raise ValueError("Cannot analyze a report with no messages")

        batches = _pack_messages(data.messages, self._settings.openai_batch_char_budget)
        partials: list[AnalysisEnvelope] = []
        prefix = data.kind.value
        schema = _schema_for(data.kind)

        for index, batch in enumerate(batches, start=1):
            payload = _payload(
                data,
                messages=[message.model_dump(mode="json") for message in batch],
            )
            parsed = await self._client.parse(
                operation=f"{prefix}-chunk-{index}",
                system_prompt=_chunk_prompt(data.kind),
                payload=payload,
                schema=schema,
            )
            envelope = self._validate_envelope(parsed, data, schema)
            self._validate_evidence(envelope.report, {item.message_ref for item in batch})
            partials.append(envelope)

        return await self._reduce(data, partials, schema)

    async def _reduce(
        self,
        data: ReportInput,
        partials: list[AnalysisEnvelope],
        schema: type[DigestAnalysisResult] | type[MoodAnalysisResult],
    ) -> Report:
        current = partials
        budget = self._settings.openai_batch_char_budget
        for level in range(1, self._settings.openai_reduce_max_levels + 1):
            groups = _pack_models(current, budget)
            if len(groups) >= len(current) and len(current) > 1:
                groups = [current[index : index + 2] for index in range(0, len(current), 2)]

            next_level: list[AnalysisEnvelope] = []
            for group_index, group in enumerate(groups, start=1):
                group_refs = {
                    ref
                    for item in group
                    for point in iter_report_points(item.report)
                    for ref in point.evidence_refs
                }
                if level == 1 and len(groups) == 1:
                    operation = f"{data.kind.value}-reduce"
                else:
                    operation = f"{data.kind.value}-reduce-{level}-{group_index}"
                payload = _payload(
                    data,
                    message_catalog=[
                        {
                            "message_ref": message.message_ref,
                            "source_title": message.source_title,
                            "author_display_name": message.author_display_name,
                            "published_at": message.published_at.isoformat(),
                            "permalink": message.permalink,
                        }
                        for message in data.messages
                        if message.message_ref in group_refs
                    ],
                    partial_reports=[item.model_dump(mode="json") for item in group],
                )
                parsed = await self._client.parse(
                    operation=operation,
                    system_prompt=_reduce_prompt(data.kind),
                    payload=payload,
                    schema=schema,
                )
                envelope = self._validate_envelope(parsed, data, schema)
                self._validate_evidence(envelope.report, group_refs)
                next_level.append(envelope)

            if len(next_level) == 1:
                return next_level[0].report
            current = next_level

        raise LunaContractError("Report reduction exceeded its safe level limit")

    @staticmethod
    def _validate_envelope(
        value: object,
        data: ReportInput,
        schema: type[DigestAnalysisResult] | type[MoodAnalysisResult],
    ) -> AnalysisEnvelope:
        if not isinstance(value, schema):
            raise LunaContractError("Codex returned the wrong report type")
        if value.window_start != data.window_start or value.window_end != data.window_end:
            raise LunaContractError("Codex changed the report time window")
        return value

    @staticmethod
    def _validate_evidence(report: Report, allowed_refs: set[str]) -> None:
        for point in iter_report_points(report):
            if not point.evidence_refs:
                raise LunaContractError("Report contains an empty evidence reference list")
            if any(ref not in allowed_refs for ref in point.evidence_refs):
                raise LunaContractError("Report contains an unknown evidence reference")


def _schema_for(
    kind: ReportKind,
) -> type[DigestAnalysisResult] | type[MoodAnalysisResult]:
    return DigestAnalysisResult if kind is ReportKind.DIGEST else MoodAnalysisResult


def _chunk_prompt(kind: ReportKind) -> str:
    return DIGEST_CHUNK_PROMPT if kind is ReportKind.DIGEST else SENTIMENT_CHUNK_PROMPT


def _reduce_prompt(kind: ReportKind) -> str:
    return DIGEST_REDUCE_PROMPT if kind is ReportKind.DIGEST else SENTIMENT_REDUCE_PROMPT


def _payload(data: ReportInput, **content: object) -> str:
    return json.dumps(
        {
            "report_kind": data.kind.value,
            "window_start": data.window_start.isoformat(),
            "window_end": data.window_end.isoformat(),
            **content,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _pack_messages(messages: Sequence[ReportMessage], budget: int) -> list[list[ReportMessage]]:
    return _pack_by_size(messages, budget, lambda item: item.model_dump_json())


def _pack_models(models: Sequence[AnalysisEnvelope], budget: int) -> list[list[AnalysisEnvelope]]:
    return _pack_by_size(models, budget, lambda item: item.model_dump_json())


def _pack_by_size[ItemT](
    items: Sequence[ItemT],
    budget: int,
    encode: Callable[[ItemT], str],
) -> list[list[ItemT]]:
    groups: list[list[ItemT]] = []
    current: list[ItemT] = []
    current_size = 0
    for item in items:
        size = len(encode(item))
        if current and current_size + size > budget:
            groups.append(current)
            current = []
            current_size = 0
        current.append(item)
        current_size += size
    if current:
        groups.append(current)
    return groups
