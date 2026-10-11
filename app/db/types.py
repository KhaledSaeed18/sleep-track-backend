"""Reusable column types."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Enum


def str_enum[E: StrEnum](enum_cls: type[E], name: str) -> Enum:
    """Store a ``StrEnum`` as VARCHAR guarded by a CHECK constraint.

    Native PostgreSQL enums need ``ALTER TYPE`` migrations for every new value;
    a check constraint is just as safe and far easier to evolve.
    """
    return Enum(
        enum_cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=max(len(member.value) for member in enum_cls),
        values_callable=lambda members: [member.value for member in members],
    )
