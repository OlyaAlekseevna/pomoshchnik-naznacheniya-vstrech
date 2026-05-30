import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.enums import (
    GoogleEventStatus,
    NotificationDeliveryStatus,
    RequestChangedByRole,
    RequestStatus,
    ReservationStatus,
)
from app.db.models import (
    AdminAuditLog,
    ConsultationRequest,
    ForbiddenDate,
    ForbiddenPeriod,
    GoogleCalendarEvent,
    GoogleOAuthCredential,
    NotificationDelivery,
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
    exclude_request_id: int | None = None,
) -> list[SlotReservation]:
    start_of_day = datetime.combine(meeting_date, time.min, tzinfo=UTC)
    end_of_day = datetime.combine(meeting_date, time.max, tzinfo=UTC)
    conditions = [
        SlotReservation.status == ReservationStatus.ACTIVE,
        SlotReservation.start_at >= start_of_day,
        SlotReservation.start_at <= end_of_day,
    ]
    if exclude_request_id is not None:
        conditions.append(SlotReservation.request_id != exclude_request_id)
    query = (
        select(SlotReservation)
        .where(and_(*conditions))
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


async def count_active_requests_by_user_id(
    session: AsyncSession,
    user_id: int,
) -> int:
    query = select(func.count(ConsultationRequest.id)).where(
        and_(
            ConsultationRequest.user_id == user_id,
            ConsultationRequest.status.in_(
                [
                    RequestStatus.PENDING_APPROVAL,
                    RequestStatus.UPDATED_BY_USER,
                ]
            ),
        )
    )
    return int((await session.execute(query)).scalar_one())


async def list_requests_for_admin(
    session: AsyncSession,
    limit: int = 20,
) -> list[ConsultationRequest]:
    query = select(ConsultationRequest).order_by(ConsultationRequest.created_at.desc()).limit(limit)
    return (await session.execute(query)).scalars().all()


async def get_request_by_id(
    session: AsyncSession,
    request_id: int,
) -> ConsultationRequest | None:
    query = select(ConsultationRequest).where(ConsultationRequest.id == request_id)
    return (await session.execute(query)).scalars().first()


async def list_request_status_history(
    session: AsyncSession,
    request_id: int,
) -> list[RequestStatusHistory]:
    query = (
        select(RequestStatusHistory)
        .where(RequestStatusHistory.request_id == request_id)
        .order_by(RequestStatusHistory.created_at.asc())
    )
    return (await session.execute(query)).scalars().all()


async def get_user_by_id(
    session: AsyncSession,
    user_id: int,
) -> User | None:
    query = select(User).where(User.id == user_id)
    return (await session.execute(query)).scalars().first()


async def anonymize_user_personal_data(
    session: AsyncSession,
    user_id: int,
) -> tuple[User, int]:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise LookupError("User not found.")

    user.username = None
    user.first_name = None
    user.last_name = None
    user.data_deletion_requested_at = datetime.now(UTC)

    requests = (
        await session.execute(
            select(ConsultationRequest).where(ConsultationRequest.user_id == user_id)
        )
    ).scalars().all()
    for item in requests:
        item.full_name = "Удалено пользователем"
        item.phone = "Удалено"
        item.email = f"deleted-user-{user.id}-{item.id}@example.invalid"
        item.meeting_goal = "Удалено по запросу на удаление данных."

    await session.flush()
    return user, len(requests)


async def set_user_blocked(
    session: AsyncSession,
    user_id: int,
    blocked: bool,
) -> User:
    user = await get_user_by_id(session, user_id)
    if user is None:
        raise LookupError("User not found.")
    user.is_blocked = blocked
    await session.flush()
    return user


async def update_schedule_settings(
    session: AsyncSession,
    **fields: object,
) -> ScheduleSettings:
    settings = await get_schedule_settings(session)
    for key, value in fields.items():
        if not hasattr(settings, key):
            raise ValueError(f"Unknown settings field: {key}")
        setattr(settings, key, value)
    await session.flush()
    return settings


async def add_forbidden_date(
    session: AsyncSession,
    day: date,
    reason: str | None = None,
) -> ForbiddenDate:
    entity = ForbiddenDate(day=day, reason=reason, created_at=datetime.now(UTC))
    session.add(entity)
    await session.flush()
    return entity


async def add_forbidden_period(
    session: AsyncSession,
    start_at: datetime,
    end_at: datetime,
    reason: str | None = None,
) -> ForbiddenPeriod:
    entity = ForbiddenPeriod(
        start_at=start_at,
        end_at=end_at,
        reason=reason,
        created_at=datetime.now(UTC),
    )
    session.add(entity)
    await session.flush()
    return entity


async def create_admin_audit_log(
    session: AsyncSession,
    admin_telegram_id: int,
    action_type: str,
    request_id: int | None = None,
    target_user_id: int | None = None,
    payload: dict[str, str] | None = None,
) -> AdminAuditLog:
    entry = AdminAuditLog(
        admin_telegram_id=admin_telegram_id,
        action_type=action_type,
        request_id=request_id,
        target_user_id=target_user_id,
        payload=payload,
        created_at=datetime.now(UTC),
    )
    session.add(entry)
    await session.flush()
    return entry


async def get_google_oauth_credentials(session: AsyncSession) -> GoogleOAuthCredential | None:
    query = select(GoogleOAuthCredential).order_by(GoogleOAuthCredential.id.asc()).limit(1)
    return (await session.execute(query)).scalars().first()


async def upsert_google_oauth_credentials(
    session: AsyncSession,
    refresh_token: str,
    access_token: str | None,
    access_token_expires_at: datetime | None,
    scope: str | None,
    token_type: str | None,
) -> GoogleOAuthCredential:
    credentials = await get_google_oauth_credentials(session)
    if credentials is None:
        credentials = GoogleOAuthCredential(
            refresh_token=refresh_token,
            access_token=access_token,
            access_token_expires_at=access_token_expires_at,
            scope=scope,
            token_type=token_type,
        )
        session.add(credentials)
    else:
        credentials.refresh_token = refresh_token
        credentials.access_token = access_token
        credentials.access_token_expires_at = access_token_expires_at
        credentials.scope = scope
        credentials.token_type = token_type
    await session.flush()
    return credentials


async def create_google_calendar_event(
    session: AsyncSession,
    request_id: int,
    creation_status: GoogleEventStatus,
    google_event_id: str | None = None,
    event_url: str | None = None,
    created_in_google_at: datetime | None = None,
    error_text: str | None = None,
) -> GoogleCalendarEvent:
    record = GoogleCalendarEvent(
        request_id=request_id,
        google_event_id=google_event_id,
        event_url=event_url,
        created_in_google_at=created_in_google_at,
        creation_status=creation_status,
        error_text=error_text,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(record)
    await session.flush()
    return record


async def update_google_calendar_event(
    session: AsyncSession,
    event_record: GoogleCalendarEvent,
    creation_status: GoogleEventStatus,
    google_event_id: str | None = None,
    event_url: str | None = None,
    created_in_google_at: datetime | None = None,
    error_text: str | None = None,
) -> GoogleCalendarEvent:
    event_record.creation_status = creation_status
    event_record.google_event_id = google_event_id
    event_record.event_url = event_url
    event_record.created_in_google_at = created_in_google_at
    event_record.error_text = error_text
    event_record.updated_at = datetime.now(UTC)
    await session.flush()
    return event_record


async def get_latest_google_calendar_event_by_request_id(
    session: AsyncSession,
    request_id: int,
) -> GoogleCalendarEvent | None:
    query = (
        select(GoogleCalendarEvent)
        .where(GoogleCalendarEvent.request_id == request_id)
        .order_by(GoogleCalendarEvent.created_at.desc())
        .limit(1)
    )
    return (await session.execute(query)).scalars().first()


async def list_expired_active_reservations(
    session: AsyncSession,
    now: datetime,
) -> list[tuple[SlotReservation, ConsultationRequest]]:
    query = (
        select(SlotReservation, ConsultationRequest)
        .join(ConsultationRequest, ConsultationRequest.id == SlotReservation.request_id)
        .where(
            and_(
                SlotReservation.status == ReservationStatus.ACTIVE,
                SlotReservation.expires_at <= now,
                ConsultationRequest.status.in_(
                    [
                        RequestStatus.DRAFT,
                        RequestStatus.PENDING_APPROVAL,
                        RequestStatus.UPDATED_BY_USER,
                    ]
                ),
            )
        )
        .order_by(SlotReservation.expires_at.asc())
    )
    rows = (await session.execute(query)).all()
    return [(row[0], row[1]) for row in rows]


async def list_requests_for_admin_12h_reminder(
    session: AsyncSession,
    created_before: datetime,
) -> list[tuple[ConsultationRequest, User]]:
    query = (
        select(ConsultationRequest, User)
        .join(User, User.id == ConsultationRequest.user_id)
        .where(
            and_(
                ConsultationRequest.status.in_(
                    [
                        RequestStatus.PENDING_APPROVAL,
                        RequestStatus.UPDATED_BY_USER,
                    ]
                ),
                ConsultationRequest.created_at <= created_before,
            )
        )
        .order_by(ConsultationRequest.created_at.asc())
    )
    rows = (await session.execute(query)).all()
    return [(row[0], row[1]) for row in rows]


async def list_approved_requests_with_users_in_date_range(
    session: AsyncSession,
    date_from: date,
    date_to: date,
) -> list[tuple[ConsultationRequest, User]]:
    query = (
        select(ConsultationRequest, User)
        .join(User, User.id == ConsultationRequest.user_id)
        .where(
            and_(
                ConsultationRequest.status == RequestStatus.APPROVED,
                ConsultationRequest.meeting_date >= date_from,
                ConsultationRequest.meeting_date <= date_to,
            )
        )
        .order_by(ConsultationRequest.meeting_date.asc(), ConsultationRequest.start_time.asc())
    )
    rows = (await session.execute(query)).all()
    return [(row[0], row[1]) for row in rows]


async def list_google_technical_errors_since(
    session: AsyncSession,
    created_after: datetime,
    limit: int = 100,
) -> list[TechnicalError]:
    query = (
        select(TechnicalError)
        .where(
            and_(
                TechnicalError.source == "google_calendar",
                TechnicalError.created_at >= created_after,
            )
        )
        .order_by(TechnicalError.created_at.asc())
        .limit(limit)
    )
    return (await session.execute(query)).scalars().all()


async def get_notification_delivery_by_key(
    session: AsyncSession,
    dedupe_key: str,
) -> NotificationDelivery | None:
    query = select(NotificationDelivery).where(NotificationDelivery.dedupe_key == dedupe_key)
    return (await session.execute(query)).scalars().first()


async def get_or_create_notification_delivery(
    session: AsyncSession,
    dedupe_key: str,
    notification_type: str,
    target_telegram_user_id: int | None,
    request_id: int | None = None,
    technical_error_id: int | None = None,
    scheduled_for: datetime | None = None,
) -> NotificationDelivery:
    delivery = await get_notification_delivery_by_key(session, dedupe_key=dedupe_key)
    if delivery is not None:
        return delivery
    delivery = NotificationDelivery(
        dedupe_key=dedupe_key,
        notification_type=notification_type,
        request_id=request_id,
        technical_error_id=technical_error_id,
        target_telegram_user_id=target_telegram_user_id,
        status=NotificationDeliveryStatus.PENDING,
        attempts=0,
        sent_at=None,
        next_retry_at=None,
        scheduled_for=scheduled_for,
        last_error=None,
    )
    session.add(delivery)
    await session.flush()
    return delivery


async def mark_notification_delivery_sent(
    session: AsyncSession,
    delivery: NotificationDelivery,
    sent_at: datetime,
) -> NotificationDelivery:
    delivery.status = NotificationDeliveryStatus.SENT
    delivery.attempts += 1
    delivery.sent_at = sent_at
    delivery.next_retry_at = None
    delivery.last_error = None
    await session.flush()
    return delivery


async def mark_notification_delivery_retry(
    session: AsyncSession,
    delivery: NotificationDelivery,
    now: datetime,
    error_text: str,
    retry_delay_seconds: int,
    max_attempts: int,
    retryable: bool,
) -> NotificationDelivery:
    delivery.attempts += 1
    delivery.last_error = error_text
    if retryable and delivery.attempts < max_attempts:
        delivery.status = NotificationDeliveryStatus.PENDING
        delivery.next_retry_at = now + timedelta(seconds=retry_delay_seconds)
    else:
        delivery.status = NotificationDeliveryStatus.FAILED
        delivery.next_retry_at = None
    await session.flush()
    return delivery
