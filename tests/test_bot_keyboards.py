import pytest

from app.bot.keyboards import (
    BOOK_TEXT,
    OPEN_MINIAPP_TEXT,
    admin_working_days_keyboard,
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
