from __future__ import annotations

from datetime import date

import pytest

from app.modules.goals.recommendations import Recommendation, age_in_months, recommend

TODAY = date(2026, 10, 12)


def months_before(today: date, months: int) -> date:
    index = today.year * 12 + (today.month - 1) - months
    return date(index // 12, index % 12 + 1, today.day)


def test_unknown_date_of_birth() -> None:
    assert recommend(None, TODAY) == Recommendation(7, 9, None)


@pytest.mark.parametrize(
    ("months", "expected"),
    [
        (0, Recommendation(14, 17, "newborn")),
        (3, Recommendation(14, 17, "newborn")),
        (4, Recommendation(12, 15, "infant")),
        (11, Recommendation(12, 15, "infant")),
        (12, Recommendation(11, 14, "toddler")),
        (35, Recommendation(11, 14, "toddler")),
        (36, Recommendation(10, 13, "preschool")),
        (71, Recommendation(10, 13, "preschool")),
        (72, Recommendation(9, 11, "school_age")),
        (167, Recommendation(9, 11, "school_age")),
        (168, Recommendation(8, 10, "teen")),
        (215, Recommendation(8, 10, "teen")),
        (216, Recommendation(7, 9, "young_adult")),
        (311, Recommendation(7, 9, "young_adult")),
        (312, Recommendation(7, 9, "adult")),
        (779, Recommendation(7, 9, "adult")),
        (780, Recommendation(7, 8, "older_adult")),
        (1200, Recommendation(7, 8, "older_adult")),
    ],
)
def test_age_bands(months: int, expected: Recommendation) -> None:
    dob = months_before(TODAY, months)
    assert age_in_months(dob, TODAY) == months
    assert recommend(dob, TODAY) == expected


def test_band_changes_on_birthday() -> None:
    eighteenth_birthday = date(2026, 10, 12)
    dob = date(2008, 10, 12)
    assert recommend(dob, date(2026, 10, 11)).age_group == "teen"
    assert recommend(dob, eighteenth_birthday).age_group == "young_adult"


@pytest.mark.parametrize(
    ("dob", "today", "expected"),
    [
        (date(2026, 1, 15), date(2026, 2, 14), 0),
        (date(2026, 1, 15), date(2026, 2, 15), 1),
        (date(2026, 1, 31), date(2026, 2, 28), 0),
        (date(2026, 1, 31), date(2026, 3, 1), 1),
        (date(2025, 12, 20), date(2026, 1, 19), 0),
        (date(2025, 12, 20), date(2026, 1, 20), 1),
        (date(2000, 2, 29), date(2001, 2, 28), 11),
        (date(2000, 2, 29), date(2001, 3, 1), 12),
        (date(1990, 5, 1), date(2026, 10, 12), 437),
    ],
)
def test_age_in_months_day_of_month(dob: date, today: date, expected: int) -> None:
    assert age_in_months(dob, today) == expected


def test_future_date_of_birth_is_clamped_to_zero() -> None:
    assert age_in_months(date(2026, 12, 1), TODAY) == 0
    assert recommend(date(2026, 12, 1), TODAY).age_group == "newborn"
