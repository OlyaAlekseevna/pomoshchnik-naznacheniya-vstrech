from __future__ import annotations

import logging
from datetime import UTC, date, datetime, time, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.core.logging import StructuredLogDefaultsFilter
from app.db.base import Base
from app.db.enums import RequestStatus
from app.db.repositories import (
    create_consultation_request,
    get_or_create_user_by_telegram_id,
    upsert_google_oauth_credentials,
)
from app.main import _run_google_oauth_check
from app.services.google_calendar import GoogleAuthRequiredError, GoogleCalendarService


async def _create_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_structured_log_filter_adds_required_fields() -> None:
    record = logging.LogRecord(
        name="test_logger",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="test message",
        args=(),
        exc_info=None,
    )
    result = StructuredLogDefaultsFilter().filter(record)
    assert result is True
    assert record.event == "generic_log_event"
    assert record.action_type == "generic_log_event"
    assert hasattr(record, "request_id")
    assert record.request_id is None


@pytest.mark.asyncio
async def test_google_oauth_check_statuses() -> None:
    engine, session_factory = await _create_session_factory()
    not_configured = await _run_google_oauth_check(
        Settings(
            app_skip_external_checks=False,
            google_oauth_client_id="",
            google_oauth_client_secret=None,
        ),
        session_factory=session_factory,
    )
    assert not_configured == "not_configured"

    configured_settings = Settings(
        app_skip_external_checks=False,
        google_oauth_client_id="client-id",
        google_oauth_client_secret="client-secret",
    )
    not_connected = await _run_google_oauth_check(
        configured_settings,
        session_factory=session_factory,
    )
    assert not_connected == "not_connected"

    async with session_factory() as session:
        await upsert_google_oauth_credentials(
            session=session,
            refresh_token="refresh-token",
            access_token="access-token",
            access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="scope",
            token_type="Bearer",
        )
        await session.commit()

    ok_status = await _run_google_oauth_check(
        configured_settings,
        session_factory=session_factory,
    )
    assert ok_status == "ok"

    async def _raise_reauth(self, credentials):  # noqa: ANN001
        raise GoogleAuthRequiredError("reauthorization needed")

    original_method = GoogleCalendarService.get_valid_access_token
    GoogleCalendarService.get_valid_access_token = _raise_reauth  # type: ignore[assignment]
    try:
        reauth_status = await _run_google_oauth_check(
            configured_settings,
            session_factory=session_factory,
        )
    finally:
        GoogleCalendarService.get_valid_access_token = original_method  # type: ignore[assignment]
    assert reauth_status == "reauthorization_required"
    await engine.dispose()


@pytest.mark.asyncio
async def test_request_logs_contain_event_and_request_id() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=990001,
            invited_access_granted=True,
        )
        with patch("app.db.repositories.logger") as mock_logger:
            request = await create_consultation_request(
                session=session,
                user_id=user.id,
                full_name="Test User",
                phone="+79990000000",
                email="test@example.com",
                meeting_goal="Monitoring test",
                duration_minutes=30,
                meeting_date=date.today() + timedelta(days=1),
                start_time=time(12, 0),
                end_time=time(12, 30),
                personal_data_consent=True,
                status=RequestStatus.PENDING_APPROVAL,
            )
            await session.commit()

    matched = False
    for call in mock_logger.info.call_args_list:
        extra = call.kwargs.get("extra", {})
        if extra.get("event") == "request_created" and extra.get("request_id") == request.id:
            matched = True
            break

    assert matched, "Expected request_created log with request_id."
    await engine.dispose()
