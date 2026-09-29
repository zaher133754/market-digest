from __future__ import annotations

import pytest
from pydantic import ValidationError

from market_digest.reports.models import MarketMoodReport, mood_label


def _mood(index: int) -> MarketMoodReport:
    return MarketMoodReport(
        index=index,
        summary="Общее настроение",
        positive=[],
        negative=[],
        themes=[],
        expectations=[],
        disagreements=[],
        extremes=[],
        conclusion="Итог",
    )


def test_market_mood_index_is_bounded() -> None:
    with pytest.raises(ValidationError):
        _mood(11)


@pytest.mark.parametrize(
    ("score", "label"),
    [
        (1, "паника"),
        (2, "паника"),
        (3, "негатив"),
        (4, "негатив"),
        (5, "нейтральное настроение"),
        (6, "умеренный оптимизм"),
        (7, "умеренный оптимизм"),
        (8, "сильная эйфория"),
        (9, "сильная эйфория"),
        (10, "предельная эйфория"),
    ],
)
def test_mood_label_is_deterministic(score: int, label: str) -> None:
    assert mood_label(score) == label


def test_mood_label_rejects_out_of_range_values() -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        mood_label(0)
