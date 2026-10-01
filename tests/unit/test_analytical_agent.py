from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from market_digest.ai.routing import ModelRoute
from market_digest.errors import LunaContractError, LunaUnavailableError
from market_digest.reports.analysis_models import (
    AnalyticalMemo,
    Disposition,
    Evidence,
    Extraction,
    Insight,
    MainTheme,
    MemoAudit,
    ResearchClaim,
    ResearchCluster,
    ResearchMerge,
    Sentiment,
)
from market_digest.reports.analytical_pipeline import (
    AnalyticalPipeline,
    align_cluster_quotes,
    exact_quote_span,
    pack,
    split_messages,
    validate_memo,
    validate_merge,
)
from market_digest.reports.analytical_rendering import render_analytical
from market_digest.reports.history import ResearchHistory
from market_digest.reports.models import ReportInput, ReportKind, ReportMessage


def corpus(end=None):
    end = end or datetime(2026, 9, 29, 11, tzinfo=UTC)
    return ReportInput(
        kind=ReportKind.DIGEST,
        window_start=end - timedelta(hours=24),
        window_end=end,
        source_count=2,
        warnings=[],
        messages=[
            ReportMessage(
                message_ref="channel:1",
                source_title="Канал",
                source_kind="channel",
                published_at=end,
                author_display_name="Аналитик",
                text="Ставка сохранена. RAW_SENTINEL_PRIVATE",
                permalink="https://t.me/a/1",
            ),
            ReportMessage(
                message_ref="chat:1",
                source_title="Чат",
                source_kind="chat",
                published_at=end,
                author_display_name="Участник",
                text="Сегодня не покупаю.",
                permalink="https://t.me/b/1",
            ),
        ],
    )


def cluster():
    return ResearchCluster(
        cluster_id="c1",
        topic="Ставка и осторожность",
        summary="Участник воздерживается от покупок.",
        message_refs=["channel:1", "chat:1"],
        facts=[
            ResearchClaim(
                text="Ставка сохранена.",
                kind="fact",
                evidence=[Evidence(message_ref="channel:1", quote="Ставка сохранена.")],
            )
        ],
        interpretations=[
            ResearchClaim(
                text="Участник не покупает.",
                kind="source_opinion",
                evidence=[Evidence(message_ref="chat:1", quote="Сегодня не покупаю.")],
            )
        ],
        bullish_arguments=[],
        bearish_arguments=[],
        neutral_arguments=[],
        affected_assets=["рынок акций"],
        independent_source_groups=[["channel:1"]],
        independent_source_count=1,
        importance=7,
        confidence=0.6,
        is_new=None,
        change_vs_previous_period="не сравнивалось",
        sentiment="mixed",
        possible_triggers=[],
    )


def memo(cluster_id):
    insight = Insight(
        text="В выборке участник воздерживается от покупок.",
        kind="synthesis",
        cluster_ids=[cluster_id],
    )
    return AnalyticalMemo(
        sentiment=Sentiment(index=4, reason=insight, insufficient_data=None),
        changes=[],
        consensus=insight,
        main_themes=[
            MainTheme(
                title="Ставка и осторожность",
                what_happened=insight,
                why_important=insight,
                connections=insight,
                conclusion=insight,
                source_refs=["channel:1", "chat:1"],
            )
        ],
        patterns=[insight],
        disagreements=[],
        triggers=[],
        conclusion=insight,
        thought=insight,
        limitations=["Малая выборка."],
    )


class FakeResearch:
    def __init__(self, *, reject=False, omit=False, fabricated=False):
        self.calls = []
        self.reject = reject
        self.omit = omit
        self.fabricated = fabricated

    async def parse(self, *, operation, system_prompt, payload, schema):
        body = json.loads(payload)
        self.calls.append((operation, body))
        if schema is Extraction:
            c = cluster()
            if self.fabricated:
                c = c.model_copy(
                    update={
                        "facts": [
                            ResearchClaim(
                                text="Выдумка",
                                kind="fact",
                                evidence=[Evidence(message_ref="channel:1", quote="НЕ СУЩЕСТВУЕТ")],
                            )
                        ]
                    }
                )
            return Extraction(
                dispositions=[
                    Disposition(part_id=p["part_id"], classification="useful", reason="рынок")
                    for p in body["parts"]
                    if not self.omit
                ],
                clusters=[c],
            )
        if schema is ResearchMerge:
            return ResearchMerge(
                covered_cluster_ids=[c["cluster_id"] for c in body["clusters"]],
                clusters=[ResearchCluster.model_validate(body["clusters"][0])],
            )
        if schema is MemoAudit:
            return MemoAudit(
                items=[
                    {"claim_id": c["claim_id"], "supported": not self.reject, "reason": "checked"}
                    for c in body["claims"]
                ],
                approved=not self.reject,
            )
        raise AssertionError(schema)


class RepairingResearch(FakeResearch):
    async def parse(self, *, operation, system_prompt, payload, schema):
        if operation.startswith("research-repair-"):
            self.fabricated = False
        return await super().parse(
            operation=operation,
            system_prompt=system_prompt,
            payload=payload,
            schema=schema,
        )


class RepairingOmittedResearch(FakeResearch):
    async def parse(self, *, operation, system_prompt, payload, schema):
        if operation == "research-extract-0":
            body = json.loads(payload)
            self.calls.append((operation, body))
            return Extraction(
                dispositions=[
                    Disposition(part_id=p["part_id"], classification="useful", reason="рынок")
                    for p in body["parts"]
                ],
                clusters=[],
            )
        return await super().parse(
            operation=operation,
            system_prompt=system_prompt,
            payload=payload,
            schema=schema,
        )


class FakeAnalyst:
    def __init__(self, error=None):
        self.error = error
        self.payloads = []

    async def parse(self, *, operation, system_prompt, payload, schema):
        self.payloads.append(json.loads(payload))
        if self.error:
            raise self.error
        body = json.loads(payload)
        return memo(body["current_research"][0]["cluster_id"])


def pipeline(settings, tmp_path, research=None, analyst=None):
    return AnalyticalPipeline(
        settings,
        ModelRoute([("gpt-6-luna", research or FakeResearch())]),
        ModelRoute([("gpt-6-astra", analyst or FakeAnalyst())]),
        ResearchHistory(tmp_path / "research.json"),
    )


@pytest.mark.asyncio
async def test_full_pipeline_routes_raw_only_to_luna_and_audits_before_history(settings, tmp_path):
    analyst, research = FakeAnalyst(), FakeResearch()
    pipe = pipeline(settings, tmp_path, research, analyst)
    result = await pipe.analyze(corpus())
    assert result is not None and result.snapshot.analyst_model == "gpt-6-astra"
    assert "RAW_SENTINEL_PRIVATE" not in json.dumps(analyst.payloads)
    assert "RAW_SENTINEL_PRIVATE" in json.dumps(research.calls)
    assert len([op for op, _ in research.calls if op.startswith("audit")]) == 1
    assert result.previous is None
    reloaded = ResearchHistory(tmp_path / "research.json")
    assert (await reloaded.recent())[0] == result.snapshot
    assert "RAW_SENTINEL_PRIVATE" not in (tmp_path / "research.json").read_text("utf-8")


@pytest.mark.asyncio
async def test_fallback_order_is_explicit_and_rendered(settings, tmp_path):
    astra, sol = FakeAnalyst(LunaUnavailableError("quota")), FakeAnalyst()
    pipe = pipeline(settings, tmp_path)
    pipe.analyst = ModelRoute([("gpt-6-astra", astra), ("gpt-6-sol", sol)])
    result = await pipe.analyze(corpus())
    assert result.snapshot.analyst_model == "gpt-6-sol"
    assert result.snapshot.fallback_from == ["gpt-6-astra"]
    text = render_analytical(result, "Europe/Samara", [])
    assert "Резервный режим" in text and "gpt-6-sol" in text
    assert text.count("https://t.me/a/1") == 1
    assert "ПОЗИТИВНЫЕ ФАКТОРЫ" not in text


@pytest.mark.asyncio
async def test_invalid_schema_does_not_fallback(settings, tmp_path):
    sol = FakeAnalyst()
    pipe = pipeline(settings, tmp_path)
    pipe.analyst = ModelRoute(
        [("gpt-6-astra", FakeAnalyst(LunaContractError("bad schema"))), ("gpt-6-sol", sol)]
    )
    with pytest.raises(LunaContractError):
        await pipe.analyze(corpus())
    assert not sol.payloads


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["reject", "omit", "fabricated"])
async def test_failure_never_saves_unverified_history(settings, tmp_path, option):
    pipe = pipeline(settings, tmp_path, FakeResearch(**{option: True}))
    with pytest.raises(LunaContractError):
        await pipe.analyze(corpus())
    assert not (tmp_path / "research.json").exists()


@pytest.mark.asyncio
async def test_previous_period_survives_restart_and_ignores_overlapping_reports(settings, tmp_path):
    today = corpus()
    yesterday = corpus(today.window_start)
    pipe = pipeline(settings, tmp_path)
    old = await pipe.analyze(yesterday)
    await pipe.analyze(corpus(today.window_end - timedelta(hours=1)))
    restarted = pipeline(settings, tmp_path)
    result = await restarted.analyze(today)
    assert result.previous == old.snapshot
    body = restarted.analyst.clients[0][1].payloads[0]
    assert body["previous"]["memo"]["sentiment"]["index"] == 4
    assert "4/10 → 4/10 =" in render_analytical(result, "Europe/Samara", [])


def test_oversized_message_split_is_lossless():
    data = corpus()
    text = "Полный текст и цифры 12345 " * 4000
    m = data.messages[0].model_copy(update={"text": text})
    parts = split_messages(data.model_copy(update={"messages": [m]}), 20000)
    assert len(parts) > 1
    assert "".join(p["text"] for p in parts) == text


def test_quote_alignment_changes_only_whitespace():
    assert exact_quote_span("Ставка\nсохранена.", "Ставка сохранена.") == "Ставка\nсохранена."
    assert exact_quote_span("Ставка сохранена.", "Ставка снижена.") is None
    raw = cluster()
    changed = raw.model_copy(
        update={
            "facts": [
                raw.facts[0].model_copy(
                    update={
                        "evidence": [
                            raw.facts[0].evidence[0].model_copy(
                                update={"quote": "Ставка\nсохранена."}
                            )
                        ]
                    }
                )
            ]
        }
    )
    repaired = align_cluster_quotes(changed, {"channel:1": "Ставка сохранена."})
    assert repaired.facts[0].evidence[0].quote == "Ставка сохранена."


@pytest.mark.asyncio
async def test_fabricated_quote_reprocessed_only_once_with_luna(settings, tmp_path):
    research = RepairingResearch(fabricated=True)
    result = await pipeline(settings, tmp_path, research=research).analyze(corpus())
    assert result is not None
    assert [operation for operation, _ in research.calls if operation.startswith("research-")] == [
        "research-extract-0",
        "research-repair-0",
        "research-merge-0-0",
    ]


@pytest.mark.asyncio
async def test_useful_messages_missing_from_clusters_are_reprocessed(settings, tmp_path):
    research = RepairingOmittedResearch()
    result = await pipeline(settings, tmp_path, research=research).analyze(corpus())
    assert result is not None
    assert [operation for operation, _ in research.calls if operation.startswith("research-")] == [
        "research-extract-0",
        "research-repair-0",
        "research-merge-0-0",
    ]


def test_extraction_batch_limit_keeps_all_large_corpus_parts():
    parts = [{"part_id": f"part-{i}", "text": "сообщение"} for i in range(2760)]
    batches = pack(parts, 218_000, max_items=120)
    assert len(batches) == 23
    assert max(map(len, batches)) == 120
    assert [part for batch in batches for part in batch] == parts


def test_research_cluster_reconciles_only_redundant_model_fields():
    raw = cluster().model_dump(mode="json")
    raw["independent_source_groups"] = [["channel:1", "channel:1"], ["channel:1"]]
    raw["independent_source_count"] = 3
    raw["facts"].append(
        ResearchClaim(
            text="Участник решил не покупать.",
            kind="source_opinion",
            evidence=[Evidence(message_ref="chat:1", quote="Сегодня не покупаю.")],
        ).model_dump(mode="json")
    )
    raw["interpretations"].append(raw["facts"][0])
    repaired = ResearchCluster.model_validate(raw)
    assert repaired.independent_source_groups == [["channel:1"]]
    assert repaired.independent_source_count == 1
    assert all(claim.kind == "fact" for claim in repaired.facts)
    assert any(claim.kind == "source_opinion" for claim in repaired.interpretations)
    assert sum(len(items) for items in (repaired.facts, repaired.interpretations)) == sum(
        len(raw[name]) for name in ("facts", "interpretations")
    )


def test_research_cluster_still_rejects_invented_origin_reference():
    raw = cluster().model_dump(mode="json")
    raw["independent_source_groups"] = [["not-in-cluster"]]
    with pytest.raises(ValueError, match="Unknown origin reference"):
        ResearchCluster.model_validate(raw)


@pytest.mark.asyncio
async def test_extraction_contract_error_identifies_stage(settings, tmp_path):
    with pytest.raises(LunaContractError, match="research-extract-0"):
        await pipeline(settings, tmp_path, FakeResearch(omit=True)).analyze(corpus())


@pytest.mark.asyncio
async def test_no_chat_evidence_cannot_produce_sentiment_index(settings, tmp_path):
    data = corpus()
    data = data.model_copy(
        update={
            "messages": [m.model_copy(update={"source_kind": "channel"}) for m in data.messages]
        }
    )
    with pytest.raises(LunaContractError, match="chat evidence"):
        await pipeline(settings, tmp_path).analyze(data)


@pytest.mark.asyncio
async def test_all_fallbacks_exhausted_no_snapshot(settings, tmp_path):
    pipe = pipeline(settings, tmp_path)
    clients = [
        (m, FakeAnalyst(LunaUnavailableError("quota")))
        for m in ["gpt-6-astra", "gpt-6-sol", "gpt-5.6-luna"]
    ]
    pipe.analyst = ModelRoute(clients)
    with pytest.raises(LunaUnavailableError):
        await pipe.analyze(corpus())
    assert all(len(client.payloads) == 1 for _, client in clients)
    assert not (tmp_path / "research.json").exists()


@pytest.mark.asyncio
async def test_last_resort_current_model_and_research_unavailable(settings, tmp_path):
    pipe = pipeline(settings, tmp_path)
    current = FakeAnalyst()
    pipe.analyst = ModelRoute(
        [
            ("gpt-6-astra", FakeAnalyst(LunaUnavailableError("quota"))),
            ("gpt-6-sol", FakeAnalyst(LunaUnavailableError("quota"))),
            ("gpt-5.6-luna", current),
        ]
    )
    result = await pipe.analyze(corpus())
    assert result.snapshot.analyst_model == "gpt-5.6-luna"
    assert result.snapshot.fallback_from == ["gpt-6-astra", "gpt-6-sol"]


def test_merge_cannot_drop_sources_while_claiming_coverage():
    original = cluster()
    reduced = original.model_copy(update={"message_refs": ["channel:1"]})
    with pytest.raises(LunaContractError, match="source coverage"):
        validate_merge(
            ResearchMerge(covered_cluster_ids=["c1"], clusters=[reduced]),
            [original],
            corpus(),
            "merged",
        )


def test_cannot_invent_yesterday_trend():
    from market_digest.reports.analysis_models import TopicChange

    report = memo("c1")
    report = report.model_copy(
        update={
            "changes": [TopicChange(topic="Ставка", direction="up", explanation=report.conclusion)]
        }
    )
    with pytest.raises(LunaContractError, match="previous period"):
        validate_memo(report, [cluster()], corpus(), None)


@pytest.mark.asyncio
async def test_stale_history_is_not_yesterday(settings, tmp_path):
    data = corpus()
    pipe = pipeline(settings, tmp_path)
    await pipe.analyze(corpus(data.window_end - timedelta(days=4)))
    result = await pipe.analyze(data)
    assert result.previous is None


@pytest.mark.asyncio
async def test_corrupt_history_fails_without_overwriting(settings, tmp_path):
    path = tmp_path / "research.json"
    path.write_text("broken", encoding="utf-8")
    pipe = pipeline(settings, tmp_path)
    with pytest.raises(ValueError):
        await pipe.analyze(corpus())
    assert not pipe.research.clients[0][1].calls
    assert path.read_text("utf-8") == "broken"
