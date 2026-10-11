"""Email provider abstraction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to: str
    subject: str
    html: str
    text: str
    headers: dict[str, str] = field(default_factory=dict)


class EmailProvider(Protocol):
    name: str

    async def send(self, message: EmailMessage) -> None: ...
