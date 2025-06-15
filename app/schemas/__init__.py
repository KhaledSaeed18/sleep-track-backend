from pydantic import BaseModel, EmailStr
from datetime import datetime, date
from typing import Optional

# User schemas
class UserCreate(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str
    date_of_birth: date
    password: str


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    email: str
    first_name: str
    last_name: str
    date_of_birth: date
    created_at: datetime

    class Config:
        from_attributes = True


# Sleep session schemas
class SleepSessionCreate(BaseModel):
    sleep_start: datetime
    sleep_end: datetime
    quality_rating: Optional[int] = None


class SleepSessionResponse(BaseModel):
    id: int
    sleep_start: datetime
    sleep_end: datetime
    quality_rating: Optional[int] = None
    created_at: datetime

    class Config:
        from_attributes = True


# Token schemas
class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserResponse


class TokenData(BaseModel):
    user_id: Optional[int] = None


# Sleep statistics schema
class SleepStats(BaseModel):
    total_sessions: int
    average_sleep_hours: float
    average_quality_rating: Optional[float] = None
    total_sleep_time_minutes: int


# Weekly summary schema
class WeeklySummary(BaseModel):
    week_start: date
    week_end: date
    total_sessions: int
    total_sleep_hours: float
    average_sleep_hours: float
    average_bedtime: str
    average_wake_time: str
    average_quality_rating: Optional[float] = None
    daily_breakdown: list[dict]
    sleep_consistency_score: float
    quality_trend: str  # "improving", "declining", "stable"


# User update schemas
class UserUpdate(BaseModel):
    first_name: str
    last_name: str
    date_of_birth: date


class PasswordReset(BaseModel):
    old_password: str
    new_password: str
