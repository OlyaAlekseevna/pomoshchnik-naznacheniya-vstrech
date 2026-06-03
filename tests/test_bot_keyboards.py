from datetime import date

import pytest

from app.bot.keyboards import (
    BOOK_TEXT,
    OPEN_MINIAPP_TEXT,
    admin_durations_keyboard,
    admin_forbidden_date_keyboard,
    admin_working_days_keyboard,
    admin_working_hours_keyboard,
    main_menu_keyboard,
)
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_main_menu_keeps_default_buttons_when_miniapp_disabled(monkeypatch) -> None:
    monkeypatch.setenv("MINIAPP_ENABLED", "false")
    monkeypatch.setenv("MINIAPP_DOMAIN", "calendar.monvera.su")

    keyboard = main_menu_keyboard()

    assert keyboard.keyboard[0][0].text == BOOK_TEXT
    assert keyboard.keyboard[0][0].web_app is None


def test_main_menu_adds_webapp_button_when_miniapp_domain_configured(monkeypatch) -> None:
    monkeypatch.setenv("MINIAPP_ENABLED", "true")
    monkeypatch.setenv("MINIAPP_DOMAIN", "calendar.monvera.su/")

    keyboard = main_menu_keyboard()
    first_button = keyboard.keyboard[0][0]

    assert first_button.text == OPEN_MINIAPP_TEXT
    assert first_button.web_app is not None
    assert first_button.web_app.url == (
        "https://calendar.monvera.su/miniapp?v=20260602-telegram-auth"
    )


def test_admin_working_days_keyboard_marks_selected_days() -> None:
    keyboard = admin_working_days_keyboard(["monday", "wednesday", "friday"])
    button_texts = [button.text for row in keyboard.inline_keyboard for button in row]

    assert "✓ ПН" in button_texts
    assert "  ВТ" in button_texts
    assert "✓ СР" in button_texts
    assert "✓ ПТ" in button_texts
    assert "Сохранить" in button_texts
    assert "Отмена" in button_texts


def test_admin_working_hours_keyboard_contains_presets_and_manual() -> None:
    keyboard = admin_working_hours_keyboard()
    button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert "09:00-18:00" in button_texts
    assert "10:00-19:00" in button_texts
    assert "Ввести вручную" in button_texts
    assert "Отмена" in button_texts
    assert "admin:hours:set:09:00-18:00" in callbacks
    assert "admin:hours:manual" in callbacks
    assert "admin:hours:cancel" in callbacks


def test_admin_durations_keyboard_marks_selected_options() -> None:
    keyboard = admin_durations_keyboard([30, 60])
    button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert "  15 мин" in button_texts
    assert "✓ 30 мин" in button_texts
    assert "  45 мин" in button_texts
    assert "✓ 60 мин" in button_texts
    assert "Сохранить" in button_texts
    assert "Отмена" in button_texts
    assert "admin:durations:toggle:30" in callbacks
    assert "admin:durations:save" in callbacks
    assert "admin:durations:cancel" in callbacks


def test_admin_forbidden_date_keyboard_uses_next_dates() -> None:
    keyboard = admin_forbidden_date_keyboard(date(2026, 6, 3), days_count=3)
    button_texts = [button.text for row in keyboard.inline_keyboard for button in row]
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]

    assert "Ср 03.06" in button_texts
    assert "Чт 04.06" in button_texts
    assert "Пт 05.06" in button_texts
    assert "Ввести вручную" in button_texts
    assert "Отмена" in button_texts
    assert "admin:forbid_date:add:2026-06-03" in callbacks
    assert "admin:forbid_date:add:2026-06-05" in callbacks
    assert "admin:forbid_date:manual" in callbacks
    assert "admin:forbid_date:cancel" in callbacks
