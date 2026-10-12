from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.deps import ClientInfoDep
from app.core.errors import problem_responses
from app.core.pagination import PageParamsDep
from app.db.session import DbSession
from app.modules.admin.schemas import AdminStats, AdminUser, AdminUserPage, AdminUserUpdate
from app.modules.admin.service import AdminService
from app.modules.auth.dependencies import CurrentAdmin, RevocationStoreDep, require_admin
from app.modules.users.models import UserRole

router = APIRouter(
    prefix="/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin)],
    responses=problem_responses(401, 403),
)


def get_admin_service(session: DbSession, revocations: RevocationStoreDep) -> AdminService:
    return AdminService(session, revocations)


AdminServiceDep = Annotated[AdminService, Depends(get_admin_service)]


@router.get("/stats", summary="Platform statistics")
async def get_stats(service: AdminServiceDep) -> AdminStats:
    return await service.stats()


@router.get("/users", summary="List users", responses=problem_responses(422))
async def list_users(
    service: AdminServiceDep,
    page: PageParamsDep,
    q: Annotated[str | None, Query(max_length=100, description="Search email or name")] = None,
    role: UserRole | None = None,
    is_active: bool | None = None,
) -> AdminUserPage:
    return await service.list_users(page, query=q, role=role, is_active=is_active)


@router.patch(
    "/users/{user_id}",
    summary="Change a user's role or status",
    responses=problem_responses(404, 422),
)
async def update_user(
    user_id: uuid.UUID,
    data: AdminUserUpdate,
    admin: CurrentAdmin,
    client: ClientInfoDep,
    service: AdminServiceDep,
) -> AdminUser:
    """Disabling a user signs them out everywhere. Admins cannot disable or demote themselves."""
    return await service.update_user(admin, user_id, data, client)
