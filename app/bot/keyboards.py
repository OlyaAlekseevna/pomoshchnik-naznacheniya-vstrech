from __future__ import annotations

from datetime import date

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from app.bot.user_flow_service import SlotChoice
from app.core.config import get_settings
from app.domain.scheduling import WeekWindow

DURATIONS_MINUTES = (15, 30, 45, 90)
BACK_TEXT = "Назад"
BOOK_TEXT = "Записаться на консультацию"
MY_REQUESTS_TEXT = "Мои заявки"
DELETE_MY_DATA_TEXT = "Удалить мои данные"
OPEN_MINIAPP_TEXT = "Открыть Mini App"
MINIAPP_URL_VERSION = "20260602-telegram-auth"
CONSENT_TEXT = "Согласен(на)"
SUBMIT_TEXT = "Отправить заявку"

_WEEKDAY_LABELS = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    settings = get_settings()
    keyboard = [
        [KeyboardButton(text=BOOK_TEXT)],
        [KeyboardButton(text=MY_REQUESTS_TEXT)],
        [KeyboardButton(text=DELETE_MY_DATA_TEXT)],
    ]
    if settings.miniapp_enabled and settings.miniapp_domain:
        miniapp_url = (
            f"https://{settings.miniapp_domain.strip().rstrip('/')}/miniapp"
            f"?v={MINIAPP_URL_VERSION}"
        )
        keyboard.insert(
            0,
            [KeyboardButton(text=OPEN_MINIAPP_TEXT, web_app=WebAppInfo(url=miniapp_url))],
        )

    return ReplyKeyboardMarkup(
        keyboard=keyboard,
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
            [InlineKeyboardButton(text="Консультация", callback_data="consultation:select")],
            [InlineKeyboardButton(text=BACK_TEXT, callback_data="nav:to_menu")],
        ]
    )


def duration_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{duration} мин",
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
                    text=f"{_WEEKDAY_LABELS[item.weekday()]} {item:%d.%m}",
                    callback_data=f"date:{item.isoformat()}",
                )
            ]
        )

    nav_row: list[InlineKeyboardButton] = []
    if week.can_go_prev:
        nav_row.append(
            InlineKeyboardButton(text="← Пред. неделя", callback_data=f"week:{week_offset - 1}")
        )
    if week.can_go_next:
        nav_row.append(
            InlineKeyboardButton(text="След. неделя →", callback_data=f"week:{week_offset + 1}")
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
                InlineKeyboardButton(text="Изменить цель", callback_data=f"req_edit:{request_id}"),
                InlineKeyboardButton(text="Отменить", callback_data=f"req_cancel:{request_id}"),
            ]
        ]
    )


def week_title(week_start: date, week_end: date) -> str:
    return f"Неделя: {week_start:%d.%m} - {week_end:%d.%m}"


def admin_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Заявки", callback_data="admin:req:list")],
            [InlineKeyboardButton(text="Настройки расписания", callback_data="admin:settings")],
        ]
    )


def admin_request_actions_keyboard(request_id: int, is_user_blocked: bool) -> InlineKeyboardMarkup:
    block_action = (
        "Разблокировать пользователя"
        if is_user_blocked
        else "Заблокировать пользователя"
    )
    block_callback = (
        f"admin:req:unblock_user:{request_id}"
        if is_user_blocked
        else f"admin:req:block_user:{request_id}"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Согласовать",
                    callback_data=f"admin:req:approve:{request_id}",
                ),
                InlineKeyboardButton(
                    text="Отклонить",
                    callback_data=f"admin:req:reject:{request_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Предложить другой слот",
                    callback_data=f"admin:req:alt_slot:{request_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="История статусов",
                    callback_data=f"admin:req:history:{request_id}",
                )
            ],
            [InlineKeyboardButton(text=block_action, callback_data=block_callback)],
            [
                InlineKeyboardButton(
                    text="Создать встречу вручную",
                    callback_data=f"admin:req:manual_create:{request_id}",
                )
            ],
            [InlineKeyboardButton(text="Назад в админ-меню", callback_data="admin:menu")],
        ]
    )


def admin_settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Рабочие дни",
                    callback_data="admin:set:working_days",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Рабочие часы",
                    callback_data="admin:set:working_hours",
                )
            ],
            [InlineKeyboardButton(text="Длительности", callback_data="admin:set:durations")],
            [
                InlineKeyboardButton(
                    text="Мин. время до встречи",
                    callback_data="admin:set:min_notice",
                )
            ],
            [InlineKeyboardButton(text="Буфер", callback_data="admin:set:buffer")],
            [
                InlineKeyboardButton(
                    text="Лимит встреч в день",
                    callback_data="admin:set:daily_limit",
                )
            ],
            [InlineKeyboardButton(text="Горизонт записи", callback_data="admin:set:horizon")],
            [
                InlineKeyboardButton(
                    text="Добавить запрещенную дату",
                    callback_data="admin:set:forbidden_date",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Добавить запрещенный период",
                    callback_data="admin:set:forbidden_period",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Текст уведомления о заявке",
                    callback_data="admin:set:new_request_text",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Подключить Google Calendar",
                    callback_data="admin:google:connect",
                )
            ],
            [InlineKeyboardButton(text="Назад в админ-меню", callback_data="admin:menu")],
        ]
    )
