"""Four-pass, fail-closed market analysis orchestration."""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from market_digest.ai.client import StructuredAIClient
from market_digest.ai.prompts import (
    CLAIM_AUDIT_SYSTEM_PROMPT,
    CLUSTER_MERGE_SYSTEM_PROMPT,
    CLUSTER_SYSTEM_PROMPT,
    DIGEST_SYSTEM_PROMPT,
    EXTRACTION_SYSTEM_PROMPT,
    FINALIZE_SYSTEM_PROMPT,
    PROMPT_VERSION,
)
from market_digest.ai.schemas import (
    ClaimAudit,
    ClaimAuditBatch,
    ClaimCandidate,
    ComparisonResult,
    DigestDraft,
    DigestLine,
    ExtractionBatch,
    HistoricAuthorPosition,
    MessageExtraction,
    SourceMessage,
    VerificationResult,
)
from market_digest.config import Settings
from market_digest.errors import LunaContractError


@dataclass(frozen=True, slots=True)
class PipelineResult:
    extractions: list[MessageExtraction]
    comparison: ComparisonResult
    draft: DigestDraft
    claim_audits: list[ClaimAudit]
    final_digest: DigestDraft


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=_jsonable)


def _pack_records(records: Sequence[Any], budget: int) -> list[list[Any]]:
    """Pack complete records without truncating or inspecting their meaning."""

    batches: list[list[Any]] = []
    current: list[Any] = []
    current_size = 2
    for record in records:
        size = len(_encode(_jsonable(record))) + 1
        if size > budget:
            raise LunaContractError(
                "One complete structured record exceeds the configured Luna input "
                "budget; it was not truncated"
            )
        if current and current_size + size > budget:
            batches.append(current)
            current = []
            current_size = 2
        current.append(record)
        current_size += size
    if current:
        batches.append(current)
    return batches


def iter_digest_lines(digest: DigestDraft) -> Iterable[DigestLine]:
    yield from digest.overview
    for topic in digest.topics:
        yield topic.heading
        yield topic.common_conclusion
        yield from topic.factual_points
        yield from topic.author_views
        yield from topic.scenarios_and_risks
    if digest.participant_sentiment is not None:
        sentiment = digest.participant_sentiment
        yield sentiment.overview
        yield from sentiment.bullish_drivers
        yield from sentiment.bearish_drivers
        yield from sentiment.neutral_or_uncertain_drivers
    yield from digest.declared_trades
    yield from digest.closing_risks


class LunaMarketPipeline:
    def __init__(self, settings: Settings, luna: StructuredAIClient) -> None:
        self._settings = settings
        self._luna = luna
        # Leave room for prompts and envelope keys. This never truncates data.
        self._record_budget = max(10_000, settings.openai_batch_char_budget - 8_000)

    async def run(
        self,
        *,
        messages: Sequence[SourceMessage],
        historic_positions: Sequence[HistoricAuthorPosition],
        window_start: datetime,
        window_end: datetime,
        historic_evidence_messages: Sequence[SourceMessage] = (),
        report_kind: str = "channel_digest",
    ) -> PipelineResult:
        if not messages:
            raise LunaContractError("The semantic pipeline requires at least one message")
        self._validate_source_messages(messages)

        extractions = await self.extract(messages)
        comparison = await self.compare(messages, extractions, historic_positions)
        draft = await self.draft(comparison, window_start, window_end, report_kind=report_kind)
        audits, final_digest = await self.verify(
            draft,
            [*messages, *historic_evidence_messages],
        )
        return PipelineResult(
            extractions=extractions,
            comparison=comparison,
            draft=draft,
            claim_audits=audits,
            final_digest=final_digest,
        )

    async def extract(self, messages: Sequence[SourceMessage]) -> list[MessageExtraction]:
        output: list[MessageExtraction] = []
        for index, batch in enumerate(_pack_records(messages, self._record_budget), start=1):
            payload = _encode(
                {
                    "prompt_version": PROMPT_VERSION,
                    "batch_index": index,
                    "messages": [_jsonable(item) for item in batch],
                }
            )
            parsed = await self._luna.parse(
                operation=f"extraction:{index}",
                system_prompt=EXTRACTION_SYSTEM_PROMPT,
                payload=payload,
                schema=ExtractionBatch,
            )
            expected = {item.message_ref: item for item in batch}
            actual_refs = [item.message_ref for item in parsed.items]
            if len(actual_refs) != len(set(actual_refs)) or set(actual_refs) != set(expected):
                raise LunaContractError(
                    "Extraction output did not cover every message exactly once"
                )
            for item in parsed.items:
                source = expected[item.message_ref]
                if item.message_id != source.message_id or item.source_id != source.source_id:
                    raise LunaContractError("Luna changed a Telegram identity field")
            output.extend(parsed.items)
        if len(output) != len(messages):
            raise LunaContractError("Extraction completeness invariant failed")
        return output

    async def compare(
        self,
        messages: Sequence[SourceMessage],
        extractions: Sequence[MessageExtraction],
        historic_positions: Sequence[HistoricAuthorPosition],
    ) -> ComparisonResult:
        extraction_by_ref = {item.message_ref: item for item in extractions}
        history_by_author: dict[str, list[HistoricAuthorPosition]] = {}
        for position in historic_positions:
            history_by_author.setdefault(position.author_ref, []).append(position)

        records: list[dict[str, Any]] = []
        for message in messages:
            extraction = extraction_by_ref.get(message.message_ref)
            if extraction is None:
                raise LunaContractError(f"Missing extraction for {message.message_ref}")
            records.append(
                {
                    "message": _jsonable(message),
                    "extraction": _jsonable(extraction),
                    "author_history": [
                        _jsonable(item) for item in history_by_author.get(message.author_ref, [])
                    ],
                }
            )

        partials: list[ComparisonResult] = []
        for index, batch in enumerate(_pack_records(records, self._record_budget), start=1):
            expected_refs = {record["message"]["message_ref"] for record in batch}
            history_refs = {
                ref
                for record in batch
                for history in record["author_history"]
                for ref in history["evidence_refs"]
            }
            parsed = await self._luna.parse(
                operation=f"comparison:{index}",
                system_prompt=CLUSTER_SYSTEM_PROMPT,
                payload=_encode(
                    {
                        "prompt_version": PROMPT_VERSION,
                        "batch_index": index,
                        "records": batch,
                    }
                ),
                schema=ComparisonResult,
            )
            self._validate_comparison(parsed, expected_refs, expected_refs | history_refs)
            partials.append(parsed)

        comparison = await self._merge_comparisons(partials)
        all_current_refs = {item.message_ref for item in messages}
        all_history_refs = {ref for item in historic_positions for ref in item.evidence_refs}
        self._validate_comparison(
            comparison,
            all_current_refs,
            all_current_refs | all_history_refs,
        )
        return comparison

    async def _merge_comparisons(self, partials: Sequence[ComparisonResult]) -> ComparisonResult:
        if not partials:
            return ComparisonResult(clusters=[], author_position_updates=[], excluded_messages=[])
        current = list(partials)
        level = 0
        merge_budget = min(750_000, max(self._record_budget, self._record_budget * 3))
        while len(current) > 1:
            level += 1
            if level > self._settings.openai_reduce_max_levels:
                raise LunaContractError("Hierarchical Luna merge exceeded its safe level limit")
            groups = _pack_records(current, merge_budget)
            if len(groups) >= len(current):
                raise LunaContractError(
                    "Partial comparison results cannot be merged without truncation"
                )
            next_level: list[ComparisonResult] = []
            for group_index, group in enumerate(groups, start=1):
                parsed = await self._luna.parse(
                    operation=f"comparison_merge:{level}:{group_index}",
                    system_prompt=CLUSTER_MERGE_SYSTEM_PROMPT,
                    payload=_encode(
                        {
                            "prompt_version": PROMPT_VERSION,
                            "merge_level": level,
                            "partial_results": [_jsonable(item) for item in group],
                        }
                    ),
                    schema=ComparisonResult,
                )
                expected_refs = self._comparison_current_refs(group)
                allowed_refs = self._comparison_all_evidence_refs(group)
                self._validate_comparison(parsed, expected_refs, allowed_refs)
                next_level.append(parsed)
            current = next_level
        return current[0]

    async def draft(
        self,
        comparison: ComparisonResult,
        window_start: datetime,
        window_end: datetime,
        *,
        report_kind: str = "channel_digest",
    ) -> DigestDraft:
        if report_kind not in {"channel_digest", "market_sentiment"}:
            raise LunaContractError(f"Unsupported report kind: {report_kind}")
        draft = await self._luna.parse(
            operation="digest_draft",
            system_prompt=DIGEST_SYSTEM_PROMPT,
            payload=_encode(
                {
                    "prompt_version": PROMPT_VERSION,
                    "window_start": window_start,
                    "window_end": window_end,
                    "report_kind": report_kind,
                    "comparison": _jsonable(comparison),
                }
            ),
            schema=DigestDraft,
        )
        if draft.window_start != window_start or draft.window_end != window_end:
            raise LunaContractError("Digest draft changed the requested time window")
        if report_kind == "channel_digest" and draft.participant_sentiment is not None:
            raise LunaContractError("Channel digest introduced chat participant sentiment")
        if report_kind == "market_sentiment" and draft.topics:
            raise LunaContractError("Market sentiment report introduced channel news topics")
        allowed_refs = self._comparison_all_evidence_refs([comparison])
        self._validate_digest_lines(draft, allowed_refs)
        return draft

    async def verify(
        self,
        draft: DigestDraft,
        source_messages: Sequence[SourceMessage],
    ) -> tuple[list[ClaimAudit], DigestDraft]:
        source_by_ref = {item.message_ref: item for item in source_messages}
        candidates = [
            ClaimCandidate(
                claim_id=line.claim_id,
                text=line.text,
                statement_kind=line.statement_kind,
                evidence_refs=line.evidence_refs,
            )
            for line in iter_digest_lines(draft)
        ]
        records: list[dict[str, Any]] = []
        for candidate in candidates:
            missing = set(candidate.evidence_refs) - set(source_by_ref)
            if missing:
                raise LunaContractError(
                    f"Draft cites unavailable raw evidence: {sorted(missing)!r}"
                )
            records.append(
                {
                    "claim": _jsonable(candidate),
                    "source_messages": [
                        _jsonable(source_by_ref[ref]) for ref in candidate.evidence_refs
                    ],
                }
            )

        audits: list[ClaimAudit] = []
        for index, batch in enumerate(_pack_records(records, self._record_budget), start=1):
            parsed = await self._luna.parse(
                operation=f"claim_audit:{index}",
                system_prompt=CLAIM_AUDIT_SYSTEM_PROMPT,
                payload=_encode(
                    {
                        "prompt_version": PROMPT_VERSION,
                        "batch_index": index,
                        "claims_with_sources": batch,
                    }
                ),
                schema=ClaimAuditBatch,
            )
            expected = {record["claim"]["claim_id"]: record["claim"] for record in batch}
            actual_ids = [item.claim_id for item in parsed.items]
            if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != set(expected):
                raise LunaContractError("Claim audit did not cover every claim exactly once")
            for audit in parsed.items:
                candidate = expected[audit.claim_id]
                if not set(audit.allowed_evidence_refs) <= set(candidate["evidence_refs"]):
                    raise LunaContractError("Claim audit introduced an evidence reference")
                if audit.supported and not audit.allowed_evidence_refs:
                    raise LunaContractError("A supported claim has no allowed evidence")
                if not audit.supported and audit.corrected_text is not None:
                    raise LunaContractError(
                        "Unsupported claim unexpectedly supplied corrected text"
                    )
            audits.extend(parsed.items)

        verified = await self._luna.parse(
            operation="digest_finalize",
            system_prompt=FINALIZE_SYSTEM_PROMPT,
            payload=_encode(
                {
                    "prompt_version": PROMPT_VERSION,
                    "draft": _jsonable(draft),
                    "claim_audits": [_jsonable(item) for item in audits],
                }
            ),
            schema=VerificationResult,
        )
        self._validate_final_digest(draft, audits, verified)
        return audits, verified.final_digest

    @staticmethod
    def _validate_source_messages(messages: Sequence[SourceMessage]) -> None:
        refs = [item.message_ref for item in messages]
        if len(refs) != len(set(refs)):
            raise LunaContractError("Duplicate message_ref supplied to Luna")
        if any(not item.text.strip() for item in messages):
            raise LunaContractError("A message without text reached the text pipeline")

    @staticmethod
    def _comparison_current_refs(results: Sequence[ComparisonResult]) -> set[str]:
        return {
            ref
            for result in results
            for ref in LunaMarketPipeline._comparison_cluster_refs(result)
            | {item.message_ref for item in result.excluded_messages}
        }

    @staticmethod
    def _comparison_cluster_refs(result: ComparisonResult) -> set[str]:
        return {ref for cluster in result.clusters for ref in cluster.source_refs}

    @staticmethod
    def _comparison_all_evidence_refs(results: Sequence[ComparisonResult]) -> set[str]:
        refs: set[str] = set()
        for result in results:
            refs |= LunaMarketPipeline._comparison_cluster_refs(result)
            for cluster in result.clusters:
                for fact in cluster.facts:
                    refs.update(fact.evidence_refs)
                for position in cluster.author_positions:
                    refs.update(position.evidence_refs)
                for disagreement in cluster.disagreements:
                    for position in disagreement.positions:
                        refs.update(position.evidence_refs)
                for change in cluster.position_changes:
                    refs.update(change.previous_evidence_refs)
                    refs.update(change.current_evidence_refs)
                for trade in cluster.declared_trades:
                    refs.update(trade.evidence_refs)
                for signal in cluster.sentiment_signals:
                    refs.update(signal.evidence_refs)
            for update in result.author_position_updates:
                refs.update(update.evidence_refs)
                refs.update(update.prior_evidence_refs)
        return refs

    @staticmethod
    def _validate_comparison(
        result: ComparisonResult,
        expected_current_refs: set[str],
        allowed_evidence_refs: set[str],
    ) -> None:
        cluster_ids = [item.cluster_id for item in result.clusters]
        if len(cluster_ids) != len(set(cluster_ids)):
            raise LunaContractError("Duplicate cluster_id in comparison output")
        clustered = LunaMarketPipeline._comparison_cluster_refs(result)
        excluded = {item.message_ref for item in result.excluded_messages}
        if len(excluded) != len(result.excluded_messages):
            raise LunaContractError("Duplicate excluded message in comparison output")
        if clustered & excluded:
            raise LunaContractError("A message is both clustered and excluded")
        covered = clustered | excluded
        if covered != expected_current_refs:
            raise LunaContractError("Comparison did not account for every input message")
        if not clustered <= expected_current_refs or not excluded <= expected_current_refs:
            raise LunaContractError("Comparison introduced an unknown current message")
        for cluster in result.clusters:
            if not cluster.source_refs:
                raise LunaContractError("A comparison cluster has no sources")
            if (
                cluster.primary_source_ref is not None
                and cluster.primary_source_ref not in cluster.source_refs
            ):
                raise LunaContractError("Primary source is not part of its cluster")
            for lineage in cluster.source_lineage:
                if lineage.source_ref not in cluster.source_refs:
                    raise LunaContractError("Source lineage points outside its cluster")
                if (
                    lineage.depends_on_source_ref is not None
                    and lineage.depends_on_source_ref not in cluster.source_refs
                ):
                    raise LunaContractError("Source lineage dependency points outside its cluster")
            for fact in cluster.facts:
                if not fact.evidence_refs or not set(fact.evidence_refs) <= allowed_evidence_refs:
                    raise LunaContractError("Cluster fact has invalid evidence")
            for position in cluster.author_positions:
                if (
                    not position.evidence_refs
                    or not set(position.evidence_refs) <= allowed_evidence_refs
                ):
                    raise LunaContractError("Author position has invalid evidence")
            for disagreement in cluster.disagreements:
                for position in disagreement.positions:
                    if (
                        not position.evidence_refs
                        or not set(position.evidence_refs) <= allowed_evidence_refs
                    ):
                        raise LunaContractError("Disagreement has invalid evidence")
            for change in cluster.position_changes:
                all_refs = set(change.previous_evidence_refs) | set(change.current_evidence_refs)
                if not all_refs <= allowed_evidence_refs:
                    raise LunaContractError("Position change has invalid evidence")
                if change.change_confirmed and (
                    not change.previous_evidence_refs or not change.current_evidence_refs
                ):
                    raise LunaContractError(
                        "Confirmed position change lacks both sides of evidence"
                    )
            for trade in cluster.declared_trades:
                if not trade.evidence_refs or not set(trade.evidence_refs) <= allowed_evidence_refs:
                    raise LunaContractError("Declared trade has invalid evidence")
            for signal in cluster.sentiment_signals:
                if (
                    not signal.evidence_refs
                    or not set(signal.evidence_refs) <= allowed_evidence_refs
                ):
                    raise LunaContractError("Sentiment signal has invalid evidence")
        for update in result.author_position_updates:
            refs = set(update.evidence_refs) | set(update.prior_evidence_refs)
            if not update.evidence_refs or not refs <= allowed_evidence_refs:
                raise LunaContractError("Author position update has invalid evidence")
            if update.explicit_change_from_history and not update.prior_evidence_refs:
                raise LunaContractError("Explicit historical change lacks prior evidence")

    @staticmethod
    def _validate_digest_lines(digest: DigestDraft, allowed_refs: set[str]) -> None:
        lines = list(iter_digest_lines(digest))
        claim_ids = [line.claim_id for line in lines]
        if len(claim_ids) != len(set(claim_ids)):
            raise LunaContractError("Digest contains duplicate claim_id")
        for line in lines:
            if not line.text.strip():
                raise LunaContractError("Digest contains an empty claim")
            if not line.evidence_refs or not set(line.evidence_refs) <= allowed_refs:
                raise LunaContractError("Digest claim has missing or unknown evidence")

    @staticmethod
    def _validate_final_digest(
        draft: DigestDraft,
        audits: Sequence[ClaimAudit],
        verified: VerificationResult,
    ) -> None:
        if not verified.approved:
            raise LunaContractError("Luna verification rejected the digest")
        if (
            verified.final_digest.window_start != draft.window_start
            or verified.final_digest.window_end != draft.window_end
        ):
            raise LunaContractError("Finalizer changed the digest time window")
        draft_by_id = {line.claim_id: line for line in iter_digest_lines(draft)}
        audit_by_id = {item.claim_id: item for item in audits}
        final_lines = list(iter_digest_lines(verified.final_digest))
        final_ids = [line.claim_id for line in final_lines]
        if len(final_ids) != len(set(final_ids)):
            raise LunaContractError("Final digest contains duplicate claim_id")
        for line in final_lines:
            audit = audit_by_id.get(line.claim_id)
            original = draft_by_id.get(line.claim_id)
            if audit is None or original is None or not audit.supported:
                raise LunaContractError("Final digest contains an unaudited claim")
            if line.statement_kind != original.statement_kind:
                raise LunaContractError(
                    "Finalizer changed a claim's fact/opinion/statement classification"
                )
            allowed_text = audit.corrected_text or original.text
            if line.text != allowed_text:
                raise LunaContractError("Finalizer changed audited claim text")
            if not line.evidence_refs or not set(line.evidence_refs) <= set(
                audit.allowed_evidence_refs
            ):
                raise LunaContractError("Finalizer changed audited evidence")
        expected_removed = set(draft_by_id) - set(final_ids)
        if set(verified.removed_claim_ids) != expected_removed:
            raise LunaContractError("Finalizer reported an inconsistent removed-claim set")
