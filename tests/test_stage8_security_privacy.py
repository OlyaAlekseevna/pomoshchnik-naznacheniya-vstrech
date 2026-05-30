from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.admin_service import is_admin_telegram_id
from app.bot.handlers import _extract_start_token
from app.bot.user_flow_service import (
    SlotChoice,
    build_user_requests_text,
    can_submit_with_consent,
    create_request_from_draft,
    ensure_active_request_limit_not_exceeded,
    request_and_anonymize_user_data,
)
from app.core.config import Settings
from app.db.base import Base
from app.db.enums import RequestStatus, ReservationStatus
from app.db.models import ConsultationRequest, ScheduleSettings, SlotReservation, User
from app.db.repositories import (
    create_consultation_request,
    create_slot_reservation,
    get_or_create_user_by_telegram_id,
)
from app.domain.exceptions import BusinessRuleViolation

PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def _create_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_settings(session) -> None:
    session.add(
        ScheduleSettings(
            timezone="Asia/Yekaterinburg",
            workday_start=time(10, 0),
            workday_end=time(18, 0),
            working_days=[
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
                "sunday",
            ],
            available_durations_minutes=[15, 30, 45, 90],
            min_notice_minutes=120,
            buffer_minutes=60,
            max_consultations_per_day=3,
            booking_horizon_days=28,
            notification_templates={"new_request_admin": "new request"},
            user_without_invitation_text="Invitation required",
        )
    )
    await session.flush()


def test_admin_access_and_start_token_checks() -> None:
    settings = Settings(telegram_admin_id=9001)
    assert is_admin_telegram_id(9001, settings) is True
    assert is_admin_telegram_id(9002, settings) is False
    assert _extract_start_token("/start invite-token") == "invite-token"
    assert _extract_start_token("/start") is None


@pytest.mark.asyncio
async def test_active_request_limit_blocks_second_active_request() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=100001,
            invited_access_granted=True,
        )
        slot = SlotChoice(
            start_at=datetime.now(UTC) + timedelta(days=1),
            end_at=datetime.now(UTC) + timedelta(days=1, minutes=30),
        )
        await create_request_from_draft(
            session=session,
            user=user,
            full_name="User",
            phone="+79990000000",
            email="user@example.com",
            meeting_goal="Goal",
            duration_minutes=30,
            slot_choice=slot,
            personal_data_consent=True,
        )
        with pytest.raises(BusinessRuleViolation):
            await ensure_active_request_limit_not_exceeded(
                session=session,
                user_id=user.id,
                max_active_requests_per_user=1,
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_request_and_anonymize_user_data_cancels_active_requests() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=100002,
            invited_access_granted=True,
            first_name="Name",
            last_name="Surname",
            username="username",
        )
        target_day = date.today() + timedelta(days=2)
        request_pending = await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Name Surname",
            phone="+79990000001",
            email="privacy@example.com",
            meeting_goal="Personal goal",
            duration_minutes=30,
            meeting_date=target_day,
            start_time=time(12, 0),
            end_time=time(12, 30),
            personal_data_consent=True,
            status=RequestStatus.PENDING_APPROVAL,
        )
        await create_slot_reservation(
            session=session,
            request_id=request_pending.id,
            start_at=datetime.combine(target_day, time(12, 0), tzinfo=UTC),
            end_at=datetime.combine(target_day, time(12, 30), tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=12),
        )
        await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Name Surname",
            phone="+79990000001",
            email="privacy2@example.com",
            meeting_goal="Another goal",
            duration_minutes=30,
            meeting_date=target_day + timedelta(days=1),
            start_time=time(14, 0),
            end_time=time(14, 30),
            personal_data_consent=True,
            status=RequestStatus.APPROVED,
        )

        stats = await request_and_anonymize_user_data(
            session=session,
            user=user,
            telegram_user_id=user.telegram_user_id,
        )
        await session.commit()

        db_user = (await session.execute(select(User).where(User.id == user.id))).scalars().one()
        requests = (
            await session.execute(
                select(ConsultationRequest).where(ConsultationRequest.user_id == user.id)
            )
        ).scalars().all()
        reservations = (await session.execute(select(SlotReservation))).scalars().all()

        assert stats["anonymized_requests"] == 2
        assert stats["canceled_requests"] == 1
        assert db_user.first_name is None
        assert db_user.last_name is None
        assert db_user.username is None
        assert db_user.data_deletion_requested_at is not None
        assert all(item.full_name == "Удалено пользователем" for item in requests)
        assert all(item.phone == "Удалено" for item in requests)
        assert all(item.email.endswith("@example.invalid") for item in requests)
        assert any(item.status == RequestStatus.CANCELED_BY_USER for item in requests)
        assert reservations[0].status == ReservationStatus.RELEASED
    await engine.dispose()


def test_consent_required_and_google_details_not_exposed_in_user_history() -> None:
    assert can_submit_with_consent(True) is True
    assert can_submit_with_consent(False) is False

    class DummyRequest:
        id = 15
        meeting_date = date(2026, 6, 10)
        start_time = time(12, 0)
        end_time = time(12, 30)
        status = RequestStatus.APPROVED
        email = "hidden@example.com"
        meeting_goal = "Hidden goal"
        phone = "+79990000002"

    text = build_user_requests_text([DummyRequest()])  # type: ignore[arg-type]
    assert "hidden@example.com" not in text
    assert "Hidden goal" not in text
    assert "+79990000002" not in text


def test_safe_dump_does_not_expose_secrets() -> None:
    telegram_secret = "telegram-bot-token-secret"
    google_secret = "google-client-secret"
    settings = Settings(
        telegram_bot_token=telegram_secret,
        google_oauth_client_secret=google_secret,
    )
    payload = settings.safe_dump()
    payload_repr = str(payload)

    assert payload["telegram_token_provided"] is True
    assert payload["google_oauth_client_secret_configured"] is True
    assert telegram_secret not in payload_repr
    assert google_secret not in payload_repr


def test_project_sources_do_not_contain_hardcoded_secrets() -> None:
    include_paths = [
        PROJECT_ROOT / "app",
        PROJECT_ROOT / "tests",
        PROJECT_ROOT / "alembic",
        PROJECT_ROOT / ".env.example",
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "docker-compose.yml",
        PROJECT_ROOT / "alembic.ini",
    ]
    patterns = {
        "telegram_bot_token": re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
        "google_client_secret": re.compile(r"\bGOCSPX-[A-Za-z0-9_-]{24,}\b"),
        "google_api_key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
        "private_key": re.compile(r"-----BEGIN (?:RSA |EC )?PRIVATE KEY-----"),
    }

    findings: list[str] = []
    for path in include_paths:
        if path.is_file():
            files = [path]
        else:
            files = [
                file_path
                for file_path in path.rglob("*")
                if file_path.is_file() and "__pycache__" not in file_path.parts
            ]
        for file_path in files:
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            for secret_type, pattern in patterns.items():
                if pattern.search(content):
                    findings.append(f"{secret_type}: {file_path}")

    assert findings == []
