"""National Sleep Foundation recommended sleep duration by age."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class Recommendation:
    min_hours: int
    max_hours: int
    age_group: str | None


# (upper age bound in months, exclusive) -> recommendation
_RANGES: tuple[tuple[int, Recommendation], ...] = (
    (4, Recommendation(14, 17, "newborn")),
    (12, Recommendation(12, 15, "infant")),
    (36, Recommendation(11, 14, "toddler")),
    (72, Recommendation(10, 13, "preschool")),
    (168, Recommendation(9, 11, "school_age")),
    (216, Recommendation(8, 10, "teen")),
    (312, Recommendation(7, 9, "young_adult")),
    (780, Recommendation(7, 9, "adult")),
)
_OLDER_ADULT = Recommendation(7, 8, "older_adult")
_UNKNOWN = Recommendation(7, 9, None)


def age_in_months(date_of_birth: date, today: date) -> int:
    months = (today.year - date_of_birth.year) * 12 + (today.month - date_of_birth.month)
    if today.day < date_of_birth.day:
        months -= 1
    return max(0, months)


def recommend(date_of_birth: date | None, today: date) -> Recommendation:
    if date_of_birth is None:
        return _UNKNOWN
    months = age_in_months(date_of_birth, today)
    for upper, recommendation in _RANGES:
        if months < upper:
            return recommendation
    return _OLDER_ADULT
