from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

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


class _DummySession:
    async def __aenter__(self) -> "_DummySession":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def commit(self) -> None:
        return None


def _dummy_session_factory() -> _DummySession:
    return _DummySession()


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


def test_dev_login_returns_admin_role_when_telegram_id_matches_admin_setting() -> None:
    admin_telegram_id = 9007199254740993
    settings = Settings(
        app_skip_external_checks=True,
        miniapp_enabled=True,
        miniapp_dev_login_enabled=True,
        telegram_polling_enabled=False,
        telegram_admin_id=admin_telegram_id,
    )
    with _create_test_client(settings) as client:
        client.app.state.session_factory = _dummy_session_factory
        with patch(
            "app.miniapp.router.get_or_create_user_by_telegram_id",
            new=AsyncMock(return_value=None),
        ):
            response = client.post(
                "/api/miniapp/auth/dev-login",
                json={"telegram_user_id": str(admin_telegram_id)},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["telegram_user_id"] == admin_telegram_id
    assert payload["role"] == "admin"


def test_dev_login_returns_user_role_when_telegram_id_differs_from_admin_setting() -> None:
    settings = Settings(
        app_skip_external_checks=True,
        miniapp_enabled=True,
        miniapp_dev_login_enabled=True,
        telegram_polling_enabled=False,
        telegram_admin_id=7001,
    )
    with _create_test_client(settings) as client:
        client.app.state.session_factory = _dummy_session_factory
        with patch(
            "app.miniapp.router.get_or_create_user_by_telegram_id",
            new=AsyncMock(return_value=None),
        ):
            response = client.post(
                "/api/miniapp/auth/dev-login",
                json={"telegram_user_id": "7002"},
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["telegram_user_id"] == 7002
    assert payload["role"] == "user"
