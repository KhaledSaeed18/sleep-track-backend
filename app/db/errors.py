"""Helpers for interpreting database errors."""

from __future__ import annotations

from sqlalchemy.exc import DBAPIError

UNIQUE_VIOLATION = "23505"
EXCLUSION_VIOLATION = "23P01"
CHECK_VIOLATION = "23514"
FOREIGN_KEY_VIOLATION = "23503"
# Text the database cannot store, e.g. a NUL character, which PostgreSQL never
# accepts in text columns or parameters.
CHARACTER_NOT_IN_REPERTOIRE = "22021"
UNTRANSLATABLE_CHARACTER = "22P05"
UNSTORABLE_TEXT_SQLSTATES = frozenset({CHARACTER_NOT_IN_REPERTOIRE, UNTRANSLATABLE_CHARACTER})


def _driver_error(exc: DBAPIError) -> object:
    # asyncpg's exception sits behind SQLAlchemy's DBAPI adapter.
    orig = exc.orig
    return getattr(orig, "__cause__", None) or orig


def sqlstate(exc: DBAPIError) -> str | None:
    for candidate in (exc.orig, _driver_error(exc)):
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code:
            return str(code)
    return None


def constraint_name(exc: DBAPIError) -> str | None:
    name = getattr(_driver_error(exc), "constraint_name", None)
    return str(name) if name else None
