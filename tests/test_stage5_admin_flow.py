from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.admin_service import (
    ALTERNATIVE_REJECTION_REASON,
    apply_setting_update,
    approve_request,
    get_request_history,
    is_admin_telegram_id,
    manual_create_meeting_for_user,
    reject_request,
    reject_with_alternative_slot,
    toggle_user_block,
)
from app.core.config import Settings
from app.db.base import Base
from app.db.enums import RequestChangedByRole, RequestStatus
from app.db.models import (
    ConsultationRequest,
    ForbiddenDate,
    ForbiddenPeriod,
    RequestStatusHistory,
    ScheduleSettings,
    User,
)
from app.db.repositories import (
    create_consultation_request,
    get_or_create_user_by_telegram_id,
    get_schedule_settings,
)


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


async def _seed_request(
    session,
    telegram_user_id: int,
    status: RequestStatus = RequestStatus.PENDING_APPROVAL,
) -> ConsultationRequest:
    user = await get_or_create_user_by_telegram_id(
        session=session,
        telegram_user_id=telegram_user_id,
        invited_access_granted=True,
    )
    target_date = date.today() + timedelta(days=2)
    request = await create_consultation_request(
        session=session,
        user_id=user.id,
        full_name="Admin Flow User",
        phone="+79000000000",
        email="admin.flow@example.com",
        meeting_goal="Need help",
        duration_minutes=30,
        meeting_date=target_date,
        start_time=time(12, 0),
        end_time=time(12, 30),
        personal_data_consent=True,
        status=status,
    )
    await session.flush()
    return request


def test_admin_id_check() -> None:
    settings = Settings(telegram_admin_id=5001)
    assert is_admin_telegram_id(5001, settings) is True
    assert is_admin_telegram_id(6001, settings) is False
    assert is_admin_telegram_id(None, settings) is False


@pytest.mark.asyncio
async def test_approve_and_reject_requests() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        to_approve = await _seed_request(session, telegram_user_id=7001)
        to_reject = await _seed_request(session, telegram_user_id=7002)

        approved = await approve_request(session, to_approve.id, admin_telegram_id=9001)
        rejected = await reject_request(
            session,
            to_reject.id,
            admin_telegram_id=9001,
            reason="Rejected by admin",
        )
        await session.commit()

        assert approved.status == RequestStatus.APPROVED
        assert rejected.status == RequestStatus.REJECTED
        history = (
            await session.execute(
                select(RequestStatusHistory).where(
                    RequestStatusHistory.request_id.in_([to_approve.id, to_reject.id])
                )
            )
        ).scalars().all()
        assert any(
            item.status == RequestStatus.APPROVED
            and item.changed_by_role == RequestChangedByRole.ADMIN
            for item in history
        )
        assert any(
            item.status == RequestStatus.REJECTED
            and item.changed_by_role == RequestChangedByRole.ADMIN
            for item in history
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_alternative_slot_and_history() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        request = await _seed_request(session, telegram_user_id=7101)
        alternative_date = date.today() + timedelta(days=5)
        updated = await reject_with_alternative_slot(
            session=session,
            request_id=request.id,
            admin_telegram_id=9001,
            alternative_date=alternative_date,
            alternative_start_time=time(14, 0),
            alternative_end_time=time(14, 30),
        )
        await session.commit()

        assert updated.status == RequestStatus.REJECTED
        assert updated.rejection_reason == ALTERNATIVE_REJECTION_REASON
        assert updated.alternative_date == alternative_date
        history = await get_request_history(session, request.id)
        assert history
        assert "alternative_slot_offered" in (history[-1].comment or "")

    await engine.dispose()


@pytest.mark.asyncio
async def test_block_unblock_and_manual_creation() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        request = await _seed_request(session, telegram_user_id=7201)
        blocked_user = await toggle_user_block(
            session=session,
            request_id=request.id,
            admin_telegram_id=9001,
            blocked=True,
        )
        assert blocked_user.is_blocked is True

        unblocked_user = await toggle_user_block(
            session=session,
            request_id=request.id,
            admin_telegram_id=9001,
            blocked=False,
        )
        assert unblocked_user.is_blocked is False

        manually_created = await manual_create_meeting_for_user(
            session=session,
            request_id=request.id,
            admin_telegram_id=9001,
        )
        await session.commit()
        assert manually_created.status == RequestStatus.APPROVED

    await engine.dispose()


@pytest.mark.asyncio
async def test_settings_and_forbidden_period_updates() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        admin_id = 9001

        with pytest.raises(ValueError) as error_info:
            await apply_setting_update(session, admin_id, "working_days", "funday")
        assert "monday" not in str(error_info.value)
        assert "понедельник,вторник,среда" in str(error_info.value)

        summary_with_ru_weekdays = await apply_setting_update(
            session,
            admin_id,
            "working_days",
            "понедельник, вторник, сред, четверг, пятница",
        )
        await apply_setting_update(session, admin_id, "buffer", "45")
        await apply_setting_update(session, admin_id, "horizon", "35")
        await apply_setting_update(session, admin_id, "working_hours", "09:30-17:15")
        await apply_setting_update(session, admin_id, "durations", "20,40,60")
        await apply_setting_update(session, admin_id, "forbidden_date", "2026-06-01|holiday")
        await apply_setting_update(
            session,
            admin_id,
            "forbidden_period",
            "2026-06-02 10:00 - 2026-06-02 12:00|maintenance",
        )
        await apply_setting_update(
            session,
            admin_id,
            "new_request_text",
            "New request template text",
        )
        await session.commit()

        settings = await get_schedule_settings(session)
        assert settings.working_days == [
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
        ]
        assert "понедельник" in summary_with_ru_weekdays
        assert settings.buffer_minutes == 45
        assert settings.booking_horizon_days == 35
        assert settings.workday_start == time(9, 30)
        assert settings.workday_end == time(17, 15)
        assert settings.available_durations_minutes == [20, 40, 60]
        assert settings.notification_templates["new_request_admin"] == "New request template text"

        forbidden_dates = (await session.execute(select(ForbiddenDate))).scalars().all()
        forbidden_periods = (await session.execute(select(ForbiddenPeriod))).scalars().all()
        assert len(forbidden_dates) == 1
        assert len(forbidden_periods) == 1
        assert forbidden_periods[0].start_at.replace(tzinfo=UTC) == datetime(
            2026,
            6,
            2,
            10,
            0,
            tzinfo=UTC,
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_block_toggle_updates_user_entity() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_settings(session)
        request = await _seed_request(session, telegram_user_id=7301)
        await toggle_user_block(
            session=session,
            request_id=request.id,
            admin_telegram_id=9001,
            blocked=True,
        )
        await session.commit()
        user = (
            await session.execute(select(User).where(User.telegram_user_id == 7301))
        ).scalars().one()
        assert user.is_blocked is True

    await engine.dispose()
