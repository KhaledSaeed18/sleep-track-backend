from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.modules.users.data_transfer import (
    EXPORT_CSV_COLUMNS,
    ParsedRow,
    RowError,
    parse_datetime,
    parse_import_row,
    read_csv,
    sessions_to_csv,
)


def export_row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": "2b7c2c51-3f0c-4f0e-9d0e-6c1f0f5d2a10",
        "status": "completed",
        "started_at": "2026-03-01T23:00:00+00:00",
        "ended_at": "2026-03-02T07:00:00+00:00",
        "timezone": "UTC",
        "night_of": "2026-03-01",
        "duration_minutes": 480,
        "quality": 4,
        "mood_on_wake": None,
        "sleep_latency_minutes": 10,
        "awakenings": 1,
        "score": 88,
        "source": "manual",
        "notes": "Slept well",
        "tags": ["Exercise", "Reading"],
    }
    row.update(overrides)
    return row


def parse_export(content: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(content)))


def import_row(**values: str | None) -> dict[str, str | None]:
    row: dict[str, str | None] = {
        "started_at": "2026-03-01T23:00:00",
        "ended_at": "2026-03-02T07:00:00",
    }
    row.update(values)
    return row


# =============================================================================== export
class TestSessionsToCsv:
    def test_header_and_values(self) -> None:
        content = sessions_to_csv([export_row()])
        lines = content.splitlines()
        assert lines[0] == ",".join(EXPORT_CSV_COLUMNS)
        (row,) = parse_export(content)
        assert row == {
            "id": "2b7c2c51-3f0c-4f0e-9d0e-6c1f0f5d2a10",
            "started_at": "2026-03-01T23:00:00+00:00",
            "ended_at": "2026-03-02T07:00:00+00:00",
            "timezone": "UTC",
            "night_of": "2026-03-01",
            "duration_minutes": "480",
            "quality": "4",
            "mood_on_wake": "",
            "sleep_latency_minutes": "10",
            "awakenings": "1",
            "score": "88",
            "source": "manual",
            "notes": "Slept well",
            "tags": "Exercise;Reading",
        }

    def test_extra_keys_are_ignored(self) -> None:
        assert "status" not in sessions_to_csv([export_row(extra="value")]).splitlines()[0]

    def test_missing_keys_are_blank(self) -> None:
        (row,) = parse_export(sessions_to_csv([{"id": "abc"}]))
        assert row["id"] == "abc"
        assert row["notes"] == row["tags"] == ""

    def test_no_rows(self) -> None:
        assert sessions_to_csv([]).strip() == ",".join(EXPORT_CSV_COLUMNS)

    def test_empty_tag_list(self) -> None:
        (row,) = parse_export(sessions_to_csv([export_row(tags=[])]))
        assert row["tags"] == ""

    @pytest.mark.parametrize(
        ("notes", "expected"),
        [
            ('=HYPERLINK("http://evil")', '\'=HYPERLINK("http://evil")'),
            ("+cmd|' /C calc'!A0", "'+cmd|' /C calc'!A0"),
            ("-2+3", "'-2+3"),
            ("@SUM(A1:A2)", "'@SUM(A1:A2)"),
            ("-", "'-"),
            ("-5", "-5"),
            ("+1.5", "+1.5"),
            ("-1e3", "-1e3"),
            ("plain = text", "plain = text"),
            ("", ""),
        ],
    )
    def test_formula_injection_is_neutralised(self, notes: str, expected: str) -> None:
        (row,) = parse_export(sessions_to_csv([export_row(notes=notes)]))
        assert row["notes"] == expected

    def test_commas_quotes_and_newlines_survive(self) -> None:
        notes = 'Woke up, then "slept"\nagain'
        (row,) = parse_export(sessions_to_csv([export_row(notes=notes)]))
        assert row["notes"] == notes

    def test_tag_names_are_neutralised(self) -> None:
        (row,) = parse_export(sessions_to_csv([export_row(tags=['=HYPERLINK("http://evil")'])]))
        assert not row["tags"].startswith("=")


# =============================================================================== timestamps
class TestParseDatetime:
    def test_naive_value_uses_user_timezone(self) -> None:
        parsed = parse_datetime("2026-03-01T23:00:00", "Europe/Paris")
        assert parsed.utcoffset() == timedelta(hours=1)
        assert parsed.astimezone(UTC) == datetime(2026, 3, 1, 22, 0, tzinfo=UTC)

    def test_naive_value_during_summer_time(self) -> None:
        parsed = parse_datetime("2026-07-01 23:00", "Europe/Paris")
        assert parsed.astimezone(UTC) == datetime(2026, 7, 1, 21, 0, tzinfo=UTC)

    def test_z_suffix_is_utc(self) -> None:
        parsed = parse_datetime("2026-03-01T22:00:00Z", "Asia/Tokyo")
        assert parsed == datetime(2026, 3, 1, 22, 0, tzinfo=UTC)
        assert parsed.utcoffset() == timedelta(0)

    def test_explicit_offset_wins(self) -> None:
        parsed = parse_datetime("  2026-03-01T23:00:00+02:00 ", "UTC")
        assert parsed.astimezone(UTC) == datetime(2026, 3, 1, 21, 0, tzinfo=UTC)

    @pytest.mark.parametrize("raw", ["", "   "])
    def test_missing(self, raw: str) -> None:
        with pytest.raises(RowError, match="missing timestamp"):
            parse_datetime(raw, "UTC")

    @pytest.mark.parametrize("raw", ["yesterday", "01/03/2026 23:00", "2026-13-01T00:00:00"])
    def test_invalid(self, raw: str) -> None:
        with pytest.raises(RowError, match="use ISO 8601"):
            parse_datetime(raw, "UTC")


# =============================================================================== import rows
class TestParseImportRow:
    def test_minimal_row(self) -> None:
        parsed = parse_import_row(2, import_row(), "UTC")
        assert parsed == ParsedRow(
            row=2,
            started_at=datetime(2026, 3, 1, 23, 0, tzinfo=UTC),
            ended_at=datetime(2026, 3, 2, 7, 0, tzinfo=UTC),
            quality=None,
            notes=None,
            tags=[],
        )

    def test_full_row(self) -> None:
        parsed = parse_import_row(
            7,
            import_row(quality=" 4 ", notes="  Late dinner  ", tags="Late meal; Stress"),
            "America/New_York",
        )
        assert parsed.row == 7
        assert parsed.started_at.astimezone(UTC) == datetime(2026, 3, 2, 4, 0, tzinfo=UTC)
        assert parsed.quality == 4
        assert parsed.notes == "Late dinner"
        assert parsed.tags == ["Late meal", "Stress"]

    @pytest.mark.parametrize("field", ["started_at", "ended_at"])
    def test_missing_timestamp(self, field: str) -> None:
        with pytest.raises(RowError, match="missing timestamp"):
            parse_import_row(2, import_row(**{field: None}), "UTC")

    @pytest.mark.parametrize(
        ("raw", "expected"), [("1", 1), ("5", 5), ("4.0", 4), ("3.9", 3), ("", None), (None, None)]
    )
    def test_quality(self, raw: str | None, expected: int | None) -> None:
        assert parse_import_row(2, import_row(quality=raw), "UTC").quality == expected

    @pytest.mark.parametrize("raw", ["0", "6", "-1", "10.5"])
    def test_quality_out_of_range(self, raw: str) -> None:
        with pytest.raises(RowError, match="quality must be between 1 and 5"):
            parse_import_row(2, import_row(quality=raw), "UTC")

    @pytest.mark.parametrize("raw", ["good", "4/5", "nan"])
    def test_quality_not_a_number(self, raw: str) -> None:
        with pytest.raises(RowError, match="quality must be a number"):
            parse_import_row(2, import_row(quality=raw), "UTC")

    @pytest.mark.parametrize("raw", ["inf", "-Infinity", "1e400"])
    def test_quality_infinite_is_a_row_error(self, raw: str) -> None:
        with pytest.raises(RowError):
            parse_import_row(2, import_row(quality=raw), "UTC")

    def test_notes_length(self) -> None:
        assert parse_import_row(2, import_row(notes="n" * 2000), "UTC").notes == "n" * 2000
        with pytest.raises(RowError, match="at most 2000 characters"):
            parse_import_row(2, import_row(notes="n" * 2001), "UTC")

    def test_blank_notes_become_none(self) -> None:
        assert parse_import_row(2, import_row(notes="   "), "UTC").notes is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Caffeine", ["Caffeine"]),
            ("Caffeine;Stress", ["Caffeine", "Stress"]),
            ("Caffeine|Stress", ["Caffeine", "Stress"]),
            (" Caffeine | Stress ; Reading ", ["Caffeine", "Stress", "Reading"]),
            ("Caffeine;Stress|Caffeine", ["Caffeine", "Stress"]),
            (";|; ", []),
            ("", []),
            (None, []),
        ],
    )
    def test_tags(self, raw: str | None, expected: list[str]) -> None:
        assert parse_import_row(2, import_row(tags=raw), "UTC").tags == expected

    def test_long_tag_names_are_truncated(self) -> None:
        parsed = parse_import_row(2, import_row(tags="x" * 60 + ";" + "x" * 55), "UTC")
        assert parsed.tags == ["x" * 50]

    def test_tags_deduplicate_case_insensitively(self) -> None:
        assert parse_import_row(2, import_row(tags="Coffee;coffee"), "UTC").tags == ["Coffee"]


# =============================================================================== reading
class TestReadCsv:
    def test_header_is_normalised(self) -> None:
        content = " Started_At ,ENDED_AT,Quality\n2026-03-01T23:00:00,2026-03-02T07:00:00,4\n"
        header, rows = read_csv(content)
        assert header == ["started_at", "ended_at", "quality"]
        assert rows == [
            {"started_at": "2026-03-01T23:00:00", "ended_at": "2026-03-02T07:00:00", "quality": "4"}
        ]

    def test_empty_content(self) -> None:
        assert read_csv("") == ([], [])

    def test_header_only(self) -> None:
        assert read_csv("started_at,ended_at\n") == (["started_at", "ended_at"], [])

    def test_short_rows_fill_with_none(self) -> None:
        _, rows = read_csv("started_at,ended_at,notes\n2026-03-01T23:00:00,2026-03-02T07:00:00\n")
        assert rows[0]["notes"] is None

    def test_export_can_be_reimported(self) -> None:
        header, rows = read_csv(sessions_to_csv([export_row()]))
        assert header == list(EXPORT_CSV_COLUMNS)
        parsed = parse_import_row(2, rows[0], "UTC")
        assert parsed.quality == 4
        assert parsed.tags == ["Exercise", "Reading"]
        assert parsed.ended_at - parsed.started_at == timedelta(hours=8)
