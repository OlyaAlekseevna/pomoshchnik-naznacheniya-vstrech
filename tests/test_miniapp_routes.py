from unittest.mock import patch

from aiogram import Dispatcher
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def test_miniapp_routes_available_when_enabled() -> None:
    with patch("app.main.create_dispatcher", return_value=Dispatcher()):
        with patch("app.main.create_bot", return_value=None):
            app = create_app(
                Settings(
                    app_skip_external_checks=True,
                    miniapp_enabled=True,
                    telegram_polling_enabled=False,
                )
            )
            with TestClient(app) as client:
                health = client.get("/api/miniapp/health")
                assert health.status_code == 200
                assert health.json()["status"] == "ok"

                miniapp_page = client.get("/miniapp")
                assert miniapp_page.status_code == 200
                assert "Календарь встреч в Telegram" in miniapp_page.text
