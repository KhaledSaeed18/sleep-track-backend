from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.users.schemas import (
    OptionalLastName,
    Password,
    PersonName,
    Timezone,
    UserRead,
)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "khaled@example.com",
                "password": "SleepWell42",
                "first_name": "Khaled",
                "last_name": "Saeed",
                "timezone": "Asia/Beirut",
            }
        }
    )

    email: EmailStr = Field(max_length=320)
    password: Password
    first_name: PersonName
    last_name: OptionalLastName = ""
    timezone: Timezone | None = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "email": "demo@sleeptrack.app",
                "password": "DemoPass123",
                "remember_me": True,
            }
        }
    )

    email: EmailStr = Field(max_length=320)
    password: str = Field(min_length=1, max_length=128)
    remember_me: bool = False


class DemoRequest(BaseModel):
    timezone: Timezone | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"  # noqa: S105 - OAuth token type, not a secret
    expires_in: int = Field(description="Access token lifetime in seconds", examples=[900])
    user: UserRead


class TokenRequest(BaseModel):
    token: str = Field(min_length=10, max_length=256)


class EmailRequest(BaseModel):
    email: EmailStr = Field(max_length=320)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10, max_length=256)
    password: Password


class MessageResponse(BaseModel):
    message: str


class OAuthProviderInfo(BaseModel):
    id: str = Field(examples=["google"])
    name: str = Field(examples=["Google"])
    enabled: bool
