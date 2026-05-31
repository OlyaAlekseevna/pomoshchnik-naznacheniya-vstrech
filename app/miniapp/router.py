from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.admin_service import (
    ApprovalResult,
    SlotUnavailableOnApprovalError,
    apply_setting_update,
    approve_request_with_calendar,
    build_google_oauth_instructions,
    build_history_text,
    connect_google_oauth_with_code,
    get_request_history,
    get_requests_for_admin,
    get_user_for_request,
    is_admin_telegram_id,
    manual_create_meeting_for_user,
    parse_alternative_slot,
    reject_request,
    reject_with_alternative_slot,
    toggle_user_block,
)
from app.bot.user_flow_service import (
    SlotChoice,
    build_user_requests_text,
    calculate_slots_for_date,
    cancel_request_for_user,
    create_request_from_draft,
    ensure_active_request_limit_not_exceeded,
    ensure_slot_still_available,
    get_schedule_settings_or_fail,
    get_user_requests,
    is_valid_email,
    request_and_anonymize_user_data,
    request_status_label,
    slot_rules_from_settings,
    update_request_goal_for_user,
)
from app.core.config import Settings
from app.db.enums import RequestStatus
from app.db.models import ConsultationRequest, User
from app.db.repositories import get_or_create_user_by_telegram_id, get_user_by_telegram_id
from app.domain.exceptions import BusinessRuleViolation
from app.domain.scheduling import build_week_window
from app.miniapp.auth import TelegramInitDataError, parse_and_validate_init_data
from app.miniapp.schemas import (
    AlternativeSlotPayload,
    AuthResponse,
    AuthTelegramRequest,
    BookingSlotsResponse,
    BookingWeekResponse,
    CreateRequestPayload,
    DevLoginRequest,
    GoogleExchangePayload,
    MeResponse,
    RejectPayload,
    SettingUpdatePayload,
    UpdateGoalPayload,
)
from app.miniapp.sessions import InMemoryMiniAppSessionStore, MiniAppSession
from app.services.google_calendar import (
    GoogleAuthRequiredError,
    GoogleCalendarService,
    GoogleIntegrationError,
    GooglePermissionDeniedError,
)

logger = logging.getLogger(__name__)

miniapp_api_router = APIRouter(prefix="/api/miniapp", tags=["miniapp"])
miniapp_web_router = APIRouter(tags=["miniapp"])

MINIAPP_STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass(frozen=True)
class MiniAppAuthContext:
    telegram_user_id: int
    role: str
    token: str


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    return request.app.state.session_factory


def _session_store(request: Request) -> InMemoryMiniAppSessionStore:
    return request.app.state.miniapp_sessions


def _extract_bearer_token(value: str | None) -> str | None:
    if value is None:
        return None
    parts = value.strip().split()
    if len(parts) != 2:
        return None
    if parts[0].lower() != "bearer":
        return None
    return parts[1]


async def _current_auth_context(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
) -> MiniAppAuthContext:
    token = _extract_bearer_token(authorization)
    if token is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")
    session_store = _session_store(request)
    session = session_store.get(token)
    if session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    return MiniAppAuthContext(
        telegram_user_id=session.telegram_user_id,
        role=session.role,
        token=token,
    )


def _ensure_admin(context: MiniAppAuthContext) -> None:
    if context.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required")


async def _load_user(
    session: AsyncSession,
    telegram_user_id: int,
) -> User:
    user = await get_user_by_telegram_id(session=session, telegram_user_id=telegram_user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user


def _request_to_payload(item: ConsultationRequest) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": item.id,
        "status": item.status.value,
        "status_label": request_status_label(item.status),
        "meeting_date": item.meeting_date.isoformat(),
        "start_time": item.start_time.strftime("%H:%M"),
        "end_time": item.end_time.strftime("%H:%M"),
        "duration_minutes": item.duration_minutes,
        "full_name": item.full_name,
        "phone": item.phone,
        "email": item.email,
        "meeting_goal": item.meeting_goal,
        "rejection_reason": item.rejection_reason,
        "alternative_date": item.alternative_date.isoformat() if item.alternative_date else None,
        "alternative_start_time": (
            item.alternative_start_time.strftime("%H:%M")
            if item.alternative_start_time
            else None
        ),
        "alternative_end_time": (
            item.alternative_end_time.strftime("%H:%M") if item.alternative_end_time else None
        ),
    }
    return payload


def _to_auth_response(
    session: MiniAppSession,
) -> AuthResponse:
    expires_in = int((session.expires_at - datetime.now(UTC)).total_seconds())
    return AuthResponse(
        access_token=session.token,
        expires_in=max(1, expires_in),
        role=session.role,
        telegram_user_id=session.telegram_user_id,
    )


def _google_service(settings: Settings) -> GoogleCalendarService:
    return GoogleCalendarService(settings)


@miniapp_web_router.get("/miniapp")
async def miniapp_index() -> FileResponse:
    index_path = MINIAPP_STATIC_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Mini App index is missing")
    return FileResponse(index_path)


@miniapp_api_router.get("/health")
async def miniapp_health() -> dict[str, str]:
    return {"status": "ok"}


@miniapp_api_router.post("/auth/telegram", response_model=AuthResponse)
async def miniapp_auth_telegram(
    payload: AuthTelegramRequest,
    request: Request,
) -> AuthResponse:
    settings = _settings(request)
    if settings.telegram_bot_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram bot token is not configured.",
        )
    bot_token = settings.telegram_bot_token.get_secret_value()
    try:
        validated = parse_and_validate_init_data(
            init_data=payload.init_data,
            bot_token=bot_token,
            max_age_seconds=settings.miniapp_auth_max_age_seconds,
        )
    except TelegramInitDataError as error:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(error)) from error

    session_factory = _session_factory(request)
    async with session_factory() as session:
        await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=validated.user.telegram_user_id,
            invited_access_granted=True,
            first_name=validated.user.first_name,
            last_name=validated.user.last_name,
            username=validated.user.username,
        )
        await session.commit()

    role = "admin" if is_admin_telegram_id(validated.user.telegram_user_id, settings) else "user"
    store = _session_store(request)
    created_session = store.create(
        telegram_user_id=validated.user.telegram_user_id,
        role=role,
        ttl_minutes=settings.miniapp_session_ttl_minutes,
    )
    return _to_auth_response(created_session)


@miniapp_api_router.post("/auth/dev-login", response_model=AuthResponse)
async def miniapp_auth_dev(
    payload: DevLoginRequest,
    request: Request,
) -> AuthResponse:
    settings = _settings(request)
    if not settings.miniapp_dev_login_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Dev login is disabled.",
        )

    session_factory = _session_factory(request)
    async with session_factory() as session:
        await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=payload.telegram_user_id,
            invited_access_granted=True,
            first_name=payload.first_name,
            last_name=payload.last_name,
            username=payload.username,
        )
        await session.commit()

    role = "admin" if is_admin_telegram_id(payload.telegram_user_id, settings) else "user"
    created_session = _session_store(request).create(
        telegram_user_id=payload.telegram_user_id,
        role=role,
        ttl_minutes=settings.miniapp_session_ttl_minutes,
    )
    return _to_auth_response(created_session)


@miniapp_api_router.post("/auth/logout")
async def miniapp_logout(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, str]:
    _session_store(request).delete(context.token)
    return {"status": "ok"}


@miniapp_api_router.get("/me", response_model=MeResponse)
async def miniapp_me(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> MeResponse:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        user = await _load_user(session, context.telegram_user_id)
    return MeResponse(
        telegram_user_id=user.telegram_user_id,
        role=context.role,
        is_blocked=user.is_blocked,
        first_name=user.first_name,
        last_name=user.last_name,
        username=user.username,
    )


@miniapp_api_router.get("/dashboard")
async def miniapp_dashboard(
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    return {
        "role": context.role,
        "blocks": [
            "Записаться",
            "Мои заявки",
            "Профиль",
            "Поддержка",
            "Уведомления",
        ],
    }


@miniapp_api_router.get("/support")
async def miniapp_support(
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ = context
    return {
        "title": "Поддержка",
        "description": (
            "Если нужна помощь с записью или заявкой, "
            "напишите владельцу календаря в Telegram."
        ),
        "channels": ["Telegram"],
        "response_time_hint": "Обычно отвечаем в течение рабочего дня.",
    }


@miniapp_api_router.get("/notifications")
async def miniapp_notifications(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        user = await _load_user(session=session, telegram_user_id=context.telegram_user_id)
        requests = await get_user_requests(session=session, user_id=user.id)

    pending_count = sum(
        1
        for item in requests
        if item.status in {RequestStatus.PENDING_APPROVAL, RequestStatus.UPDATED_BY_USER}
    )
    approved_count = sum(1 for item in requests if item.status == RequestStatus.APPROVED)
    rejected_count = sum(1 for item in requests if item.status == RequestStatus.REJECTED)

    items: list[dict[str, str | int]] = []
    if pending_count:
        items.append(
            {
                "type": "pending",
                "title": "Заявки на согласовании",
                "text": f"{pending_count} заявок сейчас ожидают решения администратора.",
                "count": pending_count,
            }
        )
    if approved_count:
        items.append(
            {
                "type": "approved",
                "title": "Согласованные встречи",
                "text": f"{approved_count} заявок уже согласованы.",
                "count": approved_count,
            }
        )
    if rejected_count:
        items.append(
            {
                "type": "rejected",
                "title": "Отклоненные заявки",
                "text": f"{rejected_count} заявок отклонены. Можно отправить новую заявку.",
                "count": rejected_count,
            }
        )
    if not items:
        items.append(
            {
                "type": "empty",
                "title": "Пока уведомлений нет",
                "text": "Когда появятся изменения по заявкам, они отобразятся здесь.",
                "count": 0,
            }
        )
    return {
        "unread_count": pending_count + rejected_count,
        "items": items,
    }


@miniapp_api_router.get("/booking/config")
async def miniapp_booking_config(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ = context
    session_factory = _session_factory(request)
    async with session_factory() as session:
        settings = await get_schedule_settings_or_fail(session)
    return {
        "timezone": settings.timezone,
        "available_durations_minutes": settings.available_durations_minutes,
        "booking_horizon_days": settings.booking_horizon_days,
        "min_notice_minutes": settings.min_notice_minutes,
        "buffer_minutes": settings.buffer_minutes,
        "max_consultations_per_day": settings.max_consultations_per_day,
    }


@miniapp_api_router.get("/booking/week", response_model=BookingWeekResponse)
async def miniapp_booking_week(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
    week_offset: int = 0,
) -> BookingWeekResponse:
    _ = context
    session_factory = _session_factory(request)
    async with session_factory() as session:
        settings = await get_schedule_settings_or_fail(session)
    today = datetime.now(UTC).date()
    try:
        week = build_week_window(
            today=today,
            week_offset=week_offset,
            booking_horizon_days=settings.booking_horizon_days,
        )
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return BookingWeekResponse(
        week_offset=week_offset,
        week_start=week.week_start,
        week_end=week.week_end,
        can_go_prev=week.can_go_prev,
        can_go_next=week.can_go_next,
        days=week.days,
    )


@miniapp_api_router.get("/booking/slots", response_model=BookingSlotsResponse)
async def miniapp_booking_slots(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
    meeting_date: str,
    duration_minutes: int,
) -> BookingSlotsResponse:
    _ = context
    try:
        parsed_date = datetime.strptime(meeting_date, "%Y-%m-%d").date()
    except ValueError as error:
        raise HTTPException(status_code=400, detail="meeting_date must be YYYY-MM-DD") from error

    session_factory = _session_factory(request)
    async with session_factory() as session:
        settings = await get_schedule_settings_or_fail(session)
        rules = slot_rules_from_settings(settings)
        slots = await calculate_slots_for_date(
            session=session,
            meeting_date=parsed_date,
            duration_minutes=duration_minutes,
            rules=rules,
        )
    return BookingSlotsResponse(
        date=parsed_date,
        duration_minutes=duration_minutes,
        slots=[slot.encoded for slot in slots],
    )


@miniapp_api_router.post("/requests")
async def miniapp_create_request(
    request: Request,
    payload: CreateRequestPayload,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    if not payload.personal_data_consent:
        raise HTTPException(status_code=400, detail="Personal data consent is required.")
    if not is_valid_email(payload.email):
        raise HTTPException(status_code=400, detail="Invalid email format.")

    slot = SlotChoice.decode(payload.slot_encoded)
    session_factory = _session_factory(request)
    settings = _settings(request)
    async with session_factory() as session:
        user = await _load_user(session=session, telegram_user_id=context.telegram_user_id)
        if user.is_blocked:
            raise HTTPException(status_code=403, detail="User is blocked.")
        try:
            await ensure_active_request_limit_not_exceeded(
                session=session,
                user_id=user.id,
                max_active_requests_per_user=settings.max_active_requests_per_user,
            )
        except BusinessRuleViolation as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        schedule_settings = await get_schedule_settings_or_fail(session)
        rules = slot_rules_from_settings(schedule_settings)
        available_slots = await calculate_slots_for_date(
            session=session,
            meeting_date=slot.start_at.date(),
            duration_minutes=payload.duration_minutes,
            rules=rules,
        )
        try:
            ensure_slot_still_available(slot, available_slots)
        except BusinessRuleViolation as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

        created = await create_request_from_draft(
            session=session,
            user=user,
            full_name=payload.full_name.strip(),
            phone=payload.phone.strip(),
            email=payload.email.strip(),
            meeting_goal=payload.meeting_goal.strip(),
            duration_minutes=payload.duration_minutes,
            slot_choice=slot,
            personal_data_consent=True,
        )
        await session.commit()
        return {"status": "ok", "request": _request_to_payload(created)}


@miniapp_api_router.get("/requests")
async def miniapp_requests(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        user = await _load_user(session=session, telegram_user_id=context.telegram_user_id)
        items = await get_user_requests(session=session, user_id=user.id)
    return {
        "items": [_request_to_payload(item) for item in items],
        "summary_text": build_user_requests_text(items),
    }


@miniapp_api_router.patch("/requests/{request_id}/goal")
async def miniapp_update_request_goal(
    request_id: int,
    payload: UpdateGoalPayload,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        user = await _load_user(session=session, telegram_user_id=context.telegram_user_id)
        try:
            updated = await update_request_goal_for_user(
                session=session,
                request_id=request_id,
                user_id=user.id,
                telegram_user_id=context.telegram_user_id,
                new_goal=payload.meeting_goal.strip(),
            )
        except (LookupError, BusinessRuleViolation) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        await session.commit()
    return {"status": "ok", "request": _request_to_payload(updated)}


@miniapp_api_router.post("/requests/{request_id}/cancel")
async def miniapp_cancel_request(
    request_id: int,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        user = await _load_user(session=session, telegram_user_id=context.telegram_user_id)
        try:
            canceled = await cancel_request_for_user(
                session=session,
                request_id=request_id,
                user_id=user.id,
                telegram_user_id=context.telegram_user_id,
            )
        except (LookupError, BusinessRuleViolation) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        await session.commit()
    return {"status": "ok", "request": _request_to_payload(canceled)}


@miniapp_api_router.post("/user/data-deletion")
async def miniapp_data_deletion(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    session_factory = _session_factory(request)
    async with session_factory() as session:
        user = await _load_user(session=session, telegram_user_id=context.telegram_user_id)
        result = await request_and_anonymize_user_data(
            session=session,
            user=user,
            telegram_user_id=context.telegram_user_id,
        )
        await session.commit()
    return {"status": "ok", "result": result}


@miniapp_api_router.get("/admin/requests")
async def miniapp_admin_requests(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    async with session_factory() as session:
        items = await get_requests_for_admin(session=session, limit=50)
        payload_items: list[dict[str, object]] = []
        for item in items:
            user = await get_user_for_request(session=session, request=item)
            payload_item = _request_to_payload(item)
            payload_item["user"] = (
                {
                    "id": user.id,
                    "telegram_user_id": user.telegram_user_id,
                    "is_blocked": user.is_blocked,
                }
                if user is not None
                else None
            )
            payload_items.append(payload_item)
    return {"items": payload_items}


async def _serialize_approval_result(result: ApprovalResult) -> dict[str, object]:
    return {
        "request": _request_to_payload(result.request),
        "event_url": result.event_url,
        "user_telegram_id": result.user.telegram_user_id if result.user is not None else None,
    }


@miniapp_api_router.post("/admin/requests/{request_id}/approve")
async def miniapp_admin_approve(
    request_id: int,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    settings = _settings(request)
    async with session_factory() as session:
        try:
            result = await approve_request_with_calendar(
                session=session,
                request_id=request_id,
                admin_telegram_id=context.telegram_user_id,
                settings=settings,
                service=_google_service(settings),
            )
        except SlotUnavailableOnApprovalError as error:
            await session.commit()
            raise HTTPException(status_code=409, detail=str(error)) from error
        except GoogleAuthRequiredError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (GooglePermissionDeniedError, GoogleIntegrationError) as error:
            await session.commit()
            raise HTTPException(status_code=502, detail=str(error)) from error
        except (LookupError, BusinessRuleViolation) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        await session.commit()
    return {"status": "ok", "result": await _serialize_approval_result(result)}


@miniapp_api_router.post("/admin/requests/{request_id}/reject")
async def miniapp_admin_reject(
    request_id: int,
    payload: RejectPayload,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    async with session_factory() as session:
        try:
            updated = await reject_request(
                session=session,
                request_id=request_id,
                admin_telegram_id=context.telegram_user_id,
                reason=payload.reason.strip(),
            )
        except (LookupError, BusinessRuleViolation) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        await session.commit()
    return {"status": "ok", "request": _request_to_payload(updated)}


@miniapp_api_router.post("/admin/requests/{request_id}/alternative")
async def miniapp_admin_alternative(
    request_id: int,
    payload: AlternativeSlotPayload,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    try:
        alternative_date, alternative_start, alternative_end = parse_alternative_slot(payload.value)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error

    session_factory = _session_factory(request)
    async with session_factory() as session:
        try:
            updated = await reject_with_alternative_slot(
                session=session,
                request_id=request_id,
                admin_telegram_id=context.telegram_user_id,
                alternative_date=alternative_date,
                alternative_start_time=alternative_start,
                alternative_end_time=alternative_end,
            )
        except (LookupError, BusinessRuleViolation) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        await session.commit()
    return {"status": "ok", "request": _request_to_payload(updated)}


@miniapp_api_router.get("/admin/requests/{request_id}/history")
async def miniapp_admin_history(
    request_id: int,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    async with session_factory() as session:
        history = await get_request_history(session=session, request_id=request_id)
    return {
        "items": [
            {
                "status": item.status.value,
                "changed_by_role": item.changed_by_role.value,
                "changed_by_telegram_id": item.changed_by_telegram_id,
                "comment": item.comment,
                "created_at": item.created_at.isoformat(),
            }
            for item in history
        ],
        "summary_text": build_history_text(request_id=request_id, history_items=history),
    }


@miniapp_api_router.post("/admin/requests/{request_id}/block")
async def miniapp_admin_block(
    request_id: int,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    async with session_factory() as session:
        updated_user = await toggle_user_block(
            session=session,
            request_id=request_id,
            admin_telegram_id=context.telegram_user_id,
            blocked=True,
        )
        await session.commit()
    return {"status": "ok", "user_id": updated_user.id, "is_blocked": updated_user.is_blocked}


@miniapp_api_router.post("/admin/requests/{request_id}/unblock")
async def miniapp_admin_unblock(
    request_id: int,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    async with session_factory() as session:
        updated_user = await toggle_user_block(
            session=session,
            request_id=request_id,
            admin_telegram_id=context.telegram_user_id,
            blocked=False,
        )
        await session.commit()
    return {"status": "ok", "user_id": updated_user.id, "is_blocked": updated_user.is_blocked}


@miniapp_api_router.post("/admin/requests/{request_id}/manual-create")
async def miniapp_admin_manual_create(
    request_id: int,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    async with session_factory() as session:
        updated = await manual_create_meeting_for_user(
            session=session,
            request_id=request_id,
            admin_telegram_id=context.telegram_user_id,
        )
        await session.commit()
    return {"status": "ok", "request": _request_to_payload(updated)}


@miniapp_api_router.get("/admin/settings")
async def miniapp_admin_settings(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    async with session_factory() as session:
        settings = await get_schedule_settings_or_fail(session)
    return {
        "timezone": settings.timezone,
        "working_days": settings.working_days,
        "workday_start": settings.workday_start.strftime("%H:%M"),
        "workday_end": settings.workday_end.strftime("%H:%M"),
        "available_durations_minutes": settings.available_durations_minutes,
        "min_notice_minutes": settings.min_notice_minutes,
        "buffer_minutes": settings.buffer_minutes,
        "max_consultations_per_day": settings.max_consultations_per_day,
        "booking_horizon_days": settings.booking_horizon_days,
        "notification_templates": settings.notification_templates,
    }


@miniapp_api_router.patch("/admin/settings")
async def miniapp_admin_settings_update(
    payload: SettingUpdatePayload,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, object]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    async with session_factory() as session:
        try:
            summary_text = await apply_setting_update(
                session=session,
                admin_telegram_id=context.telegram_user_id,
                setting_key=payload.setting_key.strip(),
                raw_value=payload.value.strip(),
            )
        except Exception as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        await session.commit()
    return {"status": "ok", "summary_text": summary_text}


@miniapp_api_router.get("/admin/google/oauth/url")
async def miniapp_admin_google_oauth_url(
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, str]:
    _ensure_admin(context)
    settings = _settings(request)
    instructions = build_google_oauth_instructions(
        settings=settings,
        service=_google_service(settings),
    )
    return {"instructions": instructions}


@miniapp_api_router.post("/admin/google/oauth/exchange")
async def miniapp_admin_google_oauth_exchange(
    payload: GoogleExchangePayload,
    request: Request,
    context: Annotated[MiniAppAuthContext, Depends(_current_auth_context)],
) -> dict[str, str]:
    _ensure_admin(context)
    session_factory = _session_factory(request)
    settings = _settings(request)
    async with session_factory() as session:
        try:
            text = await connect_google_oauth_with_code(
                session=session,
                admin_telegram_id=context.telegram_user_id,
                authorization_code=payload.code.strip(),
                service=_google_service(settings),
            )
        except (GoogleIntegrationError, ValueError) as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        await session.commit()
    return {"status": text}
