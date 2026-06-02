from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from urllib.parse import parse_qs, unquote, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.enums import GoogleEventStatus, RequestChangedByRole, RequestStatus
from app.db.models import ConsultationRequest, RequestStatusHistory, ScheduleSettings, User
from app.db.repositories import (
    add_forbidden_date,
    add_forbidden_period,
    append_request_status_history,
    create_admin_audit_log,
    create_google_calendar_event,
    create_technical_error,
    get_google_oauth_credentials,
    get_latest_google_calendar_event_by_request_id,
    get_request_by_id,
    get_schedule_settings,
    get_user_by_id,
    list_active_reservations_by_date,
    list_request_status_history,
    list_requests_for_admin,
    set_user_blocked,
    update_google_calendar_event,
    update_schedule_settings,
    upsert_google_oauth_credentials,
)
from app.domain.exceptions import BusinessRuleViolation
from app.domain.scheduling import TimeInterval
from app.services.google_calendar import (
    GoogleAuthRequiredError,
    GoogleCalendarService,
    GoogleEventDraft,
    GoogleIntegrationError,
    GooglePermissionDeniedError,
)

logger = logging.getLogger(__name__)

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

WEEKDAY_LABELS_RU = {
    "monday": "понедельник",
    "tuesday": "вторник",
    "wednesday": "среда",
    "thursday": "четверг",
    "friday": "пятница",
    "saturday": "суббота",
    "sunday": "воскресенье",
}

WEEKDAY_ALIASES = {
    "monday": "monday",
    "mon": "monday",
    "понедельник": "monday",
    "пон": "monday",
    "пн": "monday",
    "tuesday": "tuesday",
    "tue": "tuesday",
    "tues": "tuesday",
    "вторник": "tuesday",
    "втор": "tuesday",
    "вт": "tuesday",
    "wednesday": "wednesday",
    "wed": "wednesday",
    "среда": "wednesday",
    "сред": "wednesday",
    "ср": "wednesday",
    "thursday": "thursday",
    "thu": "thursday",
    "thur": "thursday",
    "четверг": "thursday",
    "четв": "thursday",
    "чт": "thursday",
    "friday": "friday",
    "fri": "friday",
    "пятница": "friday",
    "пят": "friday",
    "пт": "friday",
    "saturday": "saturday",
    "sat": "saturday",
    "суббота": "saturday",
    "суб": "saturday",
    "сб": "saturday",
    "sunday": "sunday",
    "sun": "sunday",
    "воскресенье": "sunday",
    "воск": "sunday",
    "вс": "sunday",
}

_HOURS_REGEX = re.compile(r"^(?P<start>\d{2}:\d{2})-(?P<end>\d{2}:\d{2})$")
_ALT_SLOT_REGEX = re.compile(
    r"^(?P<day>\d{4}-\d{2}-\d{2}) (?P<start>\d{2}:\d{2})-(?P<end>\d{2}:\d{2})$"
)
_PERIOD_REGEX = re.compile(
    r"^(?P<start_day>\d{4}-\d{2}-\d{2}) (?P<start_time>\d{2}:\d{2}) "
    r"- (?P<end_day>\d{4}-\d{2}-\d{2}) (?P<end_time>\d{2}:\d{2})$"
)

_FALLBACK_TIMEZONE_OFFSETS = {
    "UTC": timedelta(0),
    "Etc/UTC": timedelta(0),
    "Asia/Yekaterinburg": timedelta(hours=5),
}


@dataclass(frozen=True)
class ApprovalResult:
    request: ConsultationRequest
    user: User | None
    event_url: str | None


class SlotUnavailableOnApprovalError(BusinessRuleViolation):
    """Selected slot is no longer available when admin approves the request."""


def _request_status_label(status: RequestStatus) -> str:
    labels = {
        RequestStatus.DRAFT: "Черновик",
        RequestStatus.PENDING_APPROVAL: "Ожидает согласования",
        RequestStatus.UPDATED_BY_USER: "Обновлена пользователем",
        RequestStatus.CANCELED_BY_USER: "Отменена пользователем",
        RequestStatus.APPROVED: "Согласована",
        RequestStatus.REJECTED: "Отклонена",
        RequestStatus.SLOT_UNAVAILABLE: "Слот недоступен",
        RequestStatus.RESERVATION_EXPIRED: "Резерв истек",
        RequestStatus.EVENT_CREATION_ERROR: "Ошибка создания события",
    }
    return labels.get(status, status.value)


def is_admin_telegram_id(telegram_user_id: int | None, settings: Settings) -> bool:
    return telegram_user_id is not None and settings.telegram_admin_id == telegram_user_id


def _ensure_request_editable_for_admin(request: ConsultationRequest, action: str) -> None:
    if request.status not in EDITABLE_BY_ADMIN_STATUSES:
        raise BusinessRuleViolation(
            f"Действие '{action}' недоступно для статуса '{request.status.value}'."
        )


def _time_from_hhmm(raw_value: str) -> time:
    return datetime.strptime(raw_value, "%H:%M").time()


def parse_alternative_slot(raw_value: str) -> tuple[date, time, time]:
    match = _ALT_SLOT_REGEX.fullmatch(raw_value.strip())
    if match is None:
        raise ValueError("Формат альтернативного слота: YYYY-MM-DD HH:MM-HH:MM.")
    day = date.fromisoformat(match.group("day"))
    start = _time_from_hhmm(match.group("start"))
    end = _time_from_hhmm(match.group("end"))
    if end <= start:
        raise ValueError("Время окончания должно быть больше времени начала.")
    return day, start, end


def parse_forbidden_period(raw_value: str) -> tuple[datetime, datetime]:
    match = _PERIOD_REGEX.fullmatch(raw_value.strip())
    if match is None:
        raise ValueError(
            "Формат запрещенного периода: YYYY-MM-DD HH:MM - YYYY-MM-DD HH:MM."
        )
    start_at = datetime.fromisoformat(
        f"{match.group('start_day')}T{match.group('start_time')}"
    ).replace(tzinfo=UTC)
    end_at = datetime.fromisoformat(
        f"{match.group('end_day')}T{match.group('end_time')}"
    ).replace(tzinfo=UTC)
    if end_at <= start_at:
        raise ValueError("Окончание запрещенного периода должно быть позже начала.")
    return start_at, end_at


def _resolve_timezone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        fallback_offset = _FALLBACK_TIMEZONE_OFFSETS.get(timezone_name)
        if fallback_offset is None:
            raise RuntimeError(f"Неизвестный часовой пояс: {timezone_name}") from None
        logger.warning(
            "Timezone fallback is used in admin calendar flow.",
            extra={"event": "timezone_fallback_used", "timezone": timezone_name},
        )
        return timezone(fallback_offset, name=timezone_name)


def _request_interval(request: ConsultationRequest, timezone_name: str) -> TimeInterval:
    tz = _resolve_timezone(timezone_name)
    start_at = datetime.combine(request.meeting_date, request.start_time, tzinfo=tz)
    end_at = datetime.combine(request.meeting_date, request.end_time, tzinfo=tz)
    return TimeInterval(start_at=start_at, end_at=end_at)


def _intersects(left: TimeInterval, right: TimeInterval) -> bool:
    return left.start_at < right.end_at and right.start_at < left.end_at


def _buffered(interval: TimeInterval, buffer_minutes: int) -> TimeInterval:
    delta = timedelta(minutes=buffer_minutes)
    return TimeInterval(start_at=interval.start_at - delta, end_at=interval.end_at + delta)


def _normalize_interval_timezone(interval: TimeInterval, tzinfo) -> TimeInterval:
    start_at = (
        interval.start_at.replace(tzinfo=tzinfo)
        if interval.start_at.tzinfo is None
        else interval.start_at.astimezone(tzinfo)
    )
    end_at = (
        interval.end_at.replace(tzinfo=tzinfo)
        if interval.end_at.tzinfo is None
        else interval.end_at.astimezone(tzinfo)
    )
    return TimeInterval(start_at=start_at, end_at=end_at)


def _build_google_event_description(request: ConsultationRequest, user: User | None) -> str:
    telegram_line = "не указан"
    if user is not None:
        if user.username:
            telegram_line = f"https://t.me/{user.username}"
        else:
            telegram_line = f"tg://user?id={user.telegram_user_id}"
    return (
        "Заявка на консультацию через Telegram-бот.\n"
        f"Имя: {request.full_name}\n"
        f"Телефон: {request.phone}\n"
        f"Email: {request.email}\n"
        f"Цель: {request.meeting_goal}\n"
        f"Telegram: {telegram_line}"
    )


def _weekday_label_ru(day_value: str) -> str:
    return WEEKDAY_LABELS_RU.get(day_value, day_value)


def _normalize_weekday_token(raw_value: str) -> str | None:
    normalized = raw_value.strip().lower().replace("ё", "е").replace(".", "")
    if not normalized:
        return None
    if normalized in WEEKDAY_VALUES:
        return normalized
    return WEEKDAY_ALIASES.get(normalized)


def _parse_working_days(raw_value: str) -> list[str]:
    raw_items = [item.strip() for item in raw_value.split(",") if item.strip()]
    parsed: list[str] = []
    unknown: list[str] = []
    for item in raw_items:
        normalized = _normalize_weekday_token(item)
        if normalized is None:
            unknown.append(item)
            continue
        if normalized not in parsed:
            parsed.append(normalized)
    if not parsed or unknown:
        unknown_suffix = f" Не распознано: {', '.join(unknown)}." if unknown else ""
        raise ValueError(
            "Используйте дни недели через запятую на русском или английском. "
            "Пример: понедельник,вторник,среда или monday,tuesday,wednesday."
            f"{unknown_suffix}"
        )
    return parsed


def build_request_card(request: ConsultationRequest, user: User | None) -> str:
    user_blocked = user.is_blocked if user is not None else False
    user_line = (
        f"Пользователь #{user.id} | tg={user.telegram_user_id} | blocked={user_blocked}"
        if user is not None
        else "Пользователь: неизвестен"
    )

    alternative = ""
    if (
        request.alternative_date
        and request.alternative_start_time
        and request.alternative_end_time
    ):
        alternative = (
            "\nАльтернативный слот: "
            f"{request.alternative_date.isoformat()} "
            f"{request.alternative_start_time.strftime('%H:%M')}"
            f"-{request.alternative_end_time.strftime('%H:%M')}"
        )

    rejection = ""
    if request.rejection_reason:
        rejection = f"\nПричина отклонения: {request.rejection_reason}"

    return (
        f"Заявка #{request.id}\n"
        f"{user_line}\n"
        f"Дата: {request.meeting_date.isoformat()} "
        f"{request.start_time.strftime('%H:%M')}-{request.end_time.strftime('%H:%M')}\n"
        f"Длительность: {request.duration_minutes} мин\n"
        f"Статус: {_request_status_label(request.status)}\n"
        f"Имя: {request.full_name}\n"
        f"Телефон: {request.phone}\n"
        f"Email: {request.email}\n"
        f"Цель: {request.meeting_goal}"
        f"{rejection}"
        f"{alternative}"
    )


def build_history_text(request_id: int, history_items: list[RequestStatusHistory]) -> str:
    if not history_items:
        return f"История статусов для заявки #{request_id} пуста."

    lines = [f"История статусов заявки #{request_id}:"]
    for item in history_items:
        lines.append(
            f"- {item.created_at.isoformat()} | {_request_status_label(item.status)} | "
            f"{item.changed_by_role.value} | {item.comment or '-'}"
        )
    return "\n".join(lines)


def build_settings_summary(settings: ScheduleSettings) -> str:
    weekdays_ru = ",".join(_weekday_label_ru(item) for item in settings.working_days)
    return (
        "Текущие настройки расписания:\n"
        f"- часовой пояс: {settings.timezone}\n"
        f"- рабочие дни: {weekdays_ru}\n"
        f"- рабочее время: {settings.workday_start.strftime('%H:%M')}"
        f"-{settings.workday_end.strftime('%H:%M')}\n"
        f"- длительности: {','.join(str(item) for item in settings.available_durations_minutes)}\n"
        f"- минимум до встречи (мин): {settings.min_notice_minutes}\n"
        f"- буфер (мин): {settings.buffer_minutes}\n"
        f"- лимит консультаций в день: {settings.max_consultations_per_day}\n"
        f"- горизонт записи (дни): {settings.booking_horizon_days}\n"
        "- шаблон уведомления админу о новой заявке: "
        f"{settings.notification_templates.get('new_request_admin', '')}"
    )


async def get_requests_for_admin(
    session: AsyncSession,
    limit: int = 20,
) -> list[ConsultationRequest]:
    return await list_requests_for_admin(session=session, limit=limit)


def build_google_oauth_instructions(settings: Settings, service: GoogleCalendarService) -> str:
    if not service.is_oauth_configured():
        return (
            "Google OAuth пока не настроен.\n"
            "Заполните GOOGLE_OAUTH_CLIENT_ID и GOOGLE_OAUTH_CLIENT_SECRET в .env, "
            "перезапустите приложение и повторите команду."
        )
    url = service.build_authorization_url(state="admin_google_connect")
    return (
        "Подключение Google Calendar:\n"
        "1) Откройте ссылку ниже в браузере.\n"
        "2) Выберите Google-аккаунт с календарем.\n"
        "3) После подтверждения доступа скопируйте `code` или весь callback URL.\n"
        "4) Вставьте значение в Step 4 (Google OAuth) в Mini App или отправьте в /admin.\n"
        "5) Если появился запрос на повторную авторизацию, "
        "снова откройте OAuth URL и повторите шаги.\n\n"
        f"OAuth URL:\n{url}\n\n"
        f"Redirect URI: {settings.google_oauth_redirect_uri}"
    )


def extract_google_oauth_code(raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        raise ValueError("Код авторизации пустой.")

    parsed = urlparse(value)
    if parsed.scheme and parsed.netloc:
        query_values = parse_qs(parsed.query)
        code_from_query = query_values.get("code", [None])[0]
        if code_from_query:
            return unquote(code_from_query.strip())
        fragment_values = parse_qs(parsed.fragment)
        code_from_fragment = fragment_values.get("code", [None])[0]
        if code_from_fragment:
            return unquote(code_from_fragment.strip())
        raise ValueError("В ссылке не найден параметр code.")

    query_like = parse_qs(value.lstrip("?"))
    code_from_query_like = query_like.get("code", [None])[0]
    if code_from_query_like:
        return unquote(code_from_query_like.strip())

    if "code=" in value:
        tail = value.split("code=", maxsplit=1)[1]
        code_from_tail = tail.split("&", maxsplit=1)[0].strip()
        if code_from_tail:
            return unquote(code_from_tail)

    return value


async def connect_google_oauth_with_code(
    session: AsyncSession,
    admin_telegram_id: int,
    authorization_code: str,
    service: GoogleCalendarService,
) -> str:
    extracted_code = extract_google_oauth_code(authorization_code)
    tokens = await service.exchange_authorization_code(extracted_code)
    existing = await get_google_oauth_credentials(session)
    refresh_token = (
        tokens.refresh_token
        or (existing.refresh_token if existing is not None else None)
    )
    if not refresh_token:
        raise ValueError(
            "Google не вернул refresh_token. Повторите авторизацию с подтверждением доступа."
        )
    await upsert_google_oauth_credentials(
        session=session,
        refresh_token=refresh_token,
        access_token=tokens.access_token,
        access_token_expires_at=tokens.expires_at,
        scope=tokens.scope,
        token_type=tokens.token_type,
    )
    await create_admin_audit_log(
        session=session,
        admin_telegram_id=admin_telegram_id,
        action_type="google_oauth_connected",
    )
    return "Google OAuth успешно подключен. Теперь бот использует реальную занятость календаря."


async def approve_request(
    session: AsyncSession,
    request_id: int,
    admin_telegram_id: int,
) -> ConsultationRequest:
    request = await get_request_by_id(session, request_id=request_id)
    if request is None:
        raise LookupError("Заявка не найдена.")

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


async def approve_request_with_calendar(
    session: AsyncSession,
    request_id: int,
    admin_telegram_id: int,
    settings: Settings,
    service: GoogleCalendarService,
) -> ApprovalResult:
    request = await get_request_by_id(session, request_id=request_id)
    if request is None:
        raise LookupError("Заявка не найдена.")
    _ensure_request_editable_for_admin(request, action="approve_with_calendar")

    schedule_settings = await get_schedule_settings(session)
    interval = _request_interval(request, schedule_settings.timezone)
    day_start = datetime.combine(
        request.meeting_date,
        schedule_settings.workday_start,
        tzinfo=interval.start_at.tzinfo,
    )
    day_end = datetime.combine(
        request.meeting_date,
        schedule_settings.workday_end,
        tzinfo=interval.start_at.tzinfo,
    )

    credentials = await get_google_oauth_credentials(session)
    try:
        access_token, refreshed_tokens = await service.get_valid_access_token(credentials)
    except GoogleAuthRequiredError as error:
        await create_technical_error(
            session=session,
            source="google_calendar",
            request_id=request.id,
            user_id=request.user_id,
            error_code=error.__class__.__name__,
            error_message=str(error),
            details={"stage": "approve_request_access_token"},
        )
        logger.warning(
            "Google OAuth requires reauthorization during approval.",
            extra={"event": "google_oauth_reauthorization_required", "request_id": request.id},
        )
        raise
    if refreshed_tokens is not None and credentials is not None:
        await upsert_google_oauth_credentials(
            session=session,
            refresh_token=credentials.refresh_token,
            access_token=refreshed_tokens.access_token,
            access_token_expires_at=refreshed_tokens.expires_at,
            scope=refreshed_tokens.scope or credentials.scope,
            token_type=refreshed_tokens.token_type or credentials.token_type,
        )

    reservations = await list_active_reservations_by_date(
        session=session,
        meeting_date=request.meeting_date,
        exclude_request_id=request.id,
    )
    occupied = [
        _normalize_interval_timezone(
            TimeInterval(start_at=item.start_at, end_at=item.end_at),
            interval.start_at.tzinfo,
        )
        for item in reservations
    ]
    busy_from_google = await service.list_busy_intervals(
        access_token=access_token,
        time_min=day_start,
        time_max=day_end,
        timezone=schedule_settings.timezone,
    )
    occupied.extend(
        _normalize_interval_timezone(item, interval.start_at.tzinfo) for item in busy_from_google
    )

    buffered_busy = [_buffered(item, schedule_settings.buffer_minutes) for item in occupied]
    if any(_intersects(interval, item) for item in buffered_busy):
        request.status = RequestStatus.SLOT_UNAVAILABLE
        await append_request_status_history(
            session=session,
            request_id=request.id,
            status=RequestStatus.SLOT_UNAVAILABLE,
            changed_by_role=RequestChangedByRole.SYSTEM,
            changed_by_telegram_id=admin_telegram_id,
            comment="slot_unavailable_on_recheck",
        )
        await create_admin_audit_log(
            session=session,
            admin_telegram_id=admin_telegram_id,
            action_type="request_slot_unavailable_on_approval",
            request_id=request.id,
            target_user_id=request.user_id,
        )
        logger.warning(
            "Slot is busy during approval recheck.",
            extra={"event": "google_slot_busy_on_recheck", "request_id": request.id},
        )
        raise SlotUnavailableOnApprovalError("Слот уже занят при повторной проверке.")

    user = await get_user_by_id(session=session, user_id=request.user_id)
    event_record = await get_latest_google_calendar_event_by_request_id(session, request.id)
    if event_record is None:
        event_record = await create_google_calendar_event(
            session=session,
            request_id=request.id,
            creation_status=GoogleEventStatus.PENDING,
        )
    else:
        await update_google_calendar_event(
            session=session,
            event_record=event_record,
            creation_status=GoogleEventStatus.PENDING,
            google_event_id=None,
            event_url=None,
            created_in_google_at=None,
            error_text=None,
        )

    draft = GoogleEventDraft(
        summary=f"Консультация с {request.full_name}",
        location="Онлайн",
        description=_build_google_event_description(request, user),
        start_at=interval.start_at,
        end_at=interval.end_at,
        timezone=schedule_settings.timezone,
        attendee_email=request.email,
    )

    try:
        created_event = await service.create_event(access_token=access_token, draft=draft)
    except (GoogleAuthRequiredError, GooglePermissionDeniedError, GoogleIntegrationError) as error:
        await update_google_calendar_event(
            session=session,
            event_record=event_record,
            creation_status=GoogleEventStatus.FAILED,
            error_text=str(error),
        )
        request.status = RequestStatus.EVENT_CREATION_ERROR
        await append_request_status_history(
            session=session,
            request_id=request.id,
            status=RequestStatus.EVENT_CREATION_ERROR,
            changed_by_role=RequestChangedByRole.SYSTEM,
            changed_by_telegram_id=admin_telegram_id,
            comment="google_event_creation_error",
        )
        await create_technical_error(
            session=session,
            source="google_calendar",
            request_id=request.id,
            user_id=request.user_id,
            error_code=error.__class__.__name__,
            error_message=str(error),
            details={"stage": "approve_request"},
        )
        logger.exception(
            "Google event creation failed.",
            extra={"event": "google_event_creation_error", "request_id": request.id},
        )
        raise

    await _mark_request_as_approved(
        session=session,
        request=request,
        admin_telegram_id=admin_telegram_id,
    )
    await update_google_calendar_event(
        session=session,
        event_record=event_record,
        creation_status=GoogleEventStatus.CREATED,
        google_event_id=created_event.google_event_id,
        event_url=created_event.event_url,
        created_in_google_at=created_event.created_in_google_at,
        error_text=None,
    )
    return ApprovalResult(
        request=request,
        user=user,
        event_url=created_event.event_url,
    )


async def _mark_request_as_approved(
    session: AsyncSession,
    request: ConsultationRequest,
    admin_telegram_id: int,
) -> None:
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


async def reject_request(
    session: AsyncSession,
    request_id: int,
    admin_telegram_id: int,
    reason: str,
) -> ConsultationRequest:
    request = await get_request_by_id(session, request_id=request_id)
    if request is None:
        raise LookupError("Заявка не найдена.")

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
        raise LookupError("Заявка не найдена.")

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
        raise LookupError("Заявка не найдена.")

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
        raise LookupError("Заявка не найдена.")

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
        days = _parse_working_days(value)
        await update_schedule_settings(session, working_days=days)
    elif setting_key == "working_hours":
        match = _HOURS_REGEX.fullmatch(value)
        if match is None:
            raise ValueError("Используйте формат HH:MM-HH:MM")
        start = _time_from_hhmm(match.group("start"))
        end = _time_from_hhmm(match.group("end"))
        if end <= start:
            raise ValueError("Время окончания должно быть больше времени начала.")
        await update_schedule_settings(session, workday_start=start, workday_end=end)
    elif setting_key == "durations":
        durations = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
        if not durations:
            raise ValueError("Нужно указать хотя бы одну длительность.")
        await update_schedule_settings(session, available_durations_minutes=durations)
    elif setting_key == "min_notice":
        min_notice = int(value)
        if min_notice <= 0:
            raise ValueError("Значение должно быть положительным.")
        await update_schedule_settings(session, min_notice_minutes=min_notice)
    elif setting_key == "buffer":
        buffer = int(value)
        if buffer < 0:
            raise ValueError("Значение не может быть отрицательным.")
        await update_schedule_settings(session, buffer_minutes=buffer)
    elif setting_key == "daily_limit":
        daily_limit = int(value)
        if daily_limit <= 0:
            raise ValueError("Значение должно быть положительным.")
        await update_schedule_settings(session, max_consultations_per_day=daily_limit)
    elif setting_key == "horizon":
        horizon = int(value)
        if horizon <= 0:
            raise ValueError("Значение должно быть положительным.")
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
        raise ValueError(f"Неподдерживаемая настройка: {setting_key}")

    await create_admin_audit_log(
        session=session,
        admin_telegram_id=admin_telegram_id,
        action_type="settings_updated",
        payload={"setting_key": setting_key},
    )
    await session.flush()
    updated_settings = await get_schedule_settings(session)
    return build_settings_summary(updated_settings)
