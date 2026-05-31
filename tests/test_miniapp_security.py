from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from aiogram import Dispatcher
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.miniapp.sessions import MiniAppSession


@contextmanager
def _create_test_client(settings: Settings):
    with patch("app.main.create_dispatcher", return_value=Dispatcher()):
        with patch("app.main.create_bot", return_value=None):
            app = create_app(settings)
            with TestClient(app) as client:
                yield client


def test_protected_route_requires_valid_bearer_token() -> None:
    settings = Settings(
        app_skip_external_checks=True,
        miniapp_enabled=True,
        telegram_polling_enabled=False,
    )
    with _create_test_client(settings) as client:
        missing = client.get("/api/miniapp/me")
        malformed = client.get("/api/miniapp/me", headers={"Authorization": "Basic demo"})

    assert missing.status_code == 401
    assert missing.json()["detail"] == "Unauthorized"
    assert malformed.status_code == 401
    assert malformed.json()["detail"] == "Unauthorized"


def test_protected_route_rejects_expired_session() -> None:
    settings = Settings(
        app_skip_external_checks=True,
        miniapp_enabled=True,
        telegram_polling_enabled=False,
    )
    with _create_test_client(settings) as client:
        app = client.app
        store = app.state.miniapp_sessions
        session = store.create(telegram_user_id=91001, role="user", ttl_minutes=1)
        store._sessions[session.token] = MiniAppSession(
            token=session.token,
            telegram_user_id=session.telegram_user_id,
            role=session.role,
            created_at=session.created_at - timedelta(minutes=2),
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )

        response = client.get(
            "/api/miniapp/me",
            headers={"Authorization": f"Bearer {session.token}"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Session expired"


def test_admin_endpoints_require_admin_role() -> None:
    settings = Settings(
        app_skip_external_checks=True,
        miniapp_enabled=True,
        telegram_polling_enabled=False,
    )
    with _create_test_client(settings) as client:
        app = client.app
        store = app.state.miniapp_sessions
        session = store.create(telegram_user_id=92001, role="user", ttl_minutes=10)
        response = client.get(
            "/api/miniapp/admin/requests",
            headers={"Authorization": f"Bearer {session.token}"},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Admin access required"


def test_dev_login_is_blocked_when_feature_flag_is_disabled() -> None:
    settings = Settings(
        app_skip_external_checks=True,
        miniapp_enabled=True,
        miniapp_dev_login_enabled=False,
        telegram_polling_enabled=False,
    )
    with _create_test_client(settings) as client:
        response = client.post(
            "/api/miniapp/auth/dev-login",
            json={"telegram_user_id": 93001},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Dev login is disabled."


def test_dev_login_is_blocked_outside_dev_environment() -> None:
    settings = Settings(
        app_skip_external_checks=True,
        app_env="prod",
        miniapp_enabled=True,
        miniapp_dev_login_enabled=True,
        telegram_polling_enabled=False,
    )
    with _create_test_client(settings) as client:
        response = client.post(
            "/api/miniapp/auth/dev-login",
            json={"telegram_user_id": 94001},
        )

    assert response.status_code == 403
    assert response.json()["detail"] == "Dev login is allowed only in dev environment."
