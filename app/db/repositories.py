import logging
from datetime import UTC, date, datetime, time

from sqlalchemy import and_, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import RequestChangedByRole, RequestStatus, ReservationStatus
from app.db.models import (
    ConsultationRequest,
    RequestStatusHistory,
    ScheduleSettings,
    SlotReservation,
    TechnicalError,
    User,
)

logger = logging.getLogger(__name__)


async def create_user(
    session: AsyncSession,
    telegram_user_id: int,
    invited_access_granted: bool,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
) -> User:
    user = User(
        telegram_user_id=telegram_user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        invited_access_granted=invited_access_granted,
        first_seen_at=datetime.now(UTC),
    )
    try:
        session.add(user)
        await session.flush()
    except SQLAlchemyError:
        logger.exception(
            "Failed to create user.",
            extra={"event": "db_write_error", "entity": "user"},
        )
        raise

    logger.info(
        "User created.",
        extra={"event": "user_created", "telegram_user_id": telegram_user_id, "user_id": user.id},
    )
    return user


async def create_consultation_request(
    session: AsyncSession,
    user_id: int,
    full_name: str,
    phone: str,
    email: str,
    meeting_goal: str,
    duration_minutes: int,
    meeting_date: date,
    start_time: time,
    end_time: time,
    personal_data_consent: bool,
    status: RequestStatus = RequestStatus.PENDING_APPROVAL,
) -> ConsultationRequest:
    request = ConsultationRequest(
        user_id=user_id,
        full_name=full_name,
        phone=phone,
        email=email,
        meeting_goal=meeting_goal,
        duration_minutes=duration_minutes,
        meeting_date=meeting_date,
        start_time=start_time,
        end_time=end_time,
        status=status,
        personal_data_consent=personal_data_consent,
    )
    try:
        session.add(request)
        await session.flush()
    except SQLAlchemyError:
        logger.exception(
            "Failed to create consultation request.",
            extra={"event": "db_write_error", "entity": "consultation_request"},
        )
        raise

    logger.info(
        "Consultation request created.",
        extra={
            "event": "request_created",
            "request_id": request.id,
            "user_id": user_id,
            "status": request.status.value,
        },
    )
    return request


async def append_request_status_history(
    session: AsyncSession,
    request_id: int,
    status: RequestStatus,
    changed_by_role: RequestChangedByRole,
    changed_by_telegram_id: int | None = None,
    comment: str | None = None,
) -> RequestStatusHistory:
    history_item = RequestStatusHistory(
        request_id=request_id,
        status=status,
        changed_by_role=changed_by_role,
        changed_by_telegram_id=changed_by_telegram_id,
        comment=comment,
        created_at=datetime.now(UTC),
    )
    try:
        session.add(history_item)
        await session.flush()
    except SQLAlchemyError:
        logger.exception(
            "Failed to append request status history.",
            extra={"event": "db_write_error", "entity": "request_status_history"},
        )
        raise

    logger.info(
        "Request status updated.",
        extra={"event": "request_status_changed", "request_id": request_id, "status": status.value},
    )
    return history_item


async def create_slot_reservation(
    session: AsyncSession,
    request_id: int,
    start_at: datetime,
    end_at: datetime,
    expires_at: datetime,
) -> SlotReservation:
    reservation = SlotReservation(
        request_id=request_id,
        start_at=start_at,
        end_at=end_at,
        expires_at=expires_at,
        status=ReservationStatus.ACTIVE,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    try:
        session.add(reservation)
        await session.flush()
    except SQLAlchemyError:
        logger.exception(
            "Failed to create slot reservation.",
            extra={"event": "db_write_error", "entity": "slot_reservation"},
        )
        raise

    logger.info(
        "Slot reserved.",
        extra={
            "event": "slot_reserved",
            "request_id": request_id,
            "reservation_id": reservation.id,
        },
    )
    return reservation


async def release_slot_reservation(
    session: AsyncSession,
    reservation: SlotReservation,
    released_status: ReservationStatus,
) -> SlotReservation:
    reservation.status = released_status
    reservation.released_at = datetime.now(UTC)
    reservation.updated_at = datetime.now(UTC)
    try:
        await session.flush()
    except SQLAlchemyError:
        logger.exception(
            "Failed to release slot reservation.",
            extra={"event": "db_write_error", "entity": "slot_reservation"},
        )
        raise

    logger.info(
        "Slot reservation released.",
        extra={
            "event": "slot_reservation_released",
            "reservation_id": reservation.id,
            "request_id": reservation.request_id,
            "status": reservation.status.value,
        },
    )
    return reservation


async def create_technical_error(
    session: AsyncSession,
    source: str,
    error_message: str,
    request_id: int | None = None,
    user_id: int | None = None,
    error_code: str | None = None,
    details: dict[str, str] | None = None,
) -> TechnicalError:
    technical_error = TechnicalError(
        source=source,
        request_id=request_id,
        user_id=user_id,
        error_code=error_code,
        error_message=error_message,
        details=details,
        created_at=datetime.now(UTC),
    )
    try:
        session.add(technical_error)
        await session.flush()
    except SQLAlchemyError:
        logger.exception(
            "Failed to persist technical error.",
            extra={"event": "db_write_error", "entity": "technical_error"},
        )
        raise
    return technical_error


async def get_user_by_telegram_id(
    session: AsyncSession,
    telegram_user_id: int,
) -> User | None:
    query = select(User).where(User.telegram_user_id == telegram_user_id)
    return (await session.execute(query)).scalars().first()


async def get_or_create_user_by_telegram_id(
    session: AsyncSession,
    telegram_user_id: int,
    invited_access_granted: bool,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
) -> User:
    existing_user = await get_user_by_telegram_id(session, telegram_user_id)
    if existing_user is not None:
        if invited_access_granted and not existing_user.invited_access_granted:
            existing_user.invited_access_granted = True
            await session.flush()
        return existing_user
    return await create_user(
        session=session,
        telegram_user_id=telegram_user_id,
        invited_access_granted=invited_access_granted,
        first_name=first_name,
        last_name=last_name,
        username=username,
    )


async def get_schedule_settings(session: AsyncSession) -> ScheduleSettings:
    settings = (await session.execute(select(ScheduleSettings))).scalars().first()
    if settings is None:
        raise RuntimeError("Schedule settings are not initialized.")
    return settings


async def list_active_reservations_by_date(
    session: AsyncSession,
    meeting_date: date,
) -> list[SlotReservation]:
    start_of_day = datetime.combine(meeting_date, time.min, tzinfo=UTC)
    end_of_day = datetime.combine(meeting_date, time.max, tzinfo=UTC)
    query = (
        select(SlotReservation)
        .where(
            and_(
                SlotReservation.status == ReservationStatus.ACTIVE,
                SlotReservation.start_at >= start_of_day,
                SlotReservation.start_at <= end_of_day,
            )
        )
        .order_by(SlotReservation.start_at.asc())
    )
    return (await session.execute(query)).scalars().all()


async def count_consultations_for_date(
    session: AsyncSession,
    meeting_date: date,
) -> int:
    query = select(func.count(ConsultationRequest.id)).where(
        and_(
            ConsultationRequest.meeting_date == meeting_date,
            ConsultationRequest.status.in_(
                [
                    RequestStatus.PENDING_APPROVAL,
                    RequestStatus.UPDATED_BY_USER,
                    RequestStatus.APPROVED,
                ]
            ),
        )
    )
    return int((await session.execute(query)).scalar_one())


async def list_requests_by_user_id(
    session: AsyncSession,
    user_id: int,
) -> list[ConsultationRequest]:
    query = (
        select(ConsultationRequest)
        .where(ConsultationRequest.user_id == user_id)
        .order_by(ConsultationRequest.created_at.desc())
    )
    return (await session.execute(query)).scalars().all()


async def get_request_by_id_and_user_id(
    session: AsyncSession,
    request_id: int,
    user_id: int,
) -> ConsultationRequest | None:
    query = select(ConsultationRequest).where(
        and_(
            ConsultationRequest.id == request_id,
            ConsultationRequest.user_id == user_id,
        )
    )
    return (await session.execute(query)).scalars().first()


async def get_active_reservation_by_request_id(
    session: AsyncSession,
    request_id: int,
) -> SlotReservation | None:
    query = select(SlotReservation).where(
        and_(
            SlotReservation.request_id == request_id,
            SlotReservation.status == ReservationStatus.ACTIVE,
        )
    )
    return (await session.execute(query)).scalars().first()
