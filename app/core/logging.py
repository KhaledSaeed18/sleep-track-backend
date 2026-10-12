"""Structured logging: pretty console output in development, JSON elsewhere."""

from __future__ import annotations

import logging
import sys
from typing import Any

import orjson
import structlog
from structlog.types import EventDict, Processor, WrappedLogger

from app.core.config import Settings
from app.core.context import get_request_id

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "new_password",
        "current_password",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "secret",
        "code_verifier",
    }
)


def _add_request_id(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    request_id = get_request_id()
    if request_id and "request_id" not in event_dict:
        event_dict["request_id"] = request_id
    return event_dict


def _redact(_: WrappedLogger, __: str, event_dict: EventDict) -> EventDict:
    for key in event_dict.keys() & _SENSITIVE_KEYS:
        event_dict[key] = "[redacted]"
    # Uvicorn attaches an ANSI-coloured duplicate of every message.
    event_dict.pop("color_message", None)
    return event_dict


def _orjson_dumps(obj: Any, **_: Any) -> str:
    return orjson.dumps(obj, default=str).decode()


def configure_logging(settings: Settings) -> None:
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_request_id,
        _redact,
        structlog.stdlib.ExtraAdder(),
    ]
    renderer: Processor
    if settings.effective_log_format == "json":
        shared.append(structlog.processors.format_exc_info)
        renderer = structlog.processors.JSONRenderer(serializer=_orjson_dumps)
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=sys.stderr.isatty())

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared,
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(settings.log_level)

    # Uvicorn's access log is replaced by our own structured access log, and
    # arq's CLI installs its own handler; route both through ours instead.
    for name in ("uvicorn", "uvicorn.error", "arq"):
        logging.getLogger(name).handlers = []
        logging.getLogger(name).propagate = True
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.database_echo else logging.WARNING
    )
