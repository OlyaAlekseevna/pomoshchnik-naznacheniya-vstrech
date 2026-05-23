from __future__ import annotations

import re
from datetime import UTC, date, datetime, time

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.enums import RequestChangedByRole, RequestStatus
from app.db.models import ConsultationRequest, RequestStatusHistory, ScheduleSettings, User
from app.db.repositories import (
    add_forbidden_date,
    add_forbidden_period,
    append_request_status_history,
    create_admin_audit_log,
    get_request_by_id,
    get_schedule_settings,
    get_user_by_id,
    list_request_status_history,
    list_requests_for_admin,
    set_user_blocked,
    update_schedule_settings,
)
from app.domain.exceptions import BusinessRuleViolation

ALTERNATIVE_REJECTION_REASON = "Предложу другой слот"

EDITABLE_BY_ADMIN_STATUSES = {
    RequestStatus.DRAFT,
    RequestStatus.PENDING_APPROVAL,
    RequestStatus.UPDATED_BY_USER,
}

WEEKDAY_VALUES = {
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
}

_HOURS_REGEX = re.compile(r"^(?P<start>\d{2}:\d{2})-(?P<end>\d{2}:\d{2})$")
_ALT_SLOT_REGEX = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2}) (?P<start>\d{2}:\d{2})-(?P<end>\d{2}:\d{2})$"
)
_PERIOD_REGEX = re.compile(
    r"^(?P<start_day>\d{4}-\d{2}-\d{2}) (?P<start_time>\d{2}:\d{2}) "
    r"- (?P<end_day>\d{4}-\d{2}-\d{2}) (?P<end_time>\d{2}:\d{2})$"
)


def is_admin_telegram_id(telegram_user_id: int | None, settings: Settings) -> bool:
    return telegram_user_id is not None and settings.telegram_admin_id == telegram_user_id


def _ensure_request_editable_for_admin(request: ConsultationRequest, action: str) -> None:
    if request.status not in EDITABLE_BY_ADMIN_STATUSES:
        raise BusinessRuleViolation(
            f"Action '{action}' is not allowed for request status '{request.status.value}'."
        )


def _time_from_hhmm(raw_value: str) -> time:
    return datetime.strptime(raw_value, "%H:%M").time()


def parse_alternative_slot(raw_value: str) -> tuple[date, time, time]:
    match = _ALT_SLOT_REGEX.fullmatch(raw_value.strip())
    if match is None:
        raise ValueError("Alternative slot format must be YYYY-MM-DD HH:MM-HH:MM.")
    day = date.fromisoformat(match.group("day"))
    start = _time_from_hhmm(match.group("start"))
    end = _time_from_hhmm(match.group("end"))
    if end <= start:
        raise ValueError("Alternative slot end time must be greater than start time.")
    return day, start, end


def parse_forbidden_period(raw_value: str) -> tuple[datetime, datetime]:
    match = _PERIOD_REGEX.fullmatch(raw_value.strip())
    if match is None:
        raise ValueError(
            "Forbidden period format must be YYYY-MM-DD HH:MM - YYYY-MM-DD HH:MM."
        )
    start_at = datetime.fromisoformat(
        f"{match.group('start_day')}T{match.group('start_time')}"
    ).replace(tzinfo=UTC)
    end_at = datetime.fromisoformat(
        f"{match.group('end_day')}T{match.group('end_time')}"
    ).replace(tzinfo=UTC)
    if end_at <= start_at:
        raise ValueError("Forbidden period end must be later than start.")
    return start_at, end_at


def build_request_card(request: ConsultationRequest, user: User | None) -> str:
    user_blocked = user.is_blocked if user is not None else False
    user_line = (
        f"User #{user.id} | tg={user.telegram_user_id} | blocked={user_blocked}"
        if user is not None
        else "User: unknown"
    )

    alternative = ""
    if (
        request.alternative_date
        and request.alternative_start_time
        and request.alternative_end_time
    ):
        alternative = (
            "\nAlternative slot: "
            f"{request.alternative_date.isoformat()} "
            f"{request.alternative_start_time.strftime('%H:%M')}"
            f"-{request.alternative_end_time.strftime('%H:%M')}"
        )

    rejection = ""
    if request.rejection_reason:
        rejection = f"\nRejection reason: {request.rejection_reason}"

    return (
        f"Request #{request.id}\n"
        f"{user_line}\n"
        f"Date: {request.meeting_date.isoformat()} "
        f"{request.start_time.strftime('%H:%M')}-{request.end_time.strftime('%H:%M')}\n"
        f"Duration: {request.duration_minutes} min\n"
        f"Status: {request.status.value}\n"
        f"Name: {request.full_name}\n"
        f"Phone: {request.phone}\n"
        f"Email: {request.email}\n"
        f"Goal: {request.meeting_goal}"
        f"{rejection}"
        f"{alternative}"
    )


def build_history_text(request_id: int, history_items: list[RequestStatusHistory]) -> str:
    if not history_items:
        return f"No status history for request #{request_id}."

    lines = [f"History for request #{request_id}:"]
    for item in history_items:
        lines.append(
            f"- {item.created_at.isoformat()} | {item.status.value} | "
            f"{item.changed_by_role.value} | {item.comment or '-'}"
        )
    return "\n".join(lines)


def build_settings_summary(settings: ScheduleSettings) -> str:
    return (
        "Current schedule settings:\n"
        f"- timezone: {settings.timezone}\n"
        f"- working_days: {','.join(settings.working_days)}\n"
        f"- workday: {settings.workday_start.strftime('%H:%M')}"
        f"-{settings.workday_end.strftime('%H:%M')}\n"
        f"- durations: {','.join(str(item) for item in settings.available_durations_minutes)}\n"
        f"- min_notice_minutes: {settings.min_notice_minutes}\n"
        f"- buffer_minutes: {settings.buffer_minutes}\n"
        f"- max_consultations_per_day: {settings.max_consultations_per_day}\n"
        f"- booking_horizon_days: {settings.booking_horizon_days}\n"
        "- template.new_request_admin: "
        f"{settings.notification_templates.get('new_request_admin', '')}"
    )


async def get_requests_for_admin(
    session: AsyncSession,
    limit: int = 20,
) -> list[ConsultationRequest]:
    return await list_requests_for_admin(session=session, limit=limit)


async def approve_request(
    session: AsyncSession,
    request_id: int,
    admin_telegram_id: int,
) -> ConsultationRequest:
    request = await get_request_by_id(session, request_id=request_id)
    if request is None:
        raise LookupError("Request not found.")

    _ensure_request_editable_for_admin(request, action="approve")
    request.status = RequestStatus.APPROVED
    request.rejection_reason = None
    request.alternative_date = None
    request.alternative_start_time = None
    request.alternative_end_time = None

    await append_request_status_history(
        session=session,
        request_id=request.id,
        status=RequestStatus.APPROVED,
        changed_by_role=RequestChangedByRole.ADMIN,
        changed_by_telegram_id=admin_telegram_id,
        comment="approved_by_admin",
    )
    await create_admin_audit_log(
        session=session,
        admin_telegram_id=admin_telegram_id,
        action_type="request_approved",
        request_id=request.id,
        target_user_id=request.user_id,
    )
    await session.flush()
    return request


async def reject_request(
    session: AsyncSession,
    request_id: int,
    admin_telegram_id: int,
    reason: str,
) -> ConsultationRequest:
    request = await get_request_by_id(session, request_id=request_id)
    if request is None:
        raise LookupError("Request not found.")

    _ensure_request_editable_for_admin(request, action="reject")
    request.status = RequestStatus.REJECTED
    request.rejection_reason = reason

    await append_request_status_history(
        session=session,
        request_id=request.id,
        status=RequestStatus.REJECTED,
        changed_by_role=RequestChangedByRole.ADMIN,
        changed_by_telegram_id=admin_telegram_id,
        comment="rejected_by_admin",
    )
    await create_admin_audit_log(
        session=session,
        admin_telegram_id=admin_telegram_id,
        action_type="request_rejected",
        request_id=request.id,
        target_user_id=request.user_id,
        payload={"reason": reason},
    )
    await session.flush()
    return request


async def reject_with_alternative_slot(
    session: AsyncSession,
    request_id: int,
    admin_telegram_id: int,
    alternative_date: date,
    alternative_start_time: time,
    alternative_end_time: time,
) -> ConsultationRequest:
    request = await get_request_by_id(session, request_id=request_id)
    if request is None:
        raise LookupError("Request not found.")

    _ensure_request_editable_for_admin(request, action="reject_with_alternative")
    request.status = RequestStatus.REJECTED
    request.rejection_reason = ALTERNATIVE_REJECTION_REASON
    request.alternative_date = alternative_date
    request.alternative_start_time = alternative_start_time
    request.alternative_end_time = alternative_end_time

    await append_request_status_history(
        session=session,
        request_id=request.id,
        status=RequestStatus.REJECTED,
        changed_by_role=RequestChangedByRole.ADMIN,
        changed_by_telegram_id=admin_telegram_id,
        comment=(
            "alternative_slot_offered:"
            f"{alternative_date.isoformat()} "
            f"{alternative_start_time.strftime('%H:%M')}"
            f"-{alternative_end_time.strftime('%H:%M')}"
        ),
    )
    await create_admin_audit_log(
        session=session,
        admin_telegram_id=admin_telegram_id,
        action_type="alternative_slot_offered",
        request_id=request.id,
        target_user_id=request.user_id,
        payload={
            "alternative_date": alternative_date.isoformat(),
            "alternative_start_time": alternative_start_time.strftime("%H:%M"),
            "alternative_end_time": alternative_end_time.strftime("%H:%M"),
        },
    )
    await session.flush()
    return request


async def get_request_history(
    session: AsyncSession,
    request_id: int,
) -> list[RequestStatusHistory]:
    return await list_request_status_history(session=session, request_id=request_id)


async def toggle_user_block(
    session: AsyncSession,
    request_id: int,
    admin_telegram_id: int,
    blocked: bool,
) -> User:
    request = await get_request_by_id(session, request_id=request_id)
    if request is None:
        raise LookupError("Request not found.")

    user = await set_user_blocked(session=session, user_id=request.user_id, blocked=blocked)
    await create_admin_audit_log(
        session=session,
        admin_telegram_id=admin_telegram_id,
        action_type="user_blocked" if blocked else "user_unblocked",
        request_id=request.id,
        target_user_id=user.id,
    )
    await session.flush()
    return user


async def manual_create_meeting_for_user(
    session: AsyncSession,
    request_id: int,
    admin_telegram_id: int,
) -> ConsultationRequest:
    request = await get_request_by_id(session, request_id=request_id)
    if request is None:
        raise LookupError("Request not found.")

    request.status = RequestStatus.APPROVED
    request.rejection_reason = None
    await append_request_status_history(
        session=session,
        request_id=request.id,
        status=RequestStatus.APPROVED,
        changed_by_role=RequestChangedByRole.ADMIN,
        changed_by_telegram_id=admin_telegram_id,
        comment="manual_meeting_created_by_admin",
    )
    await create_admin_audit_log(
        session=session,
        admin_telegram_id=admin_telegram_id,
        action_type="manual_meeting_created",
        request_id=request.id,
        target_user_id=request.user_id,
    )
    await session.flush()
    return request


async def get_user_for_request(
    session: AsyncSession,
    request: ConsultationRequest,
) -> User | None:
    return await get_user_by_id(session=session, user_id=request.user_id)


async def apply_setting_update(
    session: AsyncSession,
    admin_telegram_id: int,
    setting_key: str,
    raw_value: str,
) -> str:
    value = raw_value.strip()

    if setting_key == "working_days":
        days = [item.strip().lower() for item in value.split(",") if item.strip()]
        if not days or any(item not in WEEKDAY_VALUES for item in days):
            raise ValueError("Use weekdays list: monday,tuesday,...,sunday")
        await update_schedule_settings(session, working_days=days)
    elif setting_key == "working_hours":
        match = _HOURS_REGEX.fullmatch(value)
        if match is None:
            raise ValueError("Use format HH:MM-HH:MM")
        start = _time_from_hhmm(match.group("start"))
        end = _time_from_hhmm(match.group("end"))
        if end <= start:
            raise ValueError("End time must be greater than start time.")
        await update_schedule_settings(session, workday_start=start, workday_end=end)
    elif setting_key == "durations":
        durations = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
        if not durations:
            raise ValueError("At least one duration is required.")
        await update_schedule_settings(session, available_durations_minutes=durations)
    elif setting_key == "min_notice":
        min_notice = int(value)
        if min_notice <= 0:
            raise ValueError("Value must be positive.")
        await update_schedule_settings(session, min_notice_minutes=min_notice)
    elif setting_key == "buffer":
        buffer = int(value)
        if buffer < 0:
            raise ValueError("Value must be non-negative.")
        await update_schedule_settings(session, buffer_minutes=buffer)
    elif setting_key == "daily_limit":
        daily_limit = int(value)
        if daily_limit <= 0:
            raise ValueError("Value must be positive.")
        await update_schedule_settings(session, max_consultations_per_day=daily_limit)
    elif setting_key == "horizon":
        horizon = int(value)
        if horizon <= 0:
            raise ValueError("Value must be positive.")
        await update_schedule_settings(session, booking_horizon_days=horizon)
    elif setting_key == "forbidden_date":
        day_part, _, reason_part = value.partition("|")
        day = date.fromisoformat(day_part.strip())
        reason = reason_part.strip() or None
        await add_forbidden_date(session=session, day=day, reason=reason)
    elif setting_key == "forbidden_period":
        period_part, _, reason_part = value.partition("|")
        start_at, end_at = parse_forbidden_period(period_part.strip())
        reason = reason_part.strip() or None
        await add_forbidden_period(
            session=session,
            start_at=start_at,
            end_at=end_at,
            reason=reason,
        )
    elif setting_key == "new_request_text":
        settings = await get_schedule_settings(session)
        templates = dict(settings.notification_templates)
        templates["new_request_admin"] = value
        await update_schedule_settings(session, notification_templates=templates)
    else:
        raise ValueError(f"Unsupported setting: {setting_key}")

    await create_admin_audit_log(
        session=session,
        admin_telegram_id=admin_telegram_id,
        action_type="settings_updated",
        payload={"setting_key": setting_key},
    )
    await session.flush()
    updated_settings = await get_schedule_settings(session)
    return build_settings_summary(updated_settings)
