from datetime import UTC, date, datetime, time, timedelta, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.admin_service import (
    SlotUnavailableOnApprovalError,
    approve_request_with_calendar,
    connect_google_oauth_with_code,
)
from app.bot.user_flow_service import calculate_slots_for_date, slot_rules_from_settings
from app.core.config import Settings
from app.db.base import Base
from app.db.enums import GoogleEventStatus, RequestStatus
from app.db.models import (
    GoogleCalendarEvent,
    GoogleOAuthCredential,
    ScheduleSettings,
)
from app.db.repositories import (
    create_consultation_request,
    create_slot_reservation,
    get_or_create_user_by_telegram_id,
    upsert_google_oauth_credentials,
)
from app.domain.scheduling import TimeInterval
from app.services.google_calendar import GoogleCreatedEvent, GoogleOAuthTokens


class FakeGoogleServiceForOAuth:
    async def exchange_authorization_code(self, code: str) -> GoogleOAuthTokens:
        assert code == "valid-code"
        return GoogleOAuthTokens(
            access_token="access-token",
            refresh_token="refresh-token",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="scope",
            token_type="Bearer",
        )


class FakeGoogleServiceForApproval:
    def __init__(self, busy: list[TimeInterval] | None = None) -> None:
        self._busy = busy or []

    async def get_valid_access_token(self, credentials) -> tuple[str, None]:
        assert credentials is not None
        return "access-token", None

    async def list_busy_intervals(self, **kwargs) -> list[TimeInterval]:
        return self._busy

    async def create_event(self, **kwargs) -> GoogleCreatedEvent:
        return GoogleCreatedEvent(
            google_event_id="google-event-1",
            event_url="https://calendar.google.com/event?eid=1",
            created_in_google_at=datetime.now(UTC),
        )


async def _create_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_settings(session) -> ScheduleSettings:
    settings = ScheduleSettings(
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
    session.add(settings)
    await session.flush()
    return settings


@pytest.mark.asyncio
async def test_connect_google_oauth_with_code_persists_credentials() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        text = await connect_google_oauth_with_code(
            session=session,
            admin_telegram_id=9001,
            authorization_code="valid-code",
            service=FakeGoogleServiceForOAuth(),
        )
        await session.commit()
        stored = (await session.execute(select(GoogleOAuthCredential))).scalars().all()
        assert len(stored) == 1
        assert stored[0].refresh_token == "refresh-token"
        assert "успешно" in text
    await engine.dispose()


@pytest.mark.asyncio
async def test_approve_request_with_calendar_creates_google_event() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=800001,
            invited_access_granted=True,
            username="demo_user",
        )
        target_day = date.today() + timedelta(days=3)
        request = await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Тест Пользователь",
            phone="+79990000000",
            email="client@example.com",
            meeting_goal="Нужна консультация",
            duration_minutes=30,
            meeting_date=target_day,
            start_time=time(12, 0),
            end_time=time(12, 30),
            personal_data_consent=True,
            status=RequestStatus.PENDING_APPROVAL,
        )
        await create_slot_reservation(
            session=session,
            request_id=request.id,
            start_at=datetime.combine(target_day, time(12, 0), tzinfo=UTC),
            end_at=datetime.combine(target_day, time(12, 30), tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        await upsert_google_oauth_credentials(
            session=session,
            refresh_token="refresh-token",
            access_token="access-token",
            access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="scope",
            token_type="Bearer",
        )

        result = await approve_request_with_calendar(
            session=session,
            request_id=request.id,
            admin_telegram_id=9001,
            settings=Settings(
                google_oauth_client_id="client-id",
                google_oauth_client_secret="client-secret",
            ),
            service=FakeGoogleServiceForApproval(),
        )
        await session.commit()

        assert result.request.status == RequestStatus.APPROVED
        assert result.event_url is not None
        events = (await session.execute(select(GoogleCalendarEvent))).scalars().all()
        assert len(events) == 1
        assert events[0].creation_status == GoogleEventStatus.CREATED
        assert events[0].google_event_id == "google-event-1"
    await engine.dispose()


@pytest.mark.asyncio
async def test_approve_request_with_calendar_fails_when_slot_busy_on_recheck() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=800002,
            invited_access_granted=True,
        )
        target_day = date.today() + timedelta(days=4)
        request = await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Пользователь 2",
            phone="+79990000001",
            email="client2@example.com",
            meeting_goal="Вопрос по проекту",
            duration_minutes=30,
            meeting_date=target_day,
            start_time=time(12, 0),
            end_time=time(12, 30),
            personal_data_consent=True,
            status=RequestStatus.PENDING_APPROVAL,
        )
        other_request = await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Пользователь 3",
            phone="+79990000002",
            email="client3@example.com",
            meeting_goal="Другой вопрос",
            duration_minutes=30,
            meeting_date=target_day,
            start_time=time(11, 30),
            end_time=time(12, 0),
            personal_data_consent=True,
            status=RequestStatus.PENDING_APPROVAL,
        )
        await create_slot_reservation(
            session=session,
            request_id=other_request.id,
            start_at=datetime.combine(target_day, time(11, 30), tzinfo=UTC),
            end_at=datetime.combine(target_day, time(12, 0), tzinfo=UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=24),
        )
        await upsert_google_oauth_credentials(
            session=session,
            refresh_token="refresh-token",
            access_token="access-token",
            access_token_expires_at=datetime.now(UTC) + timedelta(hours=1),
            scope="scope",
            token_type="Bearer",
        )

        with pytest.raises(SlotUnavailableOnApprovalError):
            await approve_request_with_calendar(
                session=session,
                request_id=request.id,
                admin_telegram_id=9001,
                settings=Settings(
                    google_oauth_client_id="client-id",
                    google_oauth_client_secret="client-secret",
                ),
                service=FakeGoogleServiceForApproval(),
            )
        assert request.status == RequestStatus.SLOT_UNAVAILABLE
    await engine.dispose()


@pytest.mark.asyncio
async def test_calculate_slots_includes_external_google_busy_intervals() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        settings = await _seed_settings(session)
        rules = slot_rules_from_settings(settings)
        target_day = date.today() + timedelta(days=5)
        ekb_tz = timezone(timedelta(hours=5), name="Asia/Yekaterinburg")
        busy = [
            TimeInterval(
                start_at=datetime.combine(target_day, time(12, 0), tzinfo=ekb_tz),
                end_at=datetime.combine(target_day, time(13, 0), tzinfo=ekb_tz),
            )
        ]

        slots = await calculate_slots_for_date(
            session=session,
            meeting_date=target_day,
            duration_minutes=30,
            rules=rules,
            now=datetime.now(UTC),
            external_occupied_intervals=busy,
        )
        assert slots
        assert not any(slot.start_at.hour == 12 for slot in slots)
    await engine.dispose()
