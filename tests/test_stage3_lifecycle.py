from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import MagicMock, patch

import pytest

from app.db.enums import RequestStatus, ReservationStatus
from app.db.models import ConsultationRequest, User
from app.domain.exceptions import BusinessRuleViolation
from app.domain.lifecycle import (
    BookingDraftState,
    cancel_request_before_approval,
    mark_reservation_expired,
    request_user_data_deletion,
    reserve_request_slot,
    update_draft_date,
    update_draft_duration,
    update_request_before_approval,
)


def _build_request(status: RequestStatus = RequestStatus.DRAFT) -> ConsultationRequest:
    now = datetime.now(UTC)
    return ConsultationRequest(
        id=101,
        user_id=1,
        full_name="Test User",
        phone="+79000000000",
        email="test@example.com",
        meeting_goal="Need consultation",
        duration_minutes=30,
        meeting_date=date(2026, 5, 23),
        start_time=time(10, 0),
        end_time=time(10, 30),
        status=status,
        rejection_reason=None,
        alternative_date=None,
        alternative_start_time=None,
        alternative_end_time=None,
        reservation_expires_at=None,
        personal_data_consent=True,
        created_at=now,
        updated_at=now,
    )


def _build_user() -> User:
    now = datetime.now(UTC)
    return User(
        id=1,
        telegram_user_id=7000000002,
        username="test_user",
        first_name="Test",
        last_name="User",
        first_seen_at=now,
        invited_access_granted=True,
        is_blocked=False,
        data_deletion_requested_at=None,
        created_at=now,
        updated_at=now,
    )


def _extract_events(mock_logger: MagicMock) -> list[str | None]:
    events: list[str | None] = []
    for method_name in ("info", "warning", "error", "exception"):
        method = getattr(mock_logger, method_name)
        for call in method.call_args_list:
            extra = call.kwargs.get("extra", {})
            events.append(extra.get("event"))
    return events


def test_duration_change_resets_date_and_slot() -> None:
    draft = BookingDraftState(
        duration_minutes=30,
        selected_date=date(2026, 5, 23),
        slot_start_time=time(11, 0),
        slot_end_time=time(11, 30),
    )

    updated = update_draft_duration(draft, duration_minutes=45)

    assert updated.duration_minutes == 45
    assert updated.selected_date is None
    assert updated.slot_start_time is None
    assert updated.slot_end_time is None


def test_date_change_resets_slot_only() -> None:
    draft = BookingDraftState(
        duration_minutes=30,
        selected_date=date(2026, 5, 23),
        slot_start_time=time(11, 0),
        slot_end_time=time(11, 30),
    )

    updated = update_draft_date(draft, selected_date=date(2026, 5, 24))

    assert updated.duration_minutes == 30
    assert updated.selected_date == date(2026, 5, 24)
    assert updated.slot_start_time is None
    assert updated.slot_end_time is None


def test_reserve_slot_sets_ttl_and_logs() -> None:
    request = _build_request(status=RequestStatus.DRAFT)
    start_at = datetime(2026, 5, 24, 10, 0, tzinfo=UTC)
    end_at = datetime(2026, 5, 24, 10, 30, tzinfo=UTC)
    before = datetime.now(UTC)

    with patch("app.domain.lifecycle.logger") as mock_logger:
        reservation = reserve_request_slot(request, start_at=start_at, end_at=end_at)

    after = datetime.now(UTC)
    assert reservation.request_id == request.id
    assert reservation.status == ReservationStatus.ACTIVE
    assert reservation.expires_at >= before + timedelta(hours=24)
    assert reservation.expires_at <= after + timedelta(hours=24)
    assert request.meeting_date == date(2026, 5, 24)
    assert request.start_time == time(10, 0)
    assert request.end_time == time(10, 30)
    assert request.reservation_expires_at == reservation.expires_at
    assert "slot_reserved" in _extract_events(mock_logger)


def test_reservation_expiration_changes_statuses_and_logs() -> None:
    request = _build_request(status=RequestStatus.PENDING_APPROVAL)
    reservation = reserve_request_slot(
        request=request,
        start_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
        end_at=datetime(2026, 5, 24, 10, 30, tzinfo=UTC),
    )
    reservation.id = 501

    with patch("app.domain.lifecycle.logger") as mock_logger:
        mark_reservation_expired(request, reservation)

    assert reservation.status == ReservationStatus.EXPIRED
    assert reservation.released_at is not None
    assert request.status == RequestStatus.RESERVATION_EXPIRED
    assert request.reservation_expires_at is None
    assert "reservation_expired" in _extract_events(mock_logger)


def test_update_and_cancel_request_before_approval_logs_events() -> None:
    request = _build_request(status=RequestStatus.PENDING_APPROVAL)

    with patch("app.domain.lifecycle.logger") as mock_logger:
        updated = update_request_before_approval(
            request,
            full_name="Updated User",
            phone="+79000000001",
            email="updated@example.com",
            meeting_goal="Need another topic",
        )
        canceled = cancel_request_before_approval(updated)

    assert canceled.full_name == "Updated User"
    assert canceled.phone == "+79000000001"
    assert canceled.email == "updated@example.com"
    assert canceled.meeting_goal == "Need another topic"
    assert canceled.status == RequestStatus.CANCELED_BY_USER
    assert canceled.reservation_expires_at is None
    events = _extract_events(mock_logger)
    assert "request_updated" in events
    assert "request_canceled" in events


def test_non_editable_request_action_raises_and_logs_block() -> None:
    request = _build_request(status=RequestStatus.APPROVED)

    with patch("app.domain.lifecycle.logger") as mock_logger:
        with pytest.raises(BusinessRuleViolation):
            reserve_request_slot(
                request=request,
                start_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
                end_at=datetime(2026, 5, 24, 10, 30, tzinfo=UTC),
            )

    assert "business_rule_blocked" in _extract_events(mock_logger)


def test_data_deletion_request_sets_timestamp_and_logs() -> None:
    user = _build_user()

    with patch("app.domain.lifecycle.logger") as mock_logger:
        updated = request_user_data_deletion(user)

    assert updated.data_deletion_requested_at is not None
    assert "user_data_deletion_requested" in _extract_events(mock_logger)
