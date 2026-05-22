from datetime import UTC, date, datetime, time
from unittest.mock import MagicMock, patch

import pytest

from app.domain.scheduling import (
    SlotRules,
    TimeInterval,
    build_week_window,
    calculate_free_slots,
)


def _extract_events(mock_logger: MagicMock) -> list[str | None]:
    events: list[str | None] = []
    for method_name in ("info", "warning", "error", "exception"):
        method = getattr(mock_logger, method_name)
        for call in method.call_args_list:
            extra = call.kwargs.get("extra", {})
            events.append(extra.get("event"))
    return events


def test_week_window_starts_monday_and_ends_sunday() -> None:
    window = build_week_window(today=date(2026, 5, 22), week_offset=0, booking_horizon_days=28)
    assert window.week_start == date(2026, 5, 18)  # Monday
    assert window.week_end == date(2026, 5, 24)  # Sunday
    assert window.days[0] == date(2026, 5, 22)  # past days are filtered out


def test_week_navigation_respects_horizon() -> None:
    today = date(2026, 5, 22)
    window = build_week_window(today=today, week_offset=4, booking_horizon_days=28)
    assert window.can_go_prev is True
    assert window.can_go_next is False
    with pytest.raises(ValueError):
        build_week_window(today=today, week_offset=5, booking_horizon_days=28)


def test_slot_calculation_for_15_and_90_minutes_with_buffer() -> None:
    rules = SlotRules(timezone="UTC")
    target_day = date(2026, 5, 23)
    now = datetime(2026, 5, 23, 7, 0, tzinfo=UTC)
    occupied = [
        TimeInterval(
            start_at=datetime(2026, 5, 23, 13, 0, tzinfo=UTC),
            end_at=datetime(2026, 5, 23, 14, 0, tzinfo=UTC),
        )
    ]

    slots_15 = calculate_free_slots(
        target_date=target_day,
        duration_minutes=15,
        rules=rules,
        now=now,
        occupied_intervals=occupied,
        consultations_already_planned_today=0,
    )
    slots_90 = calculate_free_slots(
        target_date=target_day,
        duration_minutes=90,
        rules=rules,
        now=now,
        occupied_intervals=occupied,
        consultations_already_planned_today=0,
    )

    assert any(slot.start_at.time() == time(11, 30) for slot in slots_15)
    assert not any(time(12, 0) <= slot.start_at.time() < time(15, 0) for slot in slots_15)
    assert all(
        slot.end_at.time() <= time(12, 0) or slot.start_at.time() >= time(15, 0)
        for slot in slots_90
    )


def test_min_notice_two_hours_rule() -> None:
    rules = SlotRules(timezone="UTC")
    target_day = date(2026, 5, 23)
    now = datetime(2026, 5, 23, 9, 30, tzinfo=UTC)

    slots = calculate_free_slots(
        target_date=target_day,
        duration_minutes=30,
        rules=rules,
        now=now,
        occupied_intervals=[],
        consultations_already_planned_today=0,
    )

    assert slots
    assert slots[0].start_at.time() >= time(11, 30)


def test_daily_limit_three_consultations_rule_and_logging() -> None:
    rules = SlotRules(timezone="UTC", max_consultations_per_day=3)
    target_day = date(2026, 5, 23)
    now = datetime(2026, 5, 23, 8, 0, tzinfo=UTC)

    with patch("app.domain.scheduling.logger") as mock_logger:
        slots = calculate_free_slots(
            target_date=target_day,
            duration_minutes=30,
            rules=rules,
            now=now,
            occupied_intervals=[],
            consultations_already_planned_today=3,
        )

    assert slots == []
    events = _extract_events(mock_logger)
    assert "slot_calculation_started" in events
    assert "business_rule_blocked" in events
    assert "slot_calculation_completed" in events
    assert "free_slots_not_found" in events
