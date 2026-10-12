from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.core import timeutils
from app.core.timeutils import (
    daterange,
    format_hhmm,
    is_valid_timezone,
    local_today,
    minutes_of_day,
    night_of,
    parse_hhmm,
    to_local,
    validate_timezone,
)
from tests.unit.helpers import utc


class TestNightOf:
    @pytest.mark.parametrize(
        ("instant", "expected"),
        [
            (utc(2026, 3, 2, 0, 0), date(2026, 3, 1)),
            (utc(2026, 3, 2, 11, 59), date(2026, 3, 1)),
            (utc(2026, 3, 2, 12, 0), date(2026, 3, 2)),
            (utc(2026, 3, 2, 23, 59), date(2026, 3, 2)),
        ],
    )
    def test_noon_boundary_in_utc(self, instant: datetime, expected: date) -> None:
        assert night_of(instant, "UTC") == expected

    @pytest.mark.parametrize(
        ("instant", "expected"),
        [
            (utc(2026, 3, 1, 2, 59), date(2026, 2, 28)),  # 11:59 JST
            (utc(2026, 3, 1, 3, 0), date(2026, 3, 1)),  # 12:00 JST
            (utc(2026, 3, 1, 15, 0), date(2026, 3, 1)),  # 00:00 JST on March 2
        ],
    )
    def test_zone_east_of_utc(self, instant: datetime, expected: date) -> None:
        assert night_of(instant, "Asia/Tokyo") == expected

    def test_boundary_follows_daylight_saving_shift(self) -> None:
        # New York moves from UTC-5 to UTC-4 on 2026-03-08, so local noon moves
        # from 17:00 UTC to 16:00 UTC.
        tz = "America/New_York"
        assert night_of(utc(2026, 3, 7, 16, 59), tz) == date(2026, 3, 6)
        assert night_of(utc(2026, 3, 7, 17, 0), tz) == date(2026, 3, 7)
        assert night_of(utc(2026, 3, 8, 15, 59), tz) == date(2026, 3, 7)
        assert night_of(utc(2026, 3, 8, 16, 0), tz) == date(2026, 3, 8)

    def test_night_spanning_fall_back(self) -> None:
        # 01:30 occurs twice in London on 2026-10-25; both belong to the night of the 24th.
        tz = "Europe/London"
        assert night_of(utc(2026, 10, 25, 0, 30), tz) == date(2026, 10, 24)
        assert night_of(utc(2026, 10, 25, 1, 30), tz) == date(2026, 10, 24)
        assert night_of(utc(2026, 10, 25, 12, 0), tz) == date(2026, 10, 25)

    def test_accepts_non_utc_aware_input(self) -> None:
        instant = datetime(2026, 3, 2, 1, 0, tzinfo=UTC).astimezone(
            timeutils.get_zone("Asia/Dubai")
        )
        assert night_of(instant, "UTC") == date(2026, 3, 1)


class TestFormatHHMM:
    @pytest.mark.parametrize(
        ("minutes", "expected"),
        [
            (0, "00:00"),
            (5, "00:05"),
            (75.4, "01:15"),
            (75.6, "01:16"),
            (1439, "23:59"),
            (1439.6, "00:00"),
            (1440, "00:00"),
            (1500, "01:00"),
            (-30, "23:30"),
        ],
    )
    def test_formats_and_wraps(self, minutes: float, expected: str) -> None:
        assert format_hhmm(minutes) == expected

    def test_none(self) -> None:
        assert format_hhmm(None) is None


class TestParseHHMM:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [("07:05", time(7, 5)), ("23:59", time(23, 59)), ("0:00", time(0, 0))],
    )
    def test_valid(self, value: str, expected: time) -> None:
        assert parse_hhmm(value) == expected

    @pytest.mark.parametrize("value", ["24:00", "12:60", "abc", "12", ""])
    def test_invalid(self, value: str) -> None:
        with pytest.raises(ValueError):  # noqa: PT011 - both int() and time() raise plain ValueError
            parse_hhmm(value)


class TestDaterange:
    def test_inclusive(self) -> None:
        assert daterange(date(2026, 2, 27), date(2026, 3, 2)) == [
            date(2026, 2, 27),
            date(2026, 2, 28),
            date(2026, 3, 1),
            date(2026, 3, 2),
        ]

    def test_single_day(self) -> None:
        assert daterange(date(2026, 3, 1), date(2026, 3, 1)) == [date(2026, 3, 1)]

    def test_reversed_range_is_empty(self) -> None:
        assert daterange(date(2026, 3, 2), date(2026, 3, 1)) == []


class TestTimezones:
    @pytest.mark.parametrize("name", ["UTC", "Europe/Paris", "Asia/Beirut", "America/New_York"])
    def test_valid(self, name: str) -> None:
        assert is_valid_timezone(name)
        assert validate_timezone(name) == name

    @pytest.mark.parametrize("name", ["Mars/Olympus", "", "../etc/passwd", "Europe/", "/UTC"])
    def test_invalid(self, name: str) -> None:
        assert not is_valid_timezone(name)
        with pytest.raises(ValueError, match="unknown timezone"):
            validate_timezone(name)

    def test_to_local(self) -> None:
        local = to_local(utc(2026, 7, 1, 12, 0), "Asia/Beirut")
        assert local.utcoffset() == timedelta(hours=3)
        assert (local.hour, local.minute) == (15, 0)


def test_minutes_of_day() -> None:
    assert minutes_of_day(time(0, 0)) == 0
    assert minutes_of_day(time(23, 30, 30)) == 23 * 60 + 30.5
    assert minutes_of_day(datetime(2026, 1, 1, 7, 15, tzinfo=UTC)) == 435


def test_local_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(timeutils, "utcnow", lambda: utc(2026, 10, 12, 22, 30))
    assert local_today("UTC") == date(2026, 10, 12)
    assert local_today("Asia/Tokyo") == date(2026, 10, 13)
    assert local_today("America/Los_Angeles") == date(2026, 10, 12)


def test_utcnow_is_aware() -> None:
    now = timeutils.utcnow()
    assert now.tzinfo is UTC
