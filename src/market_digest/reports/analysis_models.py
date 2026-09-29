"""Evidence-bearing research and concise analytical memo contracts."""

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .models import ReportKind, StrictModel

StatementKind = Literal[
    "fact", "source_opinion", "forecast", "emotion", "model_interpretation", "synthesis"
]


class Evidence(StrictModel):
    message_ref: str
    quote: str = Field(min_length=1, max_length=1200)


class ResearchClaim(StrictModel):
    text: str = Field(min_length=1, max_length=1000)
    kind: StatementKind
    evidence: list[Evidence] = Field(min_length=1)


class ResearchCluster(StrictModel):
    cluster_id: str
    topic: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1000)
    message_refs: list[str] = Field(min_length=1)
    facts: list[ResearchClaim]
    interpretations: list[ResearchClaim]
    bullish_arguments: list[ResearchClaim]
    bearish_arguments: list[ResearchClaim]
    neutral_arguments: list[ResearchClaim]
    affected_assets: list[str]
    # Groups contain copied/derivative sources of ONE originating report.
    independent_source_groups: list[list[str]]
    independent_source_count: int = Field(ge=0)
    importance: int = Field(ge=1, le=10)
    confidence: float = Field(ge=0, le=1)
    is_new: bool | None
    change_vs_previous_period: str
    sentiment: Literal["bullish", "bearish", "neutral", "mixed", "unknown"]
    possible_triggers: list[ResearchClaim]

    @model_validator(mode="after")
    def check_claims(self) -> "ResearchCluster":
        if self.independent_source_count != len(self.independent_source_groups):
            raise ValueError("Independent count must equal origin groups, not messages")
        origins = [ref for group in self.independent_source_groups for ref in group]
        if any(not g for g in self.independent_source_groups) or len(origins) != len(set(origins)):
            raise ValueError("Independent source groups must be nonempty and disjoint")
        if not set(origins) <= set(self.message_refs):
            raise ValueError("Unknown origin reference")
        if any(c.kind != "fact" for c in self.facts):
            raise ValueError("Facts cannot contain opinions or forecasts")
        return self


class Disposition(StrictModel):
    part_id: str
    classification: Literal["useful", "noise", "advertising", "duplicate"]
    reason: str


class Extraction(StrictModel):
    dispositions: list[Disposition]
    clusters: list[ResearchCluster]


class ResearchMerge(StrictModel):
    covered_cluster_ids: list[str]
    clusters: list[ResearchCluster]


class Insight(StrictModel):
    text: str = Field(min_length=1, max_length=750)
    kind: StatementKind
    cluster_ids: list[str] = Field(min_length=1)


class MainTheme(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    what_happened: Insight
    why_important: Insight
    connections: Insight
    conclusion: Insight
    source_refs: list[str] = Field(min_length=1, max_length=4)


class Sentiment(StrictModel):
    index: int | None = Field(ge=1, le=10)
    reason: Insight | None
    insufficient_data: str | None

    @model_validator(mode="after")
    def check_reason(self) -> "Sentiment":
        if self.index is None and not self.insufficient_data:
            raise ValueError("Missing sentiment must explain insufficient data")
        if self.index is not None and self.reason is None:
            raise ValueError("Sentiment index needs evidence")
        return self


class TopicChange(StrictModel):
    topic: str = Field(max_length=120)
    direction: Literal["up", "down", "same", "new", "unknown"]
    explanation: Insight


class AnalyticalMemo(StrictModel):
    sentiment: Sentiment
    changes: list[TopicChange] = Field(max_length=5)
    consensus: Insight | None
    main_themes: list[MainTheme] = Field(max_length=5)
    patterns: list[Insight] = Field(max_length=4)
    disagreements: list[Insight] = Field(max_length=3)
    triggers: list[Insight] = Field(max_length=5)
    conclusion: Insight
    thought: Insight
    limitations: list[str] = Field(max_length=5)

    @model_validator(mode="after")
    def compact(self) -> "AnalyticalMemo":
        text = " ".join(claim.text for _, claim in memo_claims(self))
        if len(text) > 9000:
            raise ValueError("Analytical memo exceeds compact text budget")
        return self


class AuditItem(StrictModel):
    claim_id: str
    supported: bool
    reason: str


class MemoAudit(StrictModel):
    items: list[AuditItem]
    approved: bool


class SourceEntry(StrictModel):
    message_ref: str
    source_title: str
    source_kind: Literal["channel", "chat"]
    author: str
    permalink: str | None


class AnalysisSnapshot(StrictModel):
    version: Literal[1] = 1
    kind: ReportKind
    window_start: datetime
    window_end: datetime
    source_count: int
    message_count: int
    clusters: list[ResearchCluster]
    sources: list[SourceEntry]
    memo: AnalyticalMemo
    research_models: list[str]
    analyst_model: str
    fallback_from: list[str]

    @model_validator(mode="after")
    def valid_window(self) -> "AnalysisSnapshot":
        if (
            self.window_start.tzinfo is None
            or self.window_end.tzinfo is None
            or self.window_start >= self.window_end
        ):
            raise ValueError("Snapshot requires an aware, ordered window")
        return self


class AnalyticalResult(StrictModel):
    snapshot: AnalysisSnapshot
    previous: AnalysisSnapshot | None


def cluster_claims(cluster: ResearchCluster) -> list[ResearchClaim]:
    return [
        *cluster.facts,
        *cluster.interpretations,
        *cluster.bullish_arguments,
        *cluster.bearish_arguments,
        *cluster.neutral_arguments,
        *cluster.possible_triggers,
    ]


def memo_claims(memo: AnalyticalMemo) -> list[tuple[str, Insight]]:
    result: list[tuple[str, Insight]] = []
    for i, theme in enumerate(memo.main_themes):
        for field in ("what_happened", "why_important", "connections", "conclusion"):
            result.append((f"theme:{i}:{field}", getattr(theme, field)))
    for field in ("patterns", "disagreements", "triggers"):
        result.extend((f"{field}:{i}", claim) for i, claim in enumerate(getattr(memo, field)))
    result.extend((f"change:{i}", c.explanation) for i, c in enumerate(memo.changes))
    for field, claim in [
        ("sentiment", memo.sentiment.reason),
        ("consensus", memo.consensus),
        ("conclusion", memo.conclusion),
        ("thought", memo.thought),
    ]:
        if claim is not None:
            result.append((field, claim))
    return result
