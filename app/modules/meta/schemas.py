from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class FeatureFlags(BaseModel):
    google_oauth: bool
    demo_mode: bool
    email_verification_required: bool
    registration_open: bool


class MetaResponse(BaseModel):
    name: str = Field(examples=["SleepTrack API"])
    version: str = Field(examples=["2.0.0"])
    environment: str = Field(examples=["development"])
    features: FeatureFlags


class HealthStatus(BaseModel):
    status: Literal["ok", "degraded"]


class ReadinessCheck(BaseModel):
    status: Literal["ok", "error"]
    latency_ms: float | None = None
    error: str | None = None


class ReadinessStatus(HealthStatus):
    checks: dict[str, ReadinessCheck]
