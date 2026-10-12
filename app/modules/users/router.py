from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from app.core.deps import ClientInfoDep, HasherDep, SettingsDep
from app.core.errors import ValidationFailedError, problem_responses
from app.core.pagination import PageParamsDep
from app.core.rate_limit import RateLimiterDep
from app.db.session import DbSession
from app.modules.analytics.cache import AnalyticsCacheDep
from app.modules.auth.cookies import clear_auth_cookies
from app.modules.auth.dependencies import AccessClaims, CurrentUser, RevocationStoreDep
from app.modules.users.schemas import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    DeviceSession,
    ImportResult,
    Preferences,
    PreferencesUpdate,
    UserRead,
    UserUpdate,
)
from app.modules.users.service import AuditLogPage, UserService

router = APIRouter(prefix="/users/me", tags=["users"], responses=problem_responses(401))

CSV_MEDIA_TYPES = {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}


def get_user_service(
    session: DbSession,
    hasher: HasherDep,
    revocations: RevocationStoreDep,
    cache: AnalyticsCacheDep,
    limiter: RateLimiterDep,
) -> UserService:
    return UserService(
        session, hasher=hasher, revocations=revocations, cache=cache, limiter=limiter
    )


UserServiceDep = Annotated[UserService, Depends(get_user_service)]


@router.get("", summary="Get the current user")
async def get_me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.patch("", summary="Update profile", responses=problem_responses(422))
async def update_me(data: UserUpdate, user: CurrentUser, service: UserServiceDep) -> UserRead:
    return await service.update_profile(user, data)


@router.patch("/preferences", summary="Update preferences", responses=problem_responses(422))
async def update_preferences(
    data: PreferencesUpdate, user: CurrentUser, service: UserServiceDep
) -> Preferences:
    return await service.update_preferences(user, data)


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change or set the password",
    responses=problem_responses(403, 422, 429),
)
async def change_password(
    data: ChangePasswordRequest,
    user: CurrentUser,
    claims: AccessClaims,
    client: ClientInfoDep,
    service: UserServiceDep,
) -> Response:
    """Signs out all other devices. Accounts created with OAuth can set a first
    password with `current_password: null`."""
    await service.change_password(user, data, claims, client)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/sessions", summary="List signed-in devices")
async def list_sessions(
    user: CurrentUser, claims: AccessClaims, service: UserServiceDep
) -> list[DeviceSession]:
    return await service.list_devices(user, claims)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out a device",
    responses=problem_responses(404),
)
async def revoke_session(
    session_id: uuid.UUID, user: CurrentUser, client: ClientInfoDep, service: UserServiceDep
) -> Response:
    await service.revoke_device(user, session_id, client)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit-log", summary="Security events for this account")
async def audit_log(
    user: CurrentUser, page: PageParamsDep, service: UserServiceDep
) -> AuditLogPage:
    return await service.audit_log(user, page)


@router.get(
    "/export",
    summary="Download your data",
    response_class=Response,
    responses={
        200: {
            "description": "File download",
            "content": {
                "application/json": {"schema": {"type": "object"}},
                "text/csv": {"schema": {"type": "string"}},
            },
        }
    },
)
async def export_data(
    user: CurrentUser,
    client: ClientInfoDep,
    service: UserServiceDep,
    format: Annotated[Literal["json", "csv"], Query()] = "json",  # noqa: A002 - contract name
) -> Response:
    body, media_type, filename = await service.export(user, format, client)
    return Response(
        content=body,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@router.post(
    "/import",
    summary="Import sessions from CSV",
    responses=problem_responses(413, 422),
)
async def import_data(
    user: CurrentUser,
    client: ClientInfoDep,
    service: UserServiceDep,
    settings: SettingsDep,
    file: Annotated[
        UploadFile, File(description="CSV with header started_at,ended_at,quality,notes,tags")
    ],
) -> ImportResult:
    """Timestamps are ISO 8601 (naive values use your timezone); `tags` are separated
    by `;` and created when missing. Overlapping or invalid rows are reported and skipped."""
    if file.content_type and file.content_type.split(";")[0] not in CSV_MEDIA_TYPES:
        raise ValidationFailedError.for_field("file", "must be a CSV file")
    raw = await file.read()
    try:
        content = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValidationFailedError.for_field("file", "must be UTF-8 encoded") from exc
    return await service.import_csv(user, content, max_rows=settings.max_import_rows, client=client)


@router.delete(
    "",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete the account and all data",
    responses=problem_responses(422, 429),
)
async def delete_me(
    data: DeleteAccountRequest, user: CurrentUser, service: UserServiceDep, settings: SettingsDep
) -> Response:
    """Requires `confirm: "DELETE"` and, for accounts with a password, the password."""
    await service.delete_account(user, data)
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    clear_auth_cookies(response, settings)
    return response
