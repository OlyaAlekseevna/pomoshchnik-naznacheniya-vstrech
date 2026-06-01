from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class AuthTelegramRequest(BaseModel):
    init_data: str = Field(min_length=1)


class DevLoginRequest(BaseModel):
    telegram_user_id: int = Field(gt=0)
    first_name: str | None = None
    last_name: str | None = None
    username: str | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    role: str
    telegram_user_id: int


class MeResponse(BaseModel):
    telegram_user_id: int
    role: str
    is_blocked: bool
    first_name: str | None
    last_name: str | None
    username: str | None


class BookingWeekResponse(BaseModel):
    week_offset: int
    week_start: date
    week_end: date
    can_go_prev: bool
    can_go_next: bool
    days: list[date]


class BookingSlotsResponse(BaseModel):
    date: date
    duration_minutes: int
    slots: list[str]


class CreateRequestPayload(BaseModel):
    duration_minutes: int
    slot_encoded: str
    full_name: str = Field(min_length=1, max_length=255)
    phone: str = Field(min_length=1, max_length=50)
    email: str = Field(min_length=3, max_length=255)
    meeting_goal: str = Field(min_length=1, max_length=10_000)
    personal_data_consent: bool


class UpdateGoalPayload(BaseModel):
    meeting_goal: str = Field(min_length=1, max_length=10_000)


class RejectPayload(BaseModel):
    reason: str = Field(min_length=1, max_length=10_000)


class AlternativeSlotPayload(BaseModel):
    value: str = Field(min_length=10, max_length=100)


class SettingUpdatePayload(BaseModel):
    setting_key: str = Field(min_length=1, max_length=64)
    value: str = Field(min_length=1, max_length=10_000)


class GoogleExchangePayload(BaseModel):
    code: str = Field(min_length=1, max_length=10_000)
