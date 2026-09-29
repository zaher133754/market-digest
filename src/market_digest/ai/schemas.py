"""Strict Pydantic contracts for every semantic pass."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Score = Annotated[float, Field(ge=0.0, le=1.0)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceKind(StrEnum):
    CHANNEL = "channel"
    CHAT = "chat"


class RecordKind(StrEnum):
    CHANNEL_POST = "channel_post"
    CHAT_MESSAGE = "chat_message"


class MarketCategory(StrEnum):
    STOCKS = "акции"
    MOEX_INDEX = "индекс Мосбиржи"
    CORPORATE = "корпоративные события"
    DIVIDENDS = "дивиденды"
    BONDS = "облигации"
    MACRO = "макроэкономика"
    COMMODITIES = "сырьё"
    GEOPOLITICS = "геополитика"


class Direction(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"
    MIXED = "mixed"
    UNCLEAR = "unclear"


class StatementKind(StrEnum):
    FACT = "fact"
    AUTHOR_OPINION = "author_opinion"
    AUTHOR_DECLARED_TRADE = "author_declared_trade"
    PARTICIPANT_SENTIMENT = "participant_sentiment"
    MODEL_SYNTHESIS = "model_synthesis"
    RISK = "risk"
    SCENARIO = "scenario"


class SourceMessage(StrictModel):
    """A complete Telegram text/caption passed to Luna without semantic filtering."""

    message_ref: str
    record_kind: RecordKind
    record_id: str
    message_id: int
    source_id: int
    source_title: str
    source_username: str | None
    source_kind: SourceKind
    published_at: datetime
    author_ref: str
    author_display_name: str
    text: str
    permalink: str | None


class HistoricAuthorPosition(StrictModel):
    author_ref: str
    author_display_name: str
    subject: str
    stance: Direction
    statement: str
    recorded_at: datetime
    evidence_refs: list[str]


class VerifiableFact(StrictModel):
    statement: str
    source_fragment: str
    attribution: str | None
    evidence_quality: Score


class NumericMetric(StrictModel):
    name: str
    value: str
    unit: str | None
    period: str | None
    context: str
    source_fragment: str


class AuthorOpinion(StrictModel):
    author_ref: str
    subject: str
    statement: str
    direction: Direction
    time_horizon: str | None
    source_fragment: str


class MarketScenario(StrictModel):
    author_ref: str
    subject: str
    outcome: str
    conditions: list[str]
    invalidation_conditions: list[str]
    time_horizon: str | None
    source_fragment: str


class TechnicalLevel(StrictModel):
    instrument: str
    level: str
    level_type: Literal["support", "resistance", "other"]
    conditions: list[str]
    invalidation_conditions: list[str]
    source_fragment: str


class DeclaredTrade(StrictModel):
    author_ref: str
    instrument: str
    action: Literal["buy", "sell", "hold", "reduce", "increase", "close", "other"]
    price_or_range: str | None
    size: str | None
    declared_at: str | None
    statement: str
    source_fragment: str


class CorporateEvent(StrictModel):
    issuer: str
    event_type: str
    description: str
    event_date_or_period: str | None
    source_fragment: str


class MessageExtraction(StrictModel):
    message_ref: str
    message_id: int
    source_id: int
    is_relevant: bool
    relevance_score: Score
    specificity: Score
    market_impact: Score
    novelty: Score
    evidence_quality: Score
    advertising_probability: Score
    emotional_noise: Score
    categories: list[MarketCategory]
    tickers: list[str]
    issuers: list[str]
    verifiable_facts: list[VerifiableFact]
    numeric_metrics: list[NumericMetric]
    author_opinions: list[AuthorOpinion]
    scenarios: list[MarketScenario]
    technical_levels: list[TechnicalLevel]
    declared_trades: list[DeclaredTrade]
    corporate_events: list[CorporateEvent]
    risks: list[str]
    advertising_fragments: list[str]
    emotional_noise_fragments: list[str]
    useful_thesis: str | None
    exclusion_reason: str | None


class ExtractionBatch(StrictModel):
    items: list[MessageExtraction]


class SourceLineage(StrictModel):
    source_ref: str
    role: Literal["primary", "independent_confirmation", "retelling", "opinion", "chat_signal"]
    depends_on_source_ref: str | None
    rationale: str


class ClusterFact(StrictModel):
    statement: str
    evidence_refs: list[str]
    independent_source_count: int = Field(ge=0)


class PositionComparison(StrictModel):
    author_ref: str
    author_display_name: str
    subject: str
    current_position: str
    direction: Direction
    evidence_refs: list[str]


class Disagreement(StrictModel):
    subject: str
    positions: list[PositionComparison]
    explanation: str


class PositionChange(StrictModel):
    author_ref: str
    author_display_name: str
    subject: str
    previous_position: str | None
    current_position: str
    change_confirmed: bool
    previous_evidence_refs: list[str]
    current_evidence_refs: list[str]
    explanation: str


class ClusterTrade(StrictModel):
    author_ref: str
    author_display_name: str
    statement: str
    evidence_refs: list[str]


class SentimentSignal(StrictModel):
    author_ref: str
    direction: Direction
    statement: str
    evidence_refs: list[str]


class ComparisonCluster(StrictModel):
    cluster_id: str
    cluster_kind: Literal["news", "chat_sentiment"]
    title: str
    categories: list[MarketCategory]
    importance: Score
    primary_source_ref: str | None
    source_refs: list[str]
    source_lineage: list[SourceLineage]
    facts: list[ClusterFact]
    consensus: str | None
    author_positions: list[PositionComparison]
    disagreements: list[Disagreement]
    position_changes: list[PositionChange]
    declared_trades: list[ClusterTrade]
    sentiment_signals: list[SentimentSignal]
    risks: list[str]


class AuthorPositionUpdate(StrictModel):
    author_ref: str
    author_display_name: str
    subject: str
    stance: Direction
    statement: str
    evidence_refs: list[str]
    explicit_change_from_history: bool
    prior_evidence_refs: list[str]


class ClusterExclusion(StrictModel):
    message_ref: str
    reason: str


class ComparisonResult(StrictModel):
    clusters: list[ComparisonCluster]
    author_position_updates: list[AuthorPositionUpdate]
    excluded_messages: list[ClusterExclusion]


class DigestLine(StrictModel):
    claim_id: str
    text: str
    statement_kind: StatementKind
    evidence_refs: list[str]


class DigestTopic(StrictModel):
    topic_id: str
    category: MarketCategory
    heading: DigestLine
    importance: Score
    common_conclusion: DigestLine
    factual_points: list[DigestLine]
    author_views: list[DigestLine]
    scenarios_and_risks: list[DigestLine]


class ParticipantSentiment(StrictModel):
    overall: Direction
    sample_authors: int = Field(ge=0)
    overview: DigestLine
    bullish_drivers: list[DigestLine]
    bearish_drivers: list[DigestLine]
    neutral_or_uncertain_drivers: list[DigestLine]


class DigestDraft(StrictModel):
    window_start: datetime
    window_end: datetime
    overview: list[DigestLine]
    topics: list[DigestTopic]
    participant_sentiment: ParticipantSentiment | None
    declared_trades: list[DigestLine]
    closing_risks: list[DigestLine]


class ClaimCandidate(StrictModel):
    claim_id: str
    text: str
    statement_kind: StatementKind
    evidence_refs: list[str]


class ClaimAudit(StrictModel):
    claim_id: str
    supported: bool
    facts_supported: bool
    numbers_dates_tickers_correct: bool
    attribution_correct: bool
    fact_opinion_separation_correct: bool
    advertising_removed: bool
    disagreements_preserved: bool
    allowed_evidence_refs: list[str]
    issues: list[str]
    corrected_text: str | None

    @model_validator(mode="after")
    def supported_claim_has_all_audit_guards(self) -> ClaimAudit:
        """Make approval fail closed when any required audit dimension fails."""

        checks = (
            self.facts_supported,
            self.numbers_dates_tickers_correct,
            self.attribution_correct,
            self.fact_opinion_separation_correct,
            self.advertising_removed,
            self.disagreements_preserved,
        )
        if self.supported and not all(checks):
            raise ValueError("supported=true requires every ClaimAudit quality flag to be true")
        if not self.supported and self.corrected_text is not None:
            raise ValueError("unsupported claim must not provide corrected_text")
        return self


class ClaimAuditBatch(StrictModel):
    items: list[ClaimAudit]


class VerificationResult(StrictModel):
    approved: bool
    final_digest: DigestDraft
    removed_claim_ids: list[str]
    verification_notes: list[str]


class PreflightResult(StrictModel):
    status: Literal["ok"]
