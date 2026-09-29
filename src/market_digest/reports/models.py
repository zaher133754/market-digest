"""Strict transport-neutral contracts for stateless reports."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ReportKind(StrEnum):
    DIGEST = "digest"
    SENTIMENT = "sentiment"


class RunTrigger(StrEnum):
    MANUAL = "manual"
    SCHEDULED = "scheduled"


class ReportMessage(StrictModel):
    message_ref: str = Field(min_length=1)
    source_title: str = Field(min_length=1)
    source_username: str | None = None
    published_at: datetime
    author_display_name: str = Field(min_length=1)
    text: str = Field(min_length=1)
    permalink: str | None = None
    source_kind: Literal["channel", "chat"] = "channel"


class ReportInput(StrictModel):
    kind: ReportKind
    window_start: datetime
    window_end: datetime
    source_count: int = Field(ge=0)
    messages: list[ReportMessage]
    warnings: list[str]

    @model_validator(mode="after")
    def validate_window(self) -> ReportInput:
        if self.window_start.tzinfo is None or self.window_end.tzinfo is None:
            raise ValueError("report window must be timezone-aware")
        if self.window_start >= self.window_end:
            raise ValueError("report window start must precede end")
        return self


class ReportPoint(StrictModel):
    text: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)


class ChannelDigestReport(StrictModel):
    overview: list[ReportPoint]
    key_events: list[ReportPoint]
    author_views: list[ReportPoint]
    instruments_and_macro: list[ReportPoint]
    declared_ideas: list[ReportPoint]
    disagreements: list[ReportPoint]
    watchlist: list[ReportPoint]
    conclusion: str = Field(min_length=1)


class MarketMoodReport(StrictModel):
    index: int = Field(ge=1, le=10)
    summary: str = Field(min_length=1)
    positive: list[ReportPoint]
    negative: list[ReportPoint]
    themes: list[ReportPoint]
    expectations: list[ReportPoint]
    disagreements: list[ReportPoint]
    extremes: list[ReportPoint]
    conclusion: str = Field(min_length=1)


def mood_label(score: int) -> str:
    """Return the approved deterministic label for a 1-10 mood score."""

    if not 1 <= score <= 10:
        raise ValueError("mood score must be between 1 and 10")
    if score <= 2:
        return "паника"
    if score <= 4:
        return "негатив"
    if score == 5:
        return "нейтральное настроение"
    if score <= 7:
        return "умеренный оптимизм"
    if score <= 9:
        return "сильная эйфория"
    return "предельная эйфория"
