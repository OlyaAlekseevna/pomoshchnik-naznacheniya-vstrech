from __future__ import annotations

from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.bot.user_flow_service import SlotChoice
from app.domain.scheduling import WeekWindow

DURATIONS_MINUTES = (15, 30, 45, 90)
BACK_TEXT = "Back"
BOOK_TEXT = "Book consultation"
MY_REQUESTS_TEXT = "My requests"
CONSENT_TEXT = "I agree"
SUBMIT_TEXT = "Submit request"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BOOK_TEXT)],
            [KeyboardButton(text=MY_REQUESTS_TEXT)],
        ],
        resize_keyboard=True,
    )


def back_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=BACK_TEXT)]],
        resize_keyboard=True,
    )


def consultation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Consultation", callback_data="consultation:select")],
            [InlineKeyboardButton(text=BACK_TEXT, callback_data="nav:to_menu")],
        ]
    )


def duration_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{duration} min",
                callback_data=f"duration:{duration}",
            )
        ]
        for duration in DURATIONS_MINUTES
    ]
    rows.append([InlineKeyboardButton(text=BACK_TEXT, callback_data="nav:to_consultation")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dates_keyboard(week: WeekWindow, week_offset: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item in week.days:
        rows.append(
            [
                InlineKeyboardButton(
                    text=item.strftime("%a %d.%m"),
                    callback_data=f"date:{item.isoformat()}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if week.can_go_prev:
        nav_row.append(
            InlineKeyboardButton(text="Prev week", callback_data=f"week:{week_offset - 1}")
        )
    if week.can_go_next:
        nav_row.append(
            InlineKeyboardButton(text="Next week", callback_data=f"week:{week_offset + 1}")
        )
    if nav_row:
        rows.append(nav_row)
    rows.append([InlineKeyboardButton(text=BACK_TEXT, callback_data="nav:to_duration")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def slots_keyboard(slots: list[SlotChoice]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=slot.label, callback_data=f"slot:{slot.encoded}")]
        for slot in slots
    ]
    rows.append([InlineKeyboardButton(text=BACK_TEXT, callback_data="nav:to_date")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def consent_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=CONSENT_TEXT, callback_data="consent:yes")],
            [InlineKeyboardButton(text=BACK_TEXT, callback_data="nav:to_goal")],
        ]
    )


def summary_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=SUBMIT_TEXT, callback_data="submit:request")],
            [InlineKeyboardButton(text=BACK_TEXT, callback_data="nav:to_consent")],
        ]
    )


def request_actions_keyboard(request_id: int, editable: bool) -> InlineKeyboardMarkup | None:
    if not editable:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Edit goal", callback_data=f"req_edit:{request_id}"),
                InlineKeyboardButton(text="Cancel", callback_data=f"req_cancel:{request_id}"),
            ]
        ]
    )


def week_title(week_start: date, week_end: date) -> str:
    return f"Week: {week_start:%d.%m} - {week_end:%d.%m}"
