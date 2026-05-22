import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

SLOT_STEP_MINUTES = 15
FALLBACK_TIMEZONE_OFFSETS = {
    "UTC": timedelta(0),
    "Etc/UTC": timedelta(0),
    "Asia/Yekaterinburg": timedelta(hours=5),
}


@dataclass(frozen=True)
class TimeInterval:
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        if self.start_at >= self.end_at:
            raise ValueError("Interval start must be earlier than end.")


@dataclass(frozen=True)
class WeekWindow:
    week_start: date
    week_end: date
    days: list[date]
    can_go_prev: bool
    can_go_next: bool


@dataclass(frozen=True)
class SlotRules:
    timezone: str = "Asia/Yekaterinburg"
    working_day_start: time = time(hour=10, minute=0)
    working_day_end: time = time(hour=18, minute=0)
    min_notice_minutes: int = 120
    buffer_minutes: int = 60
    max_consultations_per_day: int = 3
    booking_horizon_days: int = 28


def _week_start_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_week_window(today: date, week_offset: int, booking_horizon_days: int) -> WeekWindow:
    current_week_start = _week_start_monday(today)
    target_week_start = current_week_start + timedelta(days=week_offset * 7)
    target_week_end = target_week_start + timedelta(days=6)
    horizon_end = today + timedelta(days=booking_horizon_days)

    if target_week_start > horizon_end:
        raise ValueError("Requested week is outside booking horizon.")

    visible_start = max(target_week_start, today)
    visible_end = min(target_week_end, horizon_end)
    days: list[date] = []
    cursor = visible_start
    while cursor <= visible_end:
        days.append(cursor)
        cursor += timedelta(days=1)

    can_go_prev = week_offset > 0
    can_go_next = target_week_end < horizon_end

    return WeekWindow(
        week_start=target_week_start,
        week_end=target_week_end,
        days=days,
        can_go_prev=can_go_prev,
        can_go_next=can_go_next,
    )


def _with_timezone(moment: datetime, timezone: str) -> datetime:
    tz = _resolve_timezone(timezone)
    if moment.tzinfo is None:
        return moment.replace(tzinfo=tz)
    return moment.astimezone(tz)


def _make_day_bounds(target_date: date, rules: SlotRules) -> tuple[datetime, datetime]:
    tz = _resolve_timezone(rules.timezone)
    day_start = datetime.combine(target_date, rules.working_day_start, tzinfo=tz)
    day_end = datetime.combine(target_date, rules.working_day_end, tzinfo=tz)
    return day_start, day_end


def _resolve_timezone(timezone_name: str):
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        fallback_offset = FALLBACK_TIMEZONE_OFFSETS.get(timezone_name)
        if fallback_offset is None:
            raise
        logger.warning(
            "ZoneInfo database unavailable, using fixed-offset timezone fallback.",
            extra={"event": "timezone_fallback_used", "timezone": timezone_name},
        )
        return timezone(fallback_offset, name=timezone_name)


def _apply_buffer(interval: TimeInterval, minutes: int) -> TimeInterval:
    delta = timedelta(minutes=minutes)
    return TimeInterval(start_at=interval.start_at - delta, end_at=interval.end_at + delta)


def _intersects(left: TimeInterval, right: TimeInterval) -> bool:
    return left.start_at < right.end_at and right.start_at < left.end_at


def calculate_free_slots(
    target_date: date,
    duration_minutes: int,
    rules: SlotRules,
    now: datetime | None,
    occupied_intervals: list[TimeInterval],
    consultations_already_planned_today: int,
) -> list[TimeInterval]:
    now_value = _with_timezone(now or datetime.now(UTC), rules.timezone)
    logger.info(
        "Slot calculation started.",
        extra={
            "event": "slot_calculation_started",
            "target_date": str(target_date),
            "duration_minutes": duration_minutes,
        },
    )

    if consultations_already_planned_today >= rules.max_consultations_per_day:
        logger.warning(
            "Business rule blocked action: daily consultations limit reached.",
            extra={
                "event": "business_rule_blocked",
                "rule": "max_consultations_per_day",
                "limit": rules.max_consultations_per_day,
            },
        )
        logger.info(
            "Slot calculation completed.",
            extra={"event": "slot_calculation_completed", "slots_count": 0},
        )
        logger.warning(
            "No free slots found.",
            extra={"event": "free_slots_not_found", "target_date": str(target_date)},
        )
        return []

    day_start, day_end = _make_day_bounds(target_date, rules)
    minimal_allowed_start = max(day_start, now_value + timedelta(minutes=rules.min_notice_minutes))
    slot_step = timedelta(minutes=SLOT_STEP_MINUTES)
    duration_delta = timedelta(minutes=duration_minutes)

    blocked_intervals = []
    for interval in occupied_intervals:
        normalized = TimeInterval(
            _with_timezone(interval.start_at, rules.timezone),
            _with_timezone(interval.end_at, rules.timezone),
        )
        blocked_intervals.append(_apply_buffer(normalized, rules.buffer_minutes))

    free_slots: list[TimeInterval] = []
    cursor = minimal_allowed_start
    while cursor + duration_delta <= day_end:
        candidate = TimeInterval(start_at=cursor, end_at=cursor + duration_delta)
        has_intersection = any(_intersects(candidate, blocked) for blocked in blocked_intervals)
        if not has_intersection:
            free_slots.append(candidate)
        cursor += slot_step

    logger.info(
        "Slot calculation completed.",
        extra={"event": "slot_calculation_completed", "slots_count": len(free_slots)},
    )
    if not free_slots:
        logger.warning(
            "No free slots found.",
            extra={"event": "free_slots_not_found", "target_date": str(target_date)},
        )
    return free_slots
