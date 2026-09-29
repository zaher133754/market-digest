"""Concise analytical memo with evidence labels and limited links per topic."""

from zoneinfo import ZoneInfo

from .analysis_models import AnalyticalResult, Insight

LABELS = {
    "fact": "Факт",
    "source_opinion": "Мнение источников",
    "forecast": "Прогноз источников",
    "emotion": "Реакция участников",
    "model_interpretation": "Интерпретация модели",
    "synthesis": "Вывод из данных",
}


def line(insight: Insight) -> str:
    return f"{LABELS[insight.kind]}: {insight.text}"


def render_analytical(result: AnalyticalResult, timezone_name: str, warnings: list[str]) -> str:
    s = result.snapshot
    memo = s.memo
    tz = ZoneInfo(timezone_name)
    period = (
        f"{s.window_start.astimezone(tz):%d.%m.%Y %H:%M} — "
        f"{s.window_end.astimezone(tz):%d.%m.%Y %H:%M}"
    )
    parts = [
        "📊 АНАЛИТИЧЕСКИЙ ДАЙДЖЕСТ"
        if s.kind.value == "digest"
        else "🌡 АНАЛИТИЧЕСКАЯ ЗАПИСКА: НАСТРОЕНИЕ РЫНКА",
        f"{period} ({timezone_name})\n"
        f"Изучено источников: {s.source_count} · Сообщений: {s.message_count}",
    ]
    current = memo.sentiment.index
    previous = result.previous
    old = previous.memo.sentiment.index if previous else None
    if current is None:
        mood = "Индекс не определён: " + (memo.sentiment.insufficient_data or "недостаточно данных")
    elif old is None:
        mood = f"{current}/10. Нет сопоставимого предыдущего индекса."
    else:
        arrow = "↑" if current > old else "↓" if current < old else "="
        mood = f"{old}/10 → {current}/10 {arrow}"
    if memo.sentiment.reason:
        mood += "\n" + line(memo.sentiment.reason)
    parts.append("НАСТРОЕНИЕ\n" + mood)
    if previous:
        parts.append(
            "Сравнение с периодом: "
            f"{previous.window_start.astimezone(tz):%d.%m %H:%M} — "
            f"{previous.window_end.astimezone(tz):%d.%m %H:%M}"
        )
    if memo.changes:
        arrows = {"up": "↑", "down": "↓", "same": "=", "new": "новая тема", "unknown": "?"}
        parts.append(
            "ИЗМЕНЕНИЯ\n"
            + "\n".join(
                f"• {c.topic} {arrows[c.direction]} — {c.explanation.text}" for c in memo.changes
            )
        )
    catalog = {entry.message_ref: entry for entry in s.sources}
    topics = ["ГЛАВНОЕ"]
    for theme in memo.main_themes:
        links = list(
            dict.fromkeys(
                f"{catalog[ref].source_title}: {catalog[ref].permalink}"
                for ref in theme.source_refs
                if catalog[ref].permalink
            )
        )[:4]
        topics.append(
            "\n".join(
                [
                    theme.title,
                    line(theme.what_happened),
                    line(theme.why_important),
                    line(theme.connections),
                    line(theme.conclusion),
                    *links,
                ]
            )
        )
    if memo.main_themes:
        parts.append("\n\n".join(topics))
    if memo.consensus:
        parts.append("КОНСЕНСУС ИСТОЧНИКОВ\n" + line(memo.consensus))
    for title, insights in [
        ("ЧТО ВИДИТ АНАЛИТИК", memo.patterns),
        ("⚖️ ГДЕ НЕТ КОНСЕНСУСА", memo.disagreements),
        ("ЧТО МОЖЕТ ИЗМЕНИТЬ КАРТИНУ", memo.triggers),
    ]:
        if insights:
            parts.append(title + "\n" + "\n".join("• " + line(i) for i in insights))
    parts += ["ГЛАВНЫЙ ВЫВОД\n" + line(memo.conclusion), "МЫСЛЬ ДНЯ\n" + line(memo.thought)]
    limitations = [*warnings, *memo.limitations]
    if previous is None:
        limitations.append(
            "Нет сопоставимых предыдущих суток: изменение относительно вчера не определено."
        )
    if limitations:
        parts.append("Ограничения данных: " + " ".join(dict.fromkeys(limitations)))
    model = f"Аналитический этап: {s.analyst_model} (high)."
    if s.fallback_from:
        model += " Резервный режим; недоступны: " + ", ".join(s.fallback_from) + "."
    parts.append(model)
    return "\n\n".join(parts)
