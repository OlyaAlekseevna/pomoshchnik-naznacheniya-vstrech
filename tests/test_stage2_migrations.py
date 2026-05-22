from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.db.enums import RequestChangedByRole, RequestStatus, ReservationStatus
from app.db.models import RequestStatusHistory, ScheduleSettings, SlotReservation
from app.db.repositories import (
    append_request_status_history,
    create_consultation_request,
    create_slot_reservation,
    create_user,
    release_slot_reservation,
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _alembic_config(db_url: str) -> Config:
    root = _repo_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", db_url)
    return config


def _upgrade_to_head(db_url: str) -> None:
    config = _alembic_config(db_url)
    command.upgrade(config, "head")


async def test_stage2_migrations_create_schema_seed_defaults_and_support_writes(tmp_path) -> None:
    db_file = tmp_path / "stage2_migrations.db"
    sync_db_url = f"sqlite:///{db_file}"
    async_db_url = f"sqlite+aiosqlite:///{db_file}"

    _upgrade_to_head(sync_db_url)

    inspector = inspect(create_engine(sync_db_url))
    expected_tables = {
        "users",
        "consultation_requests",
        "schedule_settings",
        "forbidden_dates",
        "forbidden_periods",
        "slot_reservations",
        "google_calendar_events",
        "request_status_history",
        "admin_audit_logs",
        "technical_errors",
    }
    assert expected_tables.issubset(set(inspector.get_table_names()))

    engine = create_async_engine(async_db_url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        settings = (await session.execute(select(ScheduleSettings))).scalars().all()
        assert len(settings) == 1
        assert settings[0].timezone == "Asia/Yekaterinburg"
        assert settings[0].workday_start == time(hour=10, minute=0)
        assert settings[0].workday_end == time(hour=18, minute=0)
        assert settings[0].available_durations_minutes == [15, 30, 45, 90]
        assert settings[0].buffer_minutes == 60
        assert settings[0].max_consultations_per_day == 3
        assert settings[0].booking_horizon_days == 28

        user = await create_user(
            session=session,
            telegram_user_id=7000000001,
            invited_access_granted=True,
            first_name="Тест",
            last_name="Пользователь",
            username="test_user",
        )
        request = await create_consultation_request(
            session=session,
            user_id=user.id,
            full_name="Тест Пользователь",
            phone="+79000000000",
            email="test@example.com",
            meeting_goal="Smoke check migration write path",
            duration_minutes=30,
            meeting_date=date.today(),
            start_time=time(hour=11, minute=0),
            end_time=time(hour=11, minute=30),
            personal_data_consent=True,
            status=RequestStatus.PENDING_APPROVAL,
        )
        await append_request_status_history(
            session=session,
            request_id=request.id,
            status=RequestStatus.PENDING_APPROVAL,
            changed_by_role=RequestChangedByRole.USER,
            changed_by_telegram_id=user.telegram_user_id,
        )
        reservation = await create_slot_reservation(
            session=session,
            request_id=request.id,
            start_at=datetime.now(UTC) + timedelta(days=1),
            end_at=datetime.now(UTC) + timedelta(days=1, minutes=30),
            expires_at=datetime.now(UTC) + timedelta(days=2),
        )
        await release_slot_reservation(
            session=session,
            reservation=reservation,
            released_status=ReservationStatus.RELEASED,
        )
        await session.commit()

        history_items = (await session.execute(select(RequestStatusHistory))).scalars().all()
        assert len(history_items) == 1
        reservations = (await session.execute(select(SlotReservation))).scalars().all()
        assert len(reservations) == 1
        assert reservations[0].status == ReservationStatus.RELEASED
        assert reservations[0].released_at is not None

    await engine.dispose()

    # Re-applying migrations should be idempotent.
    _upgrade_to_head(sync_db_url)
