"""Offset pagination and sort parsing shared by list endpoints."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Annotated, Self

from fastapi import Depends, Query
from pydantic import BaseModel, Field

from app.core.errors import ValidationFailedError

MAX_PAGE_SIZE = 100


class Page[T](BaseModel):
    items: list[T]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=MAX_PAGE_SIZE)
    pages: int = Field(ge=0)

    @classmethod
    def build(cls, items: Sequence[T], *, total: int, params: PageParams) -> Self:
        return cls(
            items=list(items),
            total=total,
            page=params.page,
            page_size=params.page_size,
            pages=math.ceil(total / params.page_size) if total else 0,
        )


@dataclass(frozen=True, slots=True)
class PageParams:
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


def _page_params(
    page: Annotated[int, Query(ge=1, le=10_000, description="1-based page number")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Items per page (max 100)")
    ] = 20,
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


PageParamsDep = Annotated[PageParams, Depends(_page_params)]


@dataclass(frozen=True, slots=True)
class SortSpec:
    field: str
    descending: bool


def parse_sort(value: str | None, *, allowed: Sequence[str], default: str) -> SortSpec:
    raw = (value or default).strip()
    descending = raw.startswith("-")
    field = raw.lstrip("-+")
    if field not in allowed:
        raise ValidationFailedError.for_field(
            "sort", f"must be one of {', '.join(allowed)} (prefix with '-' for descending)"
        )
    return SortSpec(field=field, descending=descending)
