from datetime import UTC, date, datetime, time, timedelta

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.db.base import Base
from app.db.enums import NotificationDeliveryStatus, RequestStatus, ReservationStatus
from app.db.models import (
    ConsultationRequest,
    NotificationDelivery,
    RequestStatusHistory,
    ScheduleSettings,
    SlotReservation,
)
from app.db.repositories import (
    create_consultation_request,
    create_slot_reservation,
    create_technical_error,
    get_or_create_user_by_telegram_id,
    upsert_google_oauth_credentials,
)
from app.services.background_jobs import (
    NOTIFICATION_TECHNICAL_AUTH_LOST,
    NOTIFICATION_TECHNICAL_ERROR,
    NOTIFICATION_TECHNICAL_OAUTH_EXPIRING,
    BackgroundJobsService,
)
from app.services.google_calendar import GoogleAuthRequiredError, GoogleCalendarService


class FakeBot:
    def __init__(self, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.sent_messages: list[tuple[int, str]] = []
        self._failed_once = False

    async def send_message(self, chat_id: int, text: str) -> None:
        if self.fail_first and not self._failed_once:
            self._failed_once = True
            raise TelegramNetworkError(
                method=SendMessage(chat_id=chat_id, text=text),
                message="temporary network issue",
            )
        self.sent_messages.append((chat_id, text))


async def _create_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed_settings(session) -> None:
    settings = ScheduleSettings(
        timezone="UTC",
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


def _background_settings(**overrides) -> Settings:
    base = {
        "telegram_admin_id": 9001,
        "background_jobs_enabled": True,
        "background_admin_reminder_after_hours": 12,
        "background_meeting_reminder_before_minutes": 120,
        "background_reminders_check_interval_seconds": 60,
        "background_notification_retry_delay_seconds": 1,
        "background_notification_max_attempts": 3,
        "background_technical_errors_lookback_hours": 48,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.mark.asyncio
async def test_reservation_expiration_job_marks_request_and_reservation() -> None:
    engine, session_factory = await _create_session_factory()
    bot = FakeBot()
    service = BackgroundJobsService(
        settings=_background_settings(),
        session_factory=session_factory,
        bot=bot,
    )

    async with session_factory() as session:
        await _seed_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=700001,
            invited_access_granted=True,
        )
        request = await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Тест Пользователь",
            phone="+79990000000",
            email="client@example.com",
            meeting_goal="Проверка резерва",
            duration_minutes=30,
            meeting_date=date.today() + timedelta(days=1),
            start_time=time(12, 0),
            end_time=time(12, 30),
            personal_data_consent=True,
            status=RequestStatus.PENDING_APPROVAL,
        )
        reservation = await create_slot_reservation(
            session=session,
            request_id=request.id,
            start_at=datetime.now(UTC) + timedelta(hours=2),
            end_at=datetime.now(UTC) + timedelta(hours=2, minutes=30),
            expires_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        await session.commit()
        request_id = request.id
        reservation_id = reservation.id

    await service.run_reservation_expiration_job()

    async with session_factory() as session:
        refreshed_request = (
            await session.execute(
                select(ConsultationRequest).where(ConsultationRequest.id == request_id)
            )
        ).scalars().first()
        refreshed_reservation = (
            await session.execute(
                select(SlotReservation).where(SlotReservation.id == reservation_id)
            )
        ).scalars().first()
        history = (
            await session.execute(
                select(RequestStatusHistory).where(RequestStatusHistory.request_id == request_id)
            )
        ).scalars().all()

        assert refreshed_request is not None
        assert refreshed_reservation is not None
        assert refreshed_request.status == RequestStatus.RESERVATION_EXPIRED
        assert refreshed_reservation.status == ReservationStatus.EXPIRED
        assert any(item.status == RequestStatus.RESERVATION_EXPIRED for item in history)

    await engine.dispose()


@pytest.mark.asyncio
async def test_admin_12h_reminder_and_deduplication() -> None:
    engine, session_factory = await _create_session_factory()
    bot = FakeBot()
    service = BackgroundJobsService(
        settings=_background_settings(),
        session_factory=session_factory,
        bot=bot,
    )
    now = datetime.now(UTC)

    async with session_factory() as session:
        await _seed_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=700002,
            invited_access_granted=True,
        )
        request = await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Тест 12h",
            phone="+79990000001",
            email="client12h@example.com",
            meeting_goal="Проверка напоминания",
            duration_minutes=30,
            meeting_date=date.today() + timedelta(days=1),
            start_time=time(15, 0),
            end_time=time(15, 30),
            personal_data_consent=True,
            status=RequestStatus.PENDING_APPROVAL,
        )
        request.created_at = now - timedelta(hours=13)
        request.updated_at = now - timedelta(hours=13)
        await session.commit()

    await service.run_reminders_job()
    await service.run_reminders_job()

    assert len(bot.sent_messages) == 1
    assert bot.sent_messages[0][0] == 9001
    assert "ожидает согласования" in bot.sent_messages[0][1]

    async with session_factory() as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == NotificationDeliveryStatus.SENT

    await engine.dispose()


@pytest.mark.asyncio
async def test_meeting_2h_reminders_for_user_and_admin_without_duplicates() -> None:
    engine, session_factory = await _create_session_factory()
    bot = FakeBot()
    service = BackgroundJobsService(
        settings=_background_settings(),
        session_factory=session_factory,
        bot=bot,
    )
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    meeting_start = now + timedelta(hours=2)

    async with session_factory() as session:
        await _seed_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=700003,
            invited_access_granted=True,
        )
        await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Тест 2h",
            phone="+79990000002",
            email="client2h@example.com",
            meeting_goal="Проверка 2h напоминаний",
            duration_minutes=30,
            meeting_date=meeting_start.date(),
            start_time=meeting_start.time().replace(tzinfo=None),
            end_time=(meeting_start + timedelta(minutes=30)).time().replace(tzinfo=None),
            personal_data_consent=True,
            status=RequestStatus.APPROVED,
        )
        await session.commit()

    await service.run_reminders_job()
    await service.run_reminders_job()

    recipients = [chat_id for chat_id, _ in bot.sent_messages]
    assert recipients.count(700003) == 1
    assert recipients.count(9001) == 1

    async with session_factory() as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert len(deliveries) == 2
        assert all(item.status == NotificationDeliveryStatus.SENT for item in deliveries)

    await engine.dispose()


@pytest.mark.asyncio
async def test_retry_for_temporary_notification_error() -> None:
    engine, session_factory = await _create_session_factory()
    bot = FakeBot(fail_first=True)
    service = BackgroundJobsService(
        settings=_background_settings(background_notification_retry_delay_seconds=0),
        session_factory=session_factory,
        bot=bot,
    )
    now = datetime.now(UTC)

    async with session_factory() as session:
        await _seed_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=700004,
            invited_access_granted=True,
        )
        request = await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Тест retry",
            phone="+79990000003",
            email="client-retry@example.com",
            meeting_goal="Проверка retry",
            duration_minutes=30,
            meeting_date=date.today() + timedelta(days=1),
            start_time=time(11, 0),
            end_time=time(11, 30),
            personal_data_consent=True,
            status=RequestStatus.PENDING_APPROVAL,
        )
        request.created_at = now - timedelta(hours=13)
        request.updated_at = now - timedelta(hours=13)
        await session.commit()

    await service.run_reminders_job()
    await service.run_reminders_job()

    assert len(bot.sent_messages) == 1
    assert bot.sent_messages[0][0] == 9001

    async with session_factory() as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == NotificationDeliveryStatus.SENT
        assert deliveries[0].attempts == 2

    await engine.dispose()


@pytest.mark.asyncio
async def test_technical_notifications_for_google_errors_are_deduplicated() -> None:
    engine, session_factory = await _create_session_factory()
    bot = FakeBot()
    service = BackgroundJobsService(
        settings=_background_settings(),
        session_factory=session_factory,
        bot=bot,
    )

    async with session_factory() as session:
        await _seed_settings(session)
        await create_technical_error(
            session=session,
            source="google_calendar",
            error_code="GoogleIntegrationError",
            error_message="Google API temporary unavailable",
        )
        await create_technical_error(
            session=session,
            source="google_calendar",
            error_code="GoogleAuthRequiredError",
            error_message="Google OAuth needs reauthorization",
        )
        await session.commit()

    await service.run_technical_notifications_job()
    await service.run_technical_notifications_job()

    assert len(bot.sent_messages) == 2
    assert all(chat_id == 9001 for chat_id, _ in bot.sent_messages)

    async with session_factory() as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert len(deliveries) == 2
        assert all(item.status == NotificationDeliveryStatus.SENT for item in deliveries)
        delivery_types = {item.notification_type for item in deliveries}
        assert delivery_types == {
            NOTIFICATION_TECHNICAL_ERROR,
            NOTIFICATION_TECHNICAL_AUTH_LOST,
        }

    await engine.dispose()


@pytest.mark.asyncio
async def test_google_oauth_expiry_warning_is_sent_once_per_token_expiry() -> None:
    engine, session_factory = await _create_session_factory()
    bot = FakeBot()
    service = BackgroundJobsService(
        settings=_background_settings(
            google_oauth_client_id="client-id",
            google_oauth_client_secret="client-secret",
            background_google_oauth_expiry_warning_minutes=30,
        ),
        session_factory=session_factory,
        bot=bot,
    )
    now = datetime.now(UTC).replace(microsecond=0)

    async with session_factory() as session:
        await _seed_settings(session)
        await upsert_google_oauth_credentials(
            session=session,
            refresh_token="refresh-token",
            access_token="access-token",
            access_token_expires_at=now + timedelta(minutes=10),
            scope="https://www.googleapis.com/auth/calendar",
            token_type="Bearer",
        )
        await session.commit()

    await service.run_technical_notifications_job()
    await service.run_technical_notifications_job()

    assert len(bot.sent_messages) == 1
    assert bot.sent_messages[0][0] == 9001
    assert "скоро истекает" in bot.sent_messages[0][1].lower()

    async with session_factory() as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == NotificationDeliveryStatus.SENT
        assert deliveries[0].notification_type == NOTIFICATION_TECHNICAL_OAUTH_EXPIRING

    await engine.dispose()


@pytest.mark.asyncio
async def test_google_oauth_reauth_notification_is_not_spammed_per_check_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, session_factory = await _create_session_factory()
    bot = FakeBot()
    service = BackgroundJobsService(
        settings=_background_settings(
            google_oauth_client_id="client-id",
            google_oauth_client_secret="client-secret",
            background_google_oauth_expiry_warning_minutes=0,
        ),
        session_factory=session_factory,
        bot=bot,
    )
    now = datetime.now(UTC)

    async with session_factory() as session:
        await _seed_settings(session)
        await upsert_google_oauth_credentials(
            session=session,
            refresh_token="refresh-token",
            access_token="expired-token",
            access_token_expires_at=now - timedelta(minutes=5),
            scope="https://www.googleapis.com/auth/calendar",
            token_type="Bearer",
        )
        await session.commit()

    async def _raise_reauth(self, credentials):  # noqa: ANN001
        raise GoogleAuthRequiredError("Google refresh token is invalid or expired.")

    monkeypatch.setattr(GoogleCalendarService, "get_valid_access_token", _raise_reauth)

    await service.run_technical_notifications_job()
    await service.run_technical_notifications_job()

    assert len(bot.sent_messages) == 1
    assert bot.sent_messages[0][0] == 9001
    assert "переподключите" in bot.sent_messages[0][1].lower()

    async with session_factory() as session:
        deliveries = (await session.execute(select(NotificationDelivery))).scalars().all()
        assert len(deliveries) == 1
        assert deliveries[0].status == NotificationDeliveryStatus.SENT
        assert deliveries[0].notification_type == NOTIFICATION_TECHNICAL_AUTH_LOST
        assert deliveries[0].technical_error_id is None

    await engine.dispose()
