from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import RequestChangedByRole, RequestStatus, ReservationStatus
from app.db.models import ConsultationRequest, ScheduleSettings, User
from app.db.repositories import (
    anonymize_user_personal_data,
    append_request_status_history,
    count_active_requests_by_user_id,
    count_consultations_for_date,
    create_consultation_request,
    get_active_reservation_by_request_id,
    get_request_by_id_and_user_id,
    get_schedule_settings,
    list_active_reservations_by_date,
    list_requests_by_user_id,
    release_slot_reservation,
)
from app.domain.exceptions import BusinessRuleViolation
from app.domain.lifecycle import (
    cancel_request_before_approval,
    reserve_request_slot,
    update_request_before_approval,
)
from app.domain.scheduling import SlotRules, TimeInterval, calculate_free_slots

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
CONSULTATION_KIND = "consultation"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SlotChoice:
    start_at: datetime
    end_at: datetime

    @property
    def label(self) -> str:
        return f"{self.start_at:%H:%M}-{self.end_at:%H:%M}"

    @property
    def encoded(self) -> str:
        return f"{self.start_at.isoformat()}|{self.end_at.isoformat()}"

    @staticmethod
    def decode(value: str) -> SlotChoice:
        start_raw, end_raw = value.split("|", maxsplit=1)
        return SlotChoice(
            start_at=datetime.fromisoformat(start_raw),
            end_at=datetime.fromisoformat(end_raw),
        )


def is_valid_email(value: str) -> bool:
    return bool(EMAIL_REGEX.fullmatch(value.strip()))


def request_status_label(status: RequestStatus) -> str:
    labels = {
        RequestStatus.DRAFT: "черновик",
        RequestStatus.PENDING_APPROVAL: "ожидает согласования",
        RequestStatus.UPDATED_BY_USER: "обновлена пользователем",
        RequestStatus.CANCELED_BY_USER: "отменена пользователем",
        RequestStatus.APPROVED: "согласована",
        RequestStatus.REJECTED: "отклонена",
        RequestStatus.SLOT_UNAVAILABLE: "слот недоступен",
        RequestStatus.RESERVATION_EXPIRED: "резерв истек",
        RequestStatus.EVENT_CREATION_ERROR: "ошибка создания события",
    }
    return labels.get(status, status.value)


def build_user_requests_text(items: list[ConsultationRequest]) -> str:
    if not items:
        return "Заявок пока нет."
    lines = ["Ваши заявки:"]
    for item in items:
        lines.append(
            f"#{item.id} | {item.meeting_date:%d.%m.%Y} "
                f"{item.start_time.strftime('%H:%M')}-{item.end_time.strftime('%H:%M')} | "
                f"{request_status_label(item.status)}"
        )
    return "\n".join(lines)


def slot_rules_from_settings(settings: ScheduleSettings) -> SlotRules:
    return SlotRules(
        timezone=settings.timezone,
        working_day_start=settings.workday_start,
        working_day_end=settings.workday_end,
        min_notice_minutes=settings.min_notice_minutes,
        buffer_minutes=settings.buffer_minutes,
        max_consultations_per_day=settings.max_consultations_per_day,
        booking_horizon_days=settings.booking_horizon_days,
    )


async def get_schedule_settings_or_fail(session: AsyncSession) -> ScheduleSettings:
    return await get_schedule_settings(session)


async def calculate_slots_for_date(
    session: AsyncSession,
    meeting_date: date,
    duration_minutes: int,
    rules: SlotRules,
    now: datetime | None = None,
    exclude_request_id: int | None = None,
    external_occupied_intervals: list[TimeInterval] | None = None,
) -> list[SlotChoice]:
    active_reservations = await list_active_reservations_by_date(
        session=session,
        meeting_date=meeting_date,
        exclude_request_id=exclude_request_id,
    )
    occupied_intervals = [
        TimeInterval(start_at=item.start_at, end_at=item.end_at) for item in active_reservations
    ]
    if external_occupied_intervals:
        occupied_intervals.extend(external_occupied_intervals)
    consultations_count = await count_consultations_for_date(session, meeting_date)
    intervals = calculate_free_slots(
        target_date=meeting_date,
        duration_minutes=duration_minutes,
        rules=rules,
        now=now or datetime.now(UTC),
        occupied_intervals=occupied_intervals,
        consultations_already_planned_today=consultations_count,
    )
    return [SlotChoice(start_at=item.start_at, end_at=item.end_at) for item in intervals]


async def create_request_from_draft(
    session: AsyncSession,
    user: User,
    full_name: str,
    phone: str,
    email: str,
    meeting_goal: str,
    duration_minutes: int,
    slot_choice: SlotChoice,
    personal_data_consent: bool,
) -> ConsultationRequest:
    request = await create_consultation_request(
        session=session,
        user_id=user.id,
        full_name=full_name,
        phone=phone,
        email=email,
        meeting_goal=meeting_goal,
        duration_minutes=duration_minutes,
        meeting_date=slot_choice.start_at.date(),
        start_time=slot_choice.start_at.timetz().replace(tzinfo=None),
        end_time=slot_choice.end_at.timetz().replace(tzinfo=None),
        personal_data_consent=personal_data_consent,
        status=RequestStatus.PENDING_APPROVAL,
    )
    reservation = reserve_request_slot(
        request=request,
        start_at=slot_choice.start_at,
        end_at=slot_choice.end_at,
    )
    session.add(reservation)
    await append_request_status_history(
        session=session,
        request_id=request.id,
        status=RequestStatus.PENDING_APPROVAL,
        changed_by_role=RequestChangedByRole.USER,
        changed_by_telegram_id=user.telegram_user_id,
        comment="request_created_by_user",
    )
    await session.flush()
    return request


async def get_user_requests(session: AsyncSession, user_id: int) -> list[ConsultationRequest]:
    return await list_requests_by_user_id(session, user_id)


async def update_request_goal_for_user(
    session: AsyncSession,
    request_id: int,
    user_id: int,
    telegram_user_id: int,
    new_goal: str,
) -> ConsultationRequest:
    request = await get_request_by_id_and_user_id(session, request_id=request_id, user_id=user_id)
    if request is None:
        raise LookupError("Request not found.")
    updated_request = update_request_before_approval(request, meeting_goal=new_goal)
    await append_request_status_history(
        session=session,
        request_id=updated_request.id,
        status=updated_request.status,
        changed_by_role=RequestChangedByRole.USER,
        changed_by_telegram_id=telegram_user_id,
        comment="request_goal_updated_by_user",
    )
    await session.flush()
    return updated_request


async def cancel_request_for_user(
    session: AsyncSession,
    request_id: int,
    user_id: int,
    telegram_user_id: int,
) -> ConsultationRequest:
    request = await get_request_by_id_and_user_id(session, request_id=request_id, user_id=user_id)
    if request is None:
        raise LookupError("Request not found.")
    canceled_request = cancel_request_before_approval(request)
    active_reservation = await get_active_reservation_by_request_id(session, canceled_request.id)
    if active_reservation is not None:
        await release_slot_reservation(
            session=session,
            reservation=active_reservation,
            released_status=ReservationStatus.RELEASED,
        )
    await append_request_status_history(
        session=session,
        request_id=canceled_request.id,
        status=canceled_request.status,
        changed_by_role=RequestChangedByRole.USER,
        changed_by_telegram_id=telegram_user_id,
        comment="request_canceled_by_user",
    )
    await session.flush()
    return canceled_request


def is_request_editable(status: RequestStatus) -> bool:
    return status in {
        RequestStatus.DRAFT,
        RequestStatus.PENDING_APPROVAL,
        RequestStatus.UPDATED_BY_USER,
    }


def can_submit_with_consent(consent_given: bool) -> bool:
    return consent_given


def ensure_slot_still_available(
    selected_slot: SlotChoice,
    available_slots: list[SlotChoice],
) -> None:
    for slot in available_slots:
        if slot.start_at == selected_slot.start_at and slot.end_at == selected_slot.end_at:
            return
    raise BusinessRuleViolation("Selected slot is no longer available.")


async def ensure_active_request_limit_not_exceeded(
    session: AsyncSession,
    user_id: int,
    max_active_requests_per_user: int,
) -> None:
    if max_active_requests_per_user <= 0:
        return
    active_requests_count = await count_active_requests_by_user_id(session, user_id=user_id)
    if active_requests_count >= max_active_requests_per_user:
        logger.warning(
            "Active requests limit reached for user.",
            extra={
                "event": "active_requests_limit_reached",
                "user_id": user_id,
                "active_requests_count": active_requests_count,
                "max_active_requests_per_user": max_active_requests_per_user,
            },
        )
        raise BusinessRuleViolation(
            "У вас уже есть активная заявка. Дождитесь ее обработки или отмените ее в разделе "
            "'Мои заявки'."
        )


async def request_and_anonymize_user_data(
    session: AsyncSession,
    user: User,
    telegram_user_id: int,
) -> dict[str, int]:
    requests = await list_requests_by_user_id(session, user_id=user.id)
    canceled_requests = 0
    for request in requests:
        if request.status in {
            RequestStatus.DRAFT,
            RequestStatus.PENDING_APPROVAL,
            RequestStatus.UPDATED_BY_USER,
        }:
            request.status = RequestStatus.CANCELED_BY_USER
            request.reservation_expires_at = None
            active_reservation = await get_active_reservation_by_request_id(session, request.id)
            if active_reservation is not None:
                await release_slot_reservation(
                    session=session,
                    reservation=active_reservation,
                    released_status=ReservationStatus.RELEASED,
                )
            await append_request_status_history(
                session=session,
                request_id=request.id,
                status=RequestStatus.CANCELED_BY_USER,
                changed_by_role=RequestChangedByRole.USER,
                changed_by_telegram_id=telegram_user_id,
                comment="request_canceled_due_to_data_deletion",
            )
            canceled_requests += 1

    _, anonymized_requests = await anonymize_user_personal_data(session=session, user_id=user.id)
    return {
        "canceled_requests": canceled_requests,
        "anonymized_requests": anonymized_requests,
    }
