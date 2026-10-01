"""Luna extraction/research -> Astra synthesis -> grounded Luna audit."""

import json
import re
from collections import Counter
from typing import Any

import structlog

from market_digest.ai.routing import ModelRoute
from market_digest.config import Settings
from market_digest.errors import LunaContractError

from . import analysis_prompts as prompts
from .analysis_models import (
    AnalysisSnapshot,
    AnalyticalMemo,
    AnalyticalResult,
    Extraction,
    MemoAudit,
    ResearchCluster,
    ResearchMerge,
    SourceEntry,
    cluster_claims,
    memo_claims,
)
from .history import ResearchHistory
from .models import ReportInput

logger = structlog.get_logger(__name__)


def encode(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class AnalyticalPipeline:
    def __init__(
        self,
        settings: Settings,
        research: ModelRoute,
        analyst: ModelRoute,
        history: ResearchHistory,
    ) -> None:
        self.settings = settings
        self.research = research
        self.analyst = analyst
        self.history = history

    async def analyze(self, data: ReportInput) -> AnalyticalResult | None:
        if not data.messages:
            raise ValueError("Cannot analyze a report with no messages")
        refs = [m.message_ref for m in data.messages]
        if len(refs) != len(set(refs)):
            raise LunaContractError("Duplicate input message IDs")
        self.research.reset()
        self.analyst.reset()
        previous = await self.history.previous(data)
        budget = self.settings.openai_batch_char_budget
        catalog = [
            SourceEntry(
                message_ref=m.message_ref,
                source_title=m.source_title,
                source_kind=m.source_kind,
                author=m.author_display_name,
                permalink=m.permalink,
            )
            for m in data.messages
        ]
        clusters: list[ResearchCluster] = []
        parts = split_messages(data, budget)
        # Structured output grows with the number of dispositions, not only
        # with input characters. Bound both dimensions without dropping parts.
        for index, batch in enumerate(pack(parts, budget - 2000, max_items=120)):
            try:
                extracted = await self._extract_batch(batch, data, index)
                for n, cluster in enumerate(extracted):
                    cluster = cluster.model_copy(update={"cluster_id": f"e{index}-{n}"})
                    clusters.append(cluster)
            except LunaContractError as exc:
                raise LunaContractError(f"research-extract-{index}: {exc}") from exc
        if not clusters:
            return None

        # Research reduction ALWAYS runs, even a single batch, to deduplicate globally.
        clusters = await self._merge(clusters, data)
        if previous is None:
            clusters = [
                c.model_copy(
                    update={
                        "is_new": None,
                        "change_vs_previous_period": "Нет сопоставимого предыдущего периода",
                    }
                )
                for c in clusters
            ]
        else:
            comparison_payload = {
                "clusters": [c.model_dump(mode="json") for c in clusters],
                "previous": previous_research(previous),
            }
            self._ensure_budget(comparison_payload, self.settings.analyst_char_budget)
            compared = await self.research.parse(
                operation="research-compare-periods",
                system_prompt=prompts.MERGE
                + "\nСравни темы с previous. is_new и change_vs_previous_period требуют "
                "явной опоры в данных двух периодов; не путай отсутствие темы с её исчезновением.",
                payload=encode(comparison_payload),
                schema=ResearchMerge,
            )
            clusters = validate_merge(compared, clusters, data, "compared")

        research_payload = {
            "report_kind": data.kind.value,
            "window_start": data.window_start.isoformat(),
            "window_end": data.window_end.isoformat(),
            "current_research": [c.model_dump(mode="json") for c in clusters],
            "source_catalog": [
                s.model_dump(mode="json")
                for s in catalog
                if s.message_ref
                in {
                    e.message_ref
                    for c in clusters
                    for claim in cluster_claims(c)
                    for e in claim.evidence
                }
            ],
            "source_count": data.source_count,
            "message_count": len(data.messages),
            "previous": previous_research(previous),
            "coverage_warnings": data.warnings,
        }
        self._ensure_budget(research_payload, self.settings.analyst_char_budget)
        memo = await self.analyst.parse(
            operation="senior-analyst",
            system_prompt=prompts.SENIOR,
            payload=encode(research_payload),
            schema=AnalyticalMemo,
        )
        validate_memo(memo, clusters, data, previous)
        await self._audit(memo, clusters, data, previous)
        snapshot = AnalysisSnapshot(
            kind=data.kind,
            window_start=data.window_start,
            window_end=data.window_end,
            source_count=data.source_count,
            message_count=len(data.messages),
            clusters=clusters,
            sources=catalog,
            memo=memo,
            research_models=list(self.research.used_models),
            analyst_model=self.analyst.used_models[-1],
            fallback_from=list(self.analyst.failures),
        )
        # Save only audited research. It remains available if Telegram delivery fails.
        await self.history.save(snapshot)
        return AnalyticalResult(snapshot=snapshot, previous=previous)

    async def _extract_batch(
        self, batch: list[dict[str, Any]], data: ReportInput, index: int
    ) -> list[ResearchCluster]:
        result = await self.research.parse(
            operation=f"research-extract-{index}",
            system_prompt=prompts.EXTRACT,
            payload=encode({"parts": batch}),
            schema=Extraction,
        )
        valid, repair_refs, required_refs = inspect_extraction(result, batch, data)
        if not repair_refs:
            return valid

        # Retry only messages affected by a missing cluster or an inexact
        # quote. A second invalid answer fails closed; nothing is discarded.
        repair_parts = [p for p in batch if p["message_ref"] in repair_refs]
        logger.warning(
            "research_extraction_repair_requested",
            batch_index=index,
            part_count=len(repair_parts),
            message_count=len(repair_refs),
        )
        repaired = await self.research.parse(
            operation=f"research-repair-{index}",
            system_prompt=prompts.EXTRACT
            + "\nЭто повторная проверка только спорных сообщений. Цитируй фрагменты "
            "символ в символ из parts.text, сохраняй все полезные message_ref. "
            "Если текст не подтверждает тезис, не включай этот тезис в кластер.",
            payload=encode({"parts": repair_parts}),
            schema=Extraction,
        )
        repair_valid, still_invalid, _ = inspect_extraction(repaired, repair_parts, data)
        if still_invalid:
            raise LunaContractError("Research repair did not ground all useful messages")
        combined = [*valid, *repair_valid]
        covered = {ref for cluster in combined for ref in cluster.message_refs}
        if not required_refs <= covered:
            raise LunaContractError("Research repair omitted an originally useful message")
        return combined

    async def _merge(
        self, clusters: list[ResearchCluster], data: ReportInput
    ) -> list[ResearchCluster]:
        budget = self.settings.openai_batch_char_budget - 2000
        current = clusters
        for level in range(self.settings.openai_reduce_max_levels):
            groups = pack([c.model_dump(mode="json") for c in current], budget)
            next_level: list[ResearchCluster] = []
            by_id = {c.cluster_id: c for c in current}
            for index, group in enumerate(groups):
                if len(group) == 1 and len(groups) > 1:
                    next_level.append(by_id[group[0]["cluster_id"]])
                    continue
                result = await self.research.parse(
                    operation=f"research-merge-{level}-{index}",
                    system_prompt=prompts.MERGE,
                    payload=encode({"clusters": group, "previous": None}),
                    schema=ResearchMerge,
                )
                merged = validate_merge(
                    result, [by_id[g["cluster_id"]] for g in group], data, f"r{level}-{index}"
                )
                next_level.extend(merged)
            if len(groups) == 1:
                return next_level
            if len(encode([c.model_dump(mode="json") for c in next_level])) >= len(
                encode([c.model_dump(mode="json") for c in current])
            ):
                raise LunaContractError("Research cannot fit context without losing evidence")
            current = next_level
        raise LunaContractError("Research reduction exceeded level limit")

    async def _audit(
        self,
        memo: AnalyticalMemo,
        clusters: list[ResearchCluster],
        data: ReportInput,
        previous: AnalysisSnapshot | None,
    ) -> None:
        by_id = {c.cluster_id: c for c in clusters}
        budget = self.settings.openai_batch_char_budget

        # Pack audits and deduplicate shared original sources. Astra never sees these texts.
        def payload_for(claims: list[tuple[str, Any]]) -> dict[str, Any]:
            selected_ids = {c for _, claim in claims for c in claim.cluster_ids}
            selected = [by_id[c] for c in sorted(selected_ids)]
            evidence = [e for c in selected for claim in cluster_claims(c) for e in claim.evidence]
            refs = {e.message_ref for e in evidence}
            raw_sources = []
            for m in data.messages:
                if m.message_ref not in refs:
                    continue
                if len(m.text) <= 6000:
                    raw_sources.append(m.model_dump(mode="json"))
                else:
                    # Exact source excerpts, explicitly labelled; no semantic local filtering.
                    excerpts = []
                    for e in evidence:
                        if e.message_ref == m.message_ref:
                            start = m.text.index(e.quote)
                            excerpts.append(
                                m.text[max(0, start - 500) : start + len(e.quote) + 500]
                            )
                    raw_sources.append(
                        {
                            **m.model_dump(mode="json", exclude={"text"}),
                            "excerpts": list(dict.fromkeys(excerpts)),
                            "full_text_included": False,
                        }
                    )
            return {
                "claims": [{"claim_id": i, **c.model_dump(mode="json")} for i, c in claims],
                "memo_context": memo.model_dump(mode="json"),
                "research": [c.model_dump(mode="json") for c in selected],
                "raw_sources": raw_sources,
                "previous": previous_research(previous),
            }

        groups: list[list[tuple[str, Any]]] = []
        group: list[tuple[str, Any]] = []
        for claim in memo_claims(memo):
            if group and len(encode(payload_for([*group, claim]))) > budget:
                groups.append(group)
                group = []
            group.append(claim)
            self._ensure_budget(payload_for(group), budget)
        if group:
            groups.append(group)
        for index, group in enumerate(groups):
            audit = await self.research.parse(
                operation=f"audit-{index}",
                system_prompt=prompts.AUDIT,
                payload=encode(payload_for(group)),
                schema=MemoAudit,
            )
            if (
                not audit.approved
                or Counter(i.claim_id for i in audit.items) != Counter(i for i, _ in group)
                or not all(i.supported for i in audit.items)
            ):
                raise LunaContractError("Final evidence audit rejected the analytical memo")

    @staticmethod
    def _ensure_budget(payload: Any, budget: int) -> None:
        if len(encode(payload)) > budget:
            raise LunaContractError("Structured research exceeds safe context; nothing truncated")


def previous_research(snapshot: AnalysisSnapshot | None) -> dict[str, Any] | None:
    if snapshot is None:
        return None
    return {
        "window_start": snapshot.window_start.isoformat(),
        "window_end": snapshot.window_end.isoformat(),
        "clusters": [c.model_dump(mode="json") for c in snapshot.clusters],
        "memo": snapshot.memo.model_dump(mode="json"),
        "source_count": snapshot.source_count,
        "message_count": snapshot.message_count,
    }


def split_messages(data: ReportInput, budget: int) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for m in data.messages:
        chunks = [m.text]
        while True:
            oversized = next(
                (
                    i
                    for i, t in enumerate(chunks)
                    if len(
                        encode(
                            {
                                **m.model_dump(mode="json"),
                                "text": t,
                                "part_id": m.message_ref + ":part999999",
                            }
                        )
                    )
                    > budget - 3000
                ),
                None,
            )
            if oversized is None:
                break
            text = chunks[oversized]
            if len(text) < 2:
                raise LunaContractError("Message metadata exceeds context budget")
            midpoint = len(text) // 2
            chunks[oversized : oversized + 1] = [text[:midpoint], text[midpoint:]]
        for index, text in enumerate(chunks):
            parts.append(
                {
                    **m.model_dump(mode="json"),
                    "text": text,
                    "part_id": f"{m.message_ref}:part{index}",
                }
            )
    return parts


def pack(
    items: list[dict[str, Any]], budget: int, *, max_items: int | None = None
) -> list[list[dict[str, Any]]]:
    if max_items is not None and max_items < 1:
        raise ValueError("max_items must be positive")
    groups: list[list[dict[str, Any]]] = []
    group: list[dict[str, Any]] = []
    for item in items:
        if len(encode([item])) > budget:
            raise LunaContractError("Structured item exceeds context budget; nothing truncated")
        if group and (
            (max_items is not None and len(group) >= max_items)
            or len(encode([*group, item])) > budget
        ):
            groups.append(group)
            group = []
        group.append(item)
    if group:
        groups.append(group)
    return groups


def inspect_extraction(
    result: Extraction, batch: list[dict[str, Any]], data: ReportInput
) -> tuple[list[ResearchCluster], set[str], set[str]]:
    """Keep grounded clusters and identify only the refs needing Luna repair."""

    ids = [p["part_id"] for p in batch]
    if Counter(d.part_id for d in result.dispositions) != Counter(ids):
        raise LunaContractError("Extraction lost or duplicated message parts")
    part_refs = {p["part_id"]: p["message_ref"] for p in batch}
    allowed = set(part_refs.values())
    messages = {m.message_ref: m.text for m in data.messages}
    valid: list[ResearchCluster] = []
    repair_refs: set[str] = set()
    for cluster in result.clusters:
        if not set(cluster.message_refs) <= allowed:
            raise LunaContractError("Research contains unknown message references")
        aligned = align_cluster_quotes(cluster, messages)
        try:
            validate_clusters([aligned], data, allowed)
        except LunaContractError as exc:
            if str(exc) != "Research fabricated a source quote":
                raise
            repair_refs.update(cluster.message_refs)
        else:
            valid.append(aligned)
    covered = {ref for cluster in valid for ref in cluster.message_refs}
    required = {
        part_refs[d.part_id]
        for d in result.dispositions
        if d.classification in ("useful", "duplicate")
    }
    repair_refs.update(required - covered)
    return valid, repair_refs, required


def align_cluster_quotes(
    cluster: ResearchCluster, messages: dict[str, str]
) -> ResearchCluster:
    """Restore exact source whitespace when a quote differs only by spacing."""

    updates: dict[str, Any] = {}
    for field in (
        "facts",
        "interpretations",
        "bullish_arguments",
        "bearish_arguments",
        "neutral_arguments",
        "possible_triggers",
    ):
        claims = []
        for claim in getattr(cluster, field):
            evidence = []
            for item in claim.evidence:
                source = messages.get(item.message_ref)
                span = exact_quote_span(source, item.quote) if source is not None else None
                evidence.append(item.model_copy(update={"quote": span}) if span else item)
            claims.append(claim.model_copy(update={"evidence": evidence}))
        updates[field] = claims
    return cluster.model_copy(update=updates)


def exact_quote_span(source: str, quote: str) -> str | None:
    if quote in source:
        return quote
    words = re.split(r"\s+", quote.strip())
    if len(words) < 2:
        return None
    match = re.search(r"\s+".join(re.escape(word) for word in words), source)
    return match.group(0) if match else None


def validate_clusters(
    clusters: list[ResearchCluster], data: ReportInput, allowed: set[str]
) -> None:
    messages = {m.message_ref: m for m in data.messages}
    for cluster in clusters:
        if not cluster_claims(cluster):
            raise LunaContractError("Research cluster has no quoted evidence")
        if not set(cluster.message_refs) <= allowed:
            raise LunaContractError("Research contains unknown message references")
        for claim in cluster_claims(cluster):
            for evidence in claim.evidence:
                if evidence.message_ref not in cluster.message_refs:
                    raise LunaContractError("Claim evidence is outside its cluster")
                if evidence.message_ref not in messages:
                    raise LunaContractError("Research contains unknown evidence reference")
                if evidence.quote not in messages[evidence.message_ref].text:
                    raise LunaContractError("Research fabricated a source quote")


def validate_merge(
    result: ResearchMerge, inputs: list[ResearchCluster], data: ReportInput, prefix: str
) -> list[ResearchCluster]:
    if Counter(result.covered_cluster_ids) != Counter(c.cluster_id for c in inputs):
        raise LunaContractError("Research merge omitted or duplicated clusters")
    refs = {r for c in inputs for r in c.message_refs}
    if {r for c in result.clusters for r in c.message_refs} != refs:
        raise LunaContractError("Research merge lost source coverage")
    validate_clusters(result.clusters, data, refs)
    return [
        c.model_copy(update={"cluster_id": f"{prefix}-{i}"}) for i, c in enumerate(result.clusters)
    ]


def validate_memo(
    memo: AnalyticalMemo,
    clusters: list[ResearchCluster],
    data: ReportInput,
    previous: AnalysisSnapshot | None,
) -> None:
    ids = {c.cluster_id for c in clusters}
    by_id = {c.cluster_id: c for c in clusters}
    for _, claim in memo_claims(memo):
        if not set(claim.cluster_ids) <= ids:
            raise LunaContractError("Analyst referenced an unknown research cluster")
    for theme in memo.main_themes:
        claims = (theme.what_happened, theme.why_important, theme.connections, theme.conclusion)
        allowed = {
            ref for claim in claims for c in claim.cluster_ids for ref in by_id[c].message_refs
        }
        if not set(theme.source_refs) <= allowed:
            raise LunaContractError("Theme sources do not support the selected research")
    if previous is None and any(c.direction not in ("unknown",) for c in memo.changes):
        raise LunaContractError("Cannot claim a temporal trend without a previous period")
    if memo.sentiment.index is not None:
        chat_refs = {m.message_ref for m in data.messages if m.source_kind == "chat"}
        evidence_refs = {
            r
            for c in memo.sentiment.reason.cluster_ids  # type: ignore[union-attr]
            for r in by_id[c].message_refs
        }
        if not chat_refs.intersection(evidence_refs):
            raise LunaContractError("Sentiment index has no chat evidence")
