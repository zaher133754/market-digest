from __future__ import annotations

from datetime import UTC, datetime

from market_digest.reports.models import (
    ChannelDigestReport,
    MarketMoodReport,
    ReportInput,
    ReportKind,
    ReportMessage,
    ReportPoint,
)
from market_digest.reports.rendering import render_digest, render_sentiment


def _data(kind: ReportKind) -> ReportInput:
    return ReportInput(
        kind=kind,
        window_start=datetime(2026, 9, 19, 15, 0, tzinfo=UTC),
        window_end=datetime(2026, 9, 20, 15, 0, tzinfo=UTC),
        source_count=2,
        messages=[
            ReportMessage(
                message_ref="telegram:-1001:1",
                source_title="Публичный канал",
                source_username="public_channel",
                published_at=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
                author_display_name="Автор",
                text="Новость",
                permalink="https://t.me/public_channel/1",
            ),
            ReportMessage(
                message_ref="telegram:-1002:2",
                source_title="Закрытый источник",
                source_username=None,
                published_at=datetime(2026, 9, 20, 11, 0, tzinfo=UTC),
                author_display_name="Участник",
                text="Мнение",
                permalink=None,
            ),
        ],
        warnings=["Один источник пропущен"],
    )


def test_digest_renders_counts_sections_and_real_links_only() -> None:
    data = _data(ReportKind.DIGEST)
    point = ReportPoint(
        text="Рынок обсуждает событие",
        evidence_refs=["telegram:-1001:1", "telegram:-1002:2"],
    )
    report = ChannelDigestReport(
        overview=[point],
        key_events=[point],
        author_views=[],
        instruments_and_macro=[],
        declared_ideas=[],
        disagreements=[],
        watchlist=[],
        conclusion="Следим за развитием.",
    )

    text = render_digest(report, data, timezone_name="Europe/Samara")

    assert "ДАЙДЖЕСТ" in text
    assert "19.09.2026 19:00 — 20.09.2026 19:00" in text
    assert "Источников: 2 · Сообщений: 2" in text
    assert "ГЛАВНОЕ" in text and "КЛЮЧЕВЫЕ СОБЫТИЯ" in text
    assert "https://t.me/public_channel/1" in text
    assert "Закрытый источник" in text
    assert "https://t.me/Закрытый" not in text
    assert "Один источник пропущен" in text


def test_sentiment_renders_detailed_index_label_and_disclaimer() -> None:
    data = _data(ReportKind.SENTIMENT)
    point = ReportPoint(
        text="Участники ждут роста",
        evidence_refs=["telegram:-1002:2"],
    )
    report = MarketMoodReport(
        index=7,
        summary="В чатах осторожно-позитивное настроение.",
        positive=[point],
        negative=[],
        themes=[point],
        expectations=[point],
        disagreements=[],
        extremes=[],
        conclusion="Оптимизм сохраняется, но уверенность не максимальная.",
    )

    text = render_sentiment(report, data, timezone_name="Europe/Samara")

    assert "НАСТРОЕНИЕ УЧАСТНИКОВ ЧАТОВ" in text
    assert "Индекс настроения: 7/10" in text
    assert "Умеренный оптимизм" in text
    assert "ПОЗИТИВНЫЕ ФАКТОРЫ" in text
    assert "ОЖИДАНИЯ" in text
    assert "не является инвестиционной рекомендацией" in text.lower()
