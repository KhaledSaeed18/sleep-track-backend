from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status

from app.core.errors import problem_responses
from app.modules.auth.dependencies import CurrentUser
from app.modules.tags.dependencies import TagServiceDep
from app.modules.tags.schemas import TagCreate, TagRead, TagUpdate

router = APIRouter(prefix="/tags", tags=["tags"], responses=problem_responses(401))


@router.get("", summary="List tags")
async def list_tags(user: CurrentUser, service: TagServiceDep) -> list[TagRead]:
    return await service.list_tags(user.id)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create a tag",
    responses=problem_responses(409, 422),
)
async def create_tag(data: TagCreate, user: CurrentUser, service: TagServiceDep) -> TagRead:
    """Names are unique per user, case-insensitively (409 `conflict`)."""
    return await service.create(user.id, data)


@router.patch("/{tag_id}", summary="Update a tag", responses=problem_responses(404, 409, 422))
async def update_tag(
    tag_id: uuid.UUID, data: TagUpdate, user: CurrentUser, service: TagServiceDep
) -> TagRead:
    return await service.update(user.id, tag_id, data)


@router.delete(
    "/{tag_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tag",
    responses=problem_responses(404),
)
async def delete_tag(tag_id: uuid.UUID, user: CurrentUser, service: TagServiceDep) -> Response:
    """Removes the tag from every session that used it."""
    await service.delete(user.id, tag_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
