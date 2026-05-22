import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from app.db.enums import RequestStatus, ReservationStatus
from app.db.models import ConsultationRequest, SlotReservation, User
from app.domain.exceptions import BusinessRuleViolation

logger = logging.getLogger(__name__)

EDITABLE_REQUEST_STATUSES = {
    RequestStatus.DRAFT,
    RequestStatus.PENDING_APPROVAL,
    RequestStatus.UPDATED_BY_USER,
}


@dataclass
class BookingDraftState:
    duration_minutes: int | None = None
    selected_date: date | None = None
    slot_start_time: time | None = None
    slot_end_time: time | None = None


def update_draft_duration(draft: BookingDraftState, duration_minutes: int) -> BookingDraftState:
    if draft.duration_minutes != duration_minutes:
        draft.duration_minutes = duration_minutes
        draft.selected_date = None
        draft.slot_start_time = None
        draft.slot_end_time = None
    return draft


def update_draft_date(draft: BookingDraftState, selected_date: date) -> BookingDraftState:
    if draft.selected_date != selected_date:
        draft.selected_date = selected_date
        draft.slot_start_time = None
        draft.slot_end_time = None
    return draft


def _ensure_request_is_editable(request: ConsultationRequest, action: str) -> None:
    if request.status not in EDITABLE_REQUEST_STATUSES:
        logger.warning(
            "Business rule blocked action.",
            extra={
                "event": "business_rule_blocked",
                "request_id": request.id,
                "action": action,
                "status": request.status.value,
            },
        )
        raise BusinessRuleViolation(
            f"Action '{action}' is not allowed for request status '{request.status.value}'."
        )


def reserve_request_slot(
    request: ConsultationRequest,
    start_at: datetime,
    end_at: datetime,
    ttl_hours: int = 24,
) -> SlotReservation:
    _ensure_request_is_editable(request, action="reserve_slot")

    now = datetime.now(UTC)
    reservation = SlotReservation(
        request_id=request.id,
        start_at=start_at,
        end_at=end_at,
        expires_at=now + timedelta(hours=ttl_hours),
        status=ReservationStatus.ACTIVE,
        released_at=None,
        created_at=now,
        updated_at=now,
    )
    request.meeting_date = start_at.date()
    request.start_time = start_at.timetz().replace(tzinfo=None)
    request.end_time = end_at.timetz().replace(tzinfo=None)
    request.reservation_expires_at = reservation.expires_at
    logger.info(
        "Slot reserved.",
        extra={"event": "slot_reserved", "request_id": request.id},
    )
    return reservation


def mark_reservation_expired(request: ConsultationRequest, reservation: SlotReservation) -> None:
    reservation.status = ReservationStatus.EXPIRED
    reservation.released_at = datetime.now(UTC)
    reservation.updated_at = datetime.now(UTC)
    request.status = RequestStatus.RESERVATION_EXPIRED
    request.reservation_expires_at = None
    logger.info(
        "Reservation expired.",
        extra={
            "event": "reservation_expired",
            "request_id": request.id,
            "reservation_id": reservation.id,
        },
    )


def update_request_before_approval(
    request: ConsultationRequest,
    full_name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    meeting_goal: str | None = None,
) -> ConsultationRequest:
    _ensure_request_is_editable(request, action="update_request")
    if full_name is not None:
        request.full_name = full_name
    if phone is not None:
        request.phone = phone
    if email is not None:
        request.email = email
    if meeting_goal is not None:
        request.meeting_goal = meeting_goal
    request.status = RequestStatus.UPDATED_BY_USER
    logger.info(
        "Request updated.",
        extra={"event": "request_updated", "request_id": request.id},
    )
    return request


def cancel_request_before_approval(request: ConsultationRequest) -> ConsultationRequest:
    _ensure_request_is_editable(request, action="cancel_request")
    request.status = RequestStatus.CANCELED_BY_USER
    request.reservation_expires_at = None
    logger.info(
        "Request canceled by user.",
        extra={"event": "request_canceled", "request_id": request.id},
    )
    return request


def request_user_data_deletion(user: User) -> User:
    user.data_deletion_requested_at = datetime.now(UTC)
    logger.info(
        "User requested data deletion.",
        extra={
            "event": "user_data_deletion_requested",
            "telegram_user_id": user.telegram_user_id,
        },
    )
    return user
