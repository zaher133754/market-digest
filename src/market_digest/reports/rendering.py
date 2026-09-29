"""Deterministic plain-text rendering for owner Telegram delivery."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .models import (
    ChannelDigestReport,
    MarketMoodReport,
    ReportInput,
    ReportMessage,
    ReportPoint,
    mood_label,
)


def render_digest(
    report: ChannelDigestReport,
    data: ReportInput,
    *,
    timezone_name: str,
) -> str:
    parts = [
        "📊 ДАЙДЖЕСТ ПО ПАПКЕ «Посты»",
        _metadata(data, timezone_name),
    ]
    for heading, points in (
        ("ГЛАВНОЕ", report.overview),
        ("КЛЮЧЕВЫЕ СОБЫТИЯ", report.key_events),
        ("МНЕНИЯ АВТОРОВ", report.author_views),
        ("ИНСТРУМЕНТЫ И МАКРО", report.instruments_and_macro),
        ("ЗАЯВЛЕННЫЕ ИДЕИ АВТОРОВ", report.declared_ideas),
        ("РАЗНОГЛАСИЯ", report.disagreements),
        ("ЧТО ОТСЛЕЖИВАТЬ", report.watchlist),
    ):
        if points:
            parts.append(_section(heading, points, data))
    parts.append(f"ВЫВОД\n{report.conclusion}")
    _append_warnings(parts, data)
    parts.append("Не является индивидуальной инвестиционной рекомендацией.")
    return "\n\n".join(parts)


def render_sentiment(
    report: MarketMoodReport,
    data: ReportInput,
    *,
    timezone_name: str,
) -> str:
    label = mood_label(report.index)
    label = label[:1].upper() + label[1:]
    parts = [
        "🌡 НАСТРОЕНИЕ УЧАСТНИКОВ ЧАТОВ",
        _metadata(data, timezone_name),
        f"Индекс настроения: {report.index}/10 — {label}\n{report.summary}",
    ]
    for heading, points in (
        ("ПОЗИТИВНЫЕ ФАКТОРЫ", report.positive),
        ("НЕГАТИВНЫЕ ФАКТОРЫ", report.negative),
        ("ГЛАВНЫЕ ТЕМЫ", report.themes),
        ("ОЖИДАНИЯ", report.expectations),
        ("РАЗНОГЛАСИЯ", report.disagreements),
        ("КРАЙНИЕ МНЕНИЯ", report.extremes),
    ):
        if points:
            parts.append(_section(heading, points, data))
    parts.append(f"ВЫВОД\n{report.conclusion}")
    _append_warnings(parts, data)
    parts.append(
        "Индекс описывает настроение участников чатов и не является "
        "инвестиционной рекомендацией или прогнозом направления рынка."
    )
    return "\n\n".join(parts)


def _metadata(data: ReportInput, timezone_name: str) -> str:
    timezone = ZoneInfo(timezone_name)
    return (
        f"Период: {_format_time(data.window_start, timezone)} — "
        f"{_format_time(data.window_end, timezone)} ({timezone_name})\n"
        f"Источников: {data.source_count} · Сообщений: {len(data.messages)}"
    )


def _format_time(value: datetime, timezone: ZoneInfo) -> str:
    return value.astimezone(timezone).strftime("%d.%m.%Y %H:%M")


def _section(heading: str, points: list[ReportPoint], data: ReportInput) -> str:
    messages = {message.message_ref: message for message in data.messages}
    lines = [heading]
    for point in points:
        lines.append(f"• {point.text}{_sources(point, messages)}")
    return "\n".join(lines)


def _sources(point: ReportPoint, messages: dict[str, ReportMessage]) -> str:
    labels: list[str] = []
    seen: set[str] = set()
    for ref in point.evidence_refs:
        message = messages.get(ref)
        if message is None:
            raise ValueError(f"Unknown report evidence reference: {ref}")
        label = (
            f"{message.source_title} — {message.permalink}"
            if message.permalink
            else message.source_title
        )
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return "\nИсточники: " + "; ".join(labels)


def _append_warnings(parts: list[str], data: ReportInput) -> None:
    if data.warnings:
        parts.append("ПРЕДУПРЕЖДЕНИЯ\n" + "\n".join(f"• {item}" for item in data.warnings))
