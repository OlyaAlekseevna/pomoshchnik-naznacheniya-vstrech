from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.user_flow_service import (
    SlotChoice,
    build_user_requests_text,
    calculate_slots_for_date,
    can_submit_with_consent,
    cancel_request_for_user,
    create_request_from_draft,
    ensure_slot_still_available,
    get_user_requests,
    is_valid_email,
    slot_rules_from_settings,
    update_request_goal_for_user,
)
from app.db.base import Base
from app.db.enums import RequestStatus, ReservationStatus
from app.db.models import (
    ConsultationRequest,
    RequestStatusHistory,
    ScheduleSettings,
    SlotReservation,
)
from app.db.repositories import get_or_create_user_by_telegram_id, get_schedule_settings
from app.domain.exceptions import BusinessRuleViolation


async def _create_session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def _seed_schedule_settings(session) -> None:
    settings = ScheduleSettings(
        timezone="Asia/Yekaterinburg",
        workday_start=time(10, 0),
        workday_end=time(18, 0),
        working_days=["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"],
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


@pytest.mark.asyncio
async def test_create_request_persists_request_history_and_reservation() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_schedule_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=900001,
            invited_access_granted=True,
            first_name="Test",
        )
        slot = SlotChoice(
            start_at=datetime.now(UTC) + timedelta(days=1),
            end_at=datetime.now(UTC) + timedelta(days=1, minutes=30),
        )

        request = await create_request_from_draft(
            session=session,
            user=user,
            full_name="Test User",
            phone="+79000000000",
            email="user@example.com",
            meeting_goal="Need consultation",
            duration_minutes=30,
            slot_choice=slot,
            personal_data_consent=True,
        )
        await session.commit()

        persisted_request = (
            await session.execute(
                select(ConsultationRequest).where(ConsultationRequest.id == request.id)
            )
        ).scalars().first()
        reservations = (await session.execute(select(SlotReservation))).scalars().all()
        history = (await session.execute(select(RequestStatusHistory))).scalars().all()

        assert persisted_request is not None
        assert persisted_request.status == RequestStatus.PENDING_APPROVAL
        assert persisted_request.personal_data_consent is True
        assert len(reservations) == 1
        assert reservations[0].status == ReservationStatus.ACTIVE
        assert len(history) == 1
        assert history[0].status == RequestStatus.PENDING_APPROVAL

    await engine.dispose()


@pytest.mark.asyncio
async def test_update_and_cancel_request_before_approval() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_schedule_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=900002,
            invited_access_granted=True,
            first_name="Test",
        )
        slot = SlotChoice(
            start_at=datetime.now(UTC) + timedelta(days=2),
            end_at=datetime.now(UTC) + timedelta(days=2, minutes=45),
        )
        request = await create_request_from_draft(
            session=session,
            user=user,
            full_name="User Two",
            phone="+79000000001",
            email="two@example.com",
            meeting_goal="Old goal",
            duration_minutes=45,
            slot_choice=slot,
            personal_data_consent=True,
        )
        await session.flush()

        updated = await update_request_goal_for_user(
            session=session,
            request_id=request.id,
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
            new_goal="New goal",
        )
        assert updated.status == RequestStatus.UPDATED_BY_USER
        assert updated.meeting_goal == "New goal"

        canceled = await cancel_request_for_user(
            session=session,
            request_id=request.id,
            user_id=user.id,
            telegram_user_id=user.telegram_user_id,
        )
        await session.commit()

        assert canceled.status == RequestStatus.CANCELED_BY_USER
        reservations = (await session.execute(select(SlotReservation))).scalars().all()
        assert len(reservations) == 1
        assert reservations[0].status == ReservationStatus.RELEASED

    await engine.dispose()


@pytest.mark.asyncio
async def test_slot_calculation_and_slot_availability_checks() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_schedule_settings(session)
        settings = await get_schedule_settings(session)
        rules = slot_rules_from_settings(settings)
        target_day = date.today() + timedelta(days=3)

        available_slots = await calculate_slots_for_date(
            session=session,
            meeting_date=target_day,
            duration_minutes=30,
            rules=rules,
            now=datetime.now(UTC),
        )
        assert available_slots
        selected = available_slots[0]
        ensure_slot_still_available(selected, available_slots)

        with pytest.raises(BusinessRuleViolation):
            ensure_slot_still_available(
                SlotChoice(
                    start_at=selected.start_at + timedelta(days=1),
                    end_at=selected.end_at + timedelta(days=1),
                ),
                available_slots,
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_user_history_text_and_helpers() -> None:
    engine, session_factory = await _create_session_factory()
    async with session_factory() as session:
        await _seed_schedule_settings(session)
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=900003,
            invited_access_granted=True,
        )
        slot = SlotChoice(
            start_at=datetime.now(UTC) + timedelta(days=1),
            end_at=datetime.now(UTC) + timedelta(days=1, minutes=15),
        )
        await create_request_from_draft(
            session=session,
            user=user,
            full_name="User Three",
            phone="+79000000002",
            email="three@example.com",
            meeting_goal="Goal",
            duration_minutes=15,
            slot_choice=slot,
            personal_data_consent=True,
        )
        await session.commit()
        requests = await get_user_requests(session, user.id)
        text = build_user_requests_text(requests)

        assert requests
        assert text.startswith("Your requests:")
        assert "#" in text

    await engine.dispose()


def test_validation_helpers() -> None:
    assert is_valid_email("valid@example.com") is True
    assert is_valid_email("invalid-email") is False
    assert can_submit_with_consent(True) is True
    assert can_submit_with_consent(False) is False
