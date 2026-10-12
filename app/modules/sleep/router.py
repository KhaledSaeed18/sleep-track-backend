from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Query, Response, status

from app.core.errors import problem_responses
from app.core.pagination import PageParamsDep, parse_sort
from app.modules.auth.dependencies import CurrentUser
from app.modules.sleep.dependencies import SessionFiltersDep, SleepServiceDep
from app.modules.sleep.repository import SORTABLE_COLUMNS
from app.modules.sleep.schemas import (
    BulkDeleteRequest,
    BulkDeleteResponse,
    SleepSessionCreate,
    SleepSessionPage,
    SleepSessionRead,
    SleepSessionUpdate,
    TimerStart,
    TimerState,
    TimerStop,
)

router = APIRouter(prefix="/sleep", tags=["sleep"], responses=problem_responses(401))

SORT_FIELDS = tuple(SORTABLE_COLUMNS)


@router.get("/sessions", summary="List sleep sessions", responses=problem_responses(422))
async def list_sessions(
    user: CurrentUser,
    service: SleepServiceDep,
    filters: SessionFiltersDep,
    page: PageParamsDep,
    sort: Annotated[
        str | None,
        Query(
            description=f"One of {', '.join(SORT_FIELDS)}; prefix with '-' for descending",
            examples=["-started_at"],
        ),
    ] = None,
) -> SleepSessionPage:
    spec = parse_sort(sort, allowed=SORT_FIELDS, default="-started_at")
    return await service.list_sessions(user, filters, spec, page)


@router.post(
    "/sessions",
    status_code=status.HTTP_201_CREATED,
    summary="Log a sleep session",
    responses=problem_responses(409, 422),
)
async def create_session(
    data: SleepSessionCreate, user: CurrentUser, service: SleepServiceDep
) -> SleepSessionRead:
    """Rules: 10 minutes to 20 hours long, not in the future, and no overlap with
    another session (409 `session_overlap`)."""
    return await service.to_read(await service.create(user, data))


@router.get(
    "/sessions/{session_id}", summary="Get a sleep session", responses=problem_responses(404)
)
async def get_session(
    session_id: uuid.UUID, user: CurrentUser, service: SleepServiceDep
) -> SleepSessionRead:
    return await service.to_read(await service.get(user, session_id))


@router.patch(
    "/sessions/{session_id}",
    summary="Update a sleep session",
    responses=problem_responses(404, 409, 422),
)
async def update_session(
    session_id: uuid.UUID, data: SleepSessionUpdate, user: CurrentUser, service: SleepServiceDep
) -> SleepSessionRead:
    return await service.to_read(await service.update(user, session_id, data))


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a sleep session",
    responses=problem_responses(404),
)
async def delete_session(
    session_id: uuid.UUID, user: CurrentUser, service: SleepServiceDep
) -> Response:
    await service.delete(user, session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/sessions/bulk-delete",
    summary="Delete up to 100 sessions",
    responses=problem_responses(422),
)
async def bulk_delete_sessions(
    data: BulkDeleteRequest, user: CurrentUser, service: SleepServiceDep
) -> BulkDeleteResponse:
    """Ids that don't exist or belong to someone else are ignored."""
    return BulkDeleteResponse(deleted=await service.bulk_delete(user, data.ids))


@router.get("/timer", summary="Get the live sleep timer")
async def get_timer(user: CurrentUser, service: SleepServiceDep) -> TimerState:
    return await service.timer_state(user)


@router.post(
    "/timer/start",
    status_code=status.HTTP_201_CREATED,
    summary="Start the sleep timer",
    responses=problem_responses(409, 422),
)
async def start_timer(
    user: CurrentUser,
    service: SleepServiceDep,
    data: Annotated[TimerStart | None, Body()] = None,
) -> SleepSessionRead:
    return await service.to_read(await service.start_timer(user, data or TimerStart()))


@router.post(
    "/timer/stop",
    summary="Stop the sleep timer",
    responses=problem_responses(409, 422),
)
async def stop_timer(
    user: CurrentUser,
    service: SleepServiceDep,
    data: Annotated[TimerStop | None, Body()] = None,
) -> SleepSessionRead:
    return await service.to_read(await service.stop_timer(user, data or TimerStop()))


@router.delete(
    "/timer",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Cancel the running timer",
    responses=problem_responses(409),
)
async def cancel_timer(user: CurrentUser, service: SleepServiceDep) -> Response:
    await service.cancel_timer(user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
