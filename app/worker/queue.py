"""Job queue abstraction used by the API to schedule background work."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Protocol

import structlog
from arq.connections import ArqRedis
from fastapi import Depends, Request

from app.worker.context import RESOURCES_KEY, JobResources

logger = structlog.get_logger(__name__)

JobFunction = Callable[..., Awaitable[Any]]


class JobQueue(Protocol):
    async def enqueue(self, job: str, /, **kwargs: Any) -> None: ...


class ArqJobQueue:
    def __init__(self, pool: ArqRedis) -> None:
        self._pool = pool

    async def enqueue(self, job: str, /, **kwargs: Any) -> None:
        await self._pool.enqueue_job(job, **kwargs)
        logger.info("job_enqueued", job=job)


class EagerJobQueue:
    """Runs jobs inline. Failures propagate so tests surface them immediately."""

    def __init__(self, functions: dict[str, JobFunction], resources: JobResources) -> None:
        self._functions = functions
        self._ctx: dict[str, Any] = {RESOURCES_KEY: resources, "job_try": 1}

    async def enqueue(self, job: str, /, **kwargs: Any) -> None:
        await self._functions[job](self._ctx, **kwargs)


def get_job_queue(request: Request) -> JobQueue:
    queue: JobQueue = request.app.state.job_queue
    return queue


JobQueueDep = Annotated[JobQueue, Depends(get_job_queue)]
