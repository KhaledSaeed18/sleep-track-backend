"""Cross-cutting FastAPI dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request

from app.core.config import Settings
from app.core.rate_limit import client_ip
from app.core.security import PasswordHasher, TokenCodec


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_token_codec(request: Request) -> TokenCodec:
    codec: TokenCodec = request.app.state.token_codec
    return codec


def get_hasher(request: Request) -> PasswordHasher:
    hasher: PasswordHasher = request.app.state.password_hasher
    return hasher


@dataclass(frozen=True, slots=True)
class ClientInfo:
    ip_address: str | None
    user_agent: str | None


def get_client_info(request: Request) -> ClientInfo:
    user_agent = request.headers.get("user-agent")
    return ClientInfo(
        ip_address=client_ip(request),
        user_agent=user_agent[:512] if user_agent else None,
    )


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
TokenCodecDep = Annotated[TokenCodec, Depends(get_token_codec)]
HasherDep = Annotated[PasswordHasher, Depends(get_hasher)]
ClientInfoDep = Annotated[ClientInfo, Depends(get_client_info)]
