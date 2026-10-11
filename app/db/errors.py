"""Helpers for interpreting database integrity errors."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

UNIQUE_VIOLATION = "23505"
EXCLUSION_VIOLATION = "23P01"
CHECK_VIOLATION = "23514"
FOREIGN_KEY_VIOLATION = "23503"


def _driver_error(exc: IntegrityError) -> object:
    # asyncpg's exception sits behind SQLAlchemy's DBAPI adapter.
    orig = exc.orig
    return getattr(orig, "__cause__", None) or orig


def sqlstate(exc: IntegrityError) -> str | None:
    for candidate in (exc.orig, _driver_error(exc)):
        code = getattr(candidate, "sqlstate", None) or getattr(candidate, "pgcode", None)
        if code:
            return str(code)
    return None


def constraint_name(exc: IntegrityError) -> str | None:
    name = getattr(_driver_error(exc), "constraint_name", None)
    return str(name) if name else None
