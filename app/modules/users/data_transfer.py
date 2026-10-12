"""CSV/JSON export and CSV import of sleep data."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.core.timeutils import get_zone

EXPORT_CSV_COLUMNS = (
    "id",
    "started_at",
    "ended_at",
    "timezone",
    "night_of",
    "duration_minutes",
    "quality",
    "mood_on_wake",
    "sleep_latency_minutes",
    "awakenings",
    "score",
    "source",
    "notes",
    "tags",
)
IMPORT_REQUIRED_COLUMNS = ("started_at", "ended_at")
IMPORT_COLUMNS = ("started_at", "ended_at", "quality", "notes", "tags")
TAG_SEPARATORS = (";", "|")


def sessions_to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_CSV_COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({key: _csv_value(row.get(key)) for key in EXPORT_CSV_COLUMNS})
    return buffer.getvalue()


def _csv_value(value: object) -> object:
    if isinstance(value, list):
        # Joined first: a spreadsheet only evaluates what the cell starts with.
        value = ";".join(str(v) for v in value)
    if value is None:
        return ""
    if isinstance(value, str) and value[:1] in {"=", "+", "-", "@"} and not _looks_numeric(value):
        # Neutralise spreadsheet formula injection in user-controlled text.
        return f"'{value}"
    return value


def _looks_numeric(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


@dataclass(slots=True)
class ParsedRow:
    row: int
    started_at: datetime
    ended_at: datetime
    quality: int | None
    notes: str | None
    tags: list[str] = field(default_factory=list)


class RowError(ValueError):
    pass


def parse_datetime(raw: str, tz: str) -> datetime:
    value = raw.strip()
    if not value:
        raise RowError("missing timestamp")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise RowError(f"invalid timestamp '{value}' (use ISO 8601)") from exc
    if parsed.tzinfo is None:
        # Naive values are interpreted in the user's timezone.
        parsed = parsed.replace(tzinfo=get_zone(tz))
    return parsed


def parse_import_row(index: int, row: dict[str, str | None], tz: str) -> ParsedRow:
    started_at = parse_datetime(row.get("started_at") or "", tz)
    ended_at = parse_datetime(row.get("ended_at") or "", tz)

    quality_raw = (row.get("quality") or "").strip()
    quality: int | None = None
    if quality_raw:
        try:
            quality = int(float(quality_raw))
        except (ValueError, OverflowError) as exc:
            raise RowError(
                f"quality must be a number between 1 and 5, got '{quality_raw}'"
            ) from exc
        if not 1 <= quality <= 5:
            raise RowError("quality must be between 1 and 5")

    notes = (row.get("notes") or "").strip() or None
    if notes and len(notes) > 2000:
        raise RowError("notes must be at most 2000 characters")

    tags_raw = row.get("tags") or ""
    for separator in TAG_SEPARATORS[1:]:
        tags_raw = tags_raw.replace(separator, TAG_SEPARATORS[0])
    # Tag names are unique case-insensitively, so de-duplicate the same way.
    unique: dict[str, str] = {}
    for raw_tag in tags_raw.split(TAG_SEPARATORS[0]):
        name = raw_tag.strip()[:50]
        if name:
            unique.setdefault(name.casefold(), name)
    tags = list(unique.values())
    return ParsedRow(index, started_at, ended_at, quality, notes, tags)


def read_csv(content: str) -> tuple[list[str], list[dict[str, str | None]]]:
    reader = csv.DictReader(io.StringIO(content))
    header = [name.strip().lower() for name in (reader.fieldnames or [])]
    reader.fieldnames = header
    return header, list(reader)
