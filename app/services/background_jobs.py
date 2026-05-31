from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramNotFound,
    TelegramRetryAfter,
    TelegramServerError,
    TelegramUnauthorizedError,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.db.enums import NotificationDeliveryStatus, RequestChangedByRole, RequestStatus
from app.db.models import ConsultationRequest, GoogleOAuthCredential, TechnicalError
from app.db.repositories import (
    append_request_status_history,
    create_technical_error,
    get_google_oauth_credentials,
    get_or_create_notification_delivery,
    get_schedule_settings,
    list_approved_requests_with_users_in_date_range,
    list_expired_active_reservations,
    list_google_technical_errors_since,
    list_requests_for_admin_12h_reminder,
    mark_notification_delivery_retry,
    mark_notification_delivery_sent,
    upsert_google_oauth_credentials,
)
from app.domain.lifecycle import mark_reservation_expired
from app.services.google_calendar import (
    GoogleAuthRequiredError,
    GoogleCalendarService,
    GoogleIntegrationError,
    GooglePermissionDeniedError,
)

logger = logging.getLogger(__name__)

NOTIFICATION_ADMIN_PENDING_12H = "admin_pending_12h"
NOTIFICATION_USER_MEETING_2H = "user_meeting_2h"
NOTIFICATION_ADMIN_MEETING_2H = "admin_meeting_2h"
NOTIFICATION_TECHNICAL_ERROR = "technical_google_error"
NOTIFICATION_TECHNICAL_AUTH_LOST = "technical_google_auth_lost"
NOTIFICATION_TECHNICAL_OAUTH_EXPIRING = "technical_google_oauth_expiring"


def _is_retryable_telegram_error(error: TelegramAPIError) -> tuple[bool, int | None]:
    if isinstance(error, TelegramRetryAfter):
        return True, max(1, int(error.retry_after))
    if isinstance(error, (TelegramNetworkError, TelegramServerError)):
        return True, None
    if isinstance(
        error,
        (
            TelegramBadRequest,
            TelegramForbiddenError,
            TelegramNotFound,
            TelegramUnauthorizedError,
        ),
    ):
        return False, None
    return True, None


def _meeting_start_at_utc(
    request: ConsultationRequest,
    timezone_name: str,
) -> datetime:
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        logger.warning(
            "Timezone fallback is used in background jobs.",
            extra={"event": "timezone_fallback_used", "timezone": timezone_name},
        )
        tz = UTC
    local_start = datetime.combine(request.meeting_date, request.start_time, tzinfo=tz)
    return local_start.astimezone(UTC)


def _normalize_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


class BackgroundJobsService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker[AsyncSession],
        bot: Bot | None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._bot = bot
        self._scheduler = AsyncIOScheduler(timezone=UTC)
        self._is_started = False

    def start(self) -> None:
        if not self._settings.background_jobs_enabled:
            logger.info(
                "Background jobs are disabled by config.",
                extra={"event": "background_jobs_disabled"},
            )
            return
        if self._is_started:
            return

        self._scheduler.add_job(
            self.run_reservation_expiration_job,
            trigger="interval",
            seconds=self._settings.background_reservation_check_interval_seconds,
            id="reservation_expiration_job",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )
        self._scheduler.add_job(
            self.run_reminders_job,
            trigger="interval",
            seconds=self._settings.background_reminders_check_interval_seconds,
            id="reminders_job",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )
        self._scheduler.add_job(
            self.run_technical_notifications_job,
            trigger="interval",
            seconds=self._settings.background_technical_check_interval_seconds,
            id="technical_notifications_job",
            max_instances=1,
            coalesce=True,
            misfire_grace_time=30,
        )
        self._scheduler.start()
        self._is_started = True
        logger.info(
            "Background jobs scheduler started.",
            extra={"event": "background_jobs_started"},
        )

    def shutdown(self) -> None:
        if not self._is_started:
            return
        self._scheduler.shutdown(wait=False)
        self._is_started = False
        logger.info(
            "Background jobs scheduler stopped.",
            extra={"event": "background_jobs_stopped"},
        )

    async def run_reservation_expiration_job(self) -> None:
        job_name = "reservation_expiration_job"
        started_at = datetime.now(UTC)
        logger.info(
            "Background task started.",
            extra={"event": "background_task_started", "job_name": job_name},
        )
        processed = 0
        try:
            async with self._session_factory() as session:
                expired_items = await list_expired_active_reservations(session, now=started_at)
                for reservation, request in expired_items:
                    logger.info(
                        "Expired reservation found by scheduler.",
                        extra={
                            "event": "expired_reservation_found",
                            "request_id": request.id,
                            "reservation_id": reservation.id,
                        },
                    )
                    mark_reservation_expired(request=request, reservation=reservation)
                    await append_request_status_history(
                        session=session,
                        request_id=request.id,
                        status=RequestStatus.RESERVATION_EXPIRED,
                        changed_by_role=RequestChangedByRole.SYSTEM,
                        comment="reservation_expired_by_scheduler",
                    )
                    processed += 1
                    logger.info(
                        "Reservation released by scheduler.",
                        extra={
                            "event": "reservation_released",
                            "request_id": request.id,
                            "reservation_id": reservation.id,
                        },
                    )
                await session.commit()
        except Exception:
            logger.exception(
                "Background reservation expiration task failed.",
                extra={"event": "background_task_error", "job_name": job_name},
            )
            return

        logger.info(
            "Background task completed.",
            extra={
                "event": "background_task_completed",
                "job_name": job_name,
                "processed": processed,
            },
        )

    async def run_reminders_job(self) -> None:
        job_name = "reminders_job"
        logger.info(
            "Background task started.",
            extra={"event": "background_task_started", "job_name": job_name},
        )
        try:
            async with self._session_factory() as session:
                now = datetime.now(UTC)
                await self._send_admin_pending_12h_reminders(session=session, now=now)
                await self._send_meeting_2h_reminders(session=session, now=now)
                await session.commit()
        except Exception:
            logger.exception(
                "Background reminders task failed.",
                extra={"event": "background_task_error", "job_name": job_name},
            )
            return

        logger.info(
            "Background task completed.",
            extra={"event": "background_task_completed", "job_name": job_name},
        )

    async def run_technical_notifications_job(self) -> None:
        job_name = "technical_notifications_job"
        logger.info(
            "Background task started.",
            extra={"event": "background_task_started", "job_name": job_name},
        )
        try:
            async with self._session_factory() as session:
                now = datetime.now(UTC)
                since = now - timedelta(
                    hours=self._settings.background_technical_errors_lookback_hours
                )
                errors = await list_google_technical_errors_since(
                    session=session,
                    created_after=since,
                    limit=100,
                )
                for error in errors:
                    await self._send_technical_error_notification(
                        session=session,
                        error=error,
                        now=now,
                    )
                await self._monitor_google_oauth_token(
                    session=session,
                    now=now,
                )
                await session.commit()
        except Exception:
            logger.exception(
                "Background technical notifications task failed.",
                extra={"event": "background_task_error", "job_name": job_name},
            )
            return

        logger.info(
            "Background task completed.",
            extra={"event": "background_task_completed", "job_name": job_name},
        )

    async def _monitor_google_oauth_token(
        self,
        session: AsyncSession,
        now: datetime,
    ) -> None:
        google_service = GoogleCalendarService(self._settings)
        if not google_service.is_oauth_configured():
            return

        credentials = await get_google_oauth_credentials(session)
        if credentials is None:
            return

        await self._send_google_oauth_expiry_warning(
            session=session,
            credentials=credentials,
            now=now,
        )

        try:
            _, refreshed_tokens = await google_service.get_valid_access_token(credentials)
        except (
            GoogleAuthRequiredError,
            GooglePermissionDeniedError,
            GoogleIntegrationError,
        ) as error:
            technical_error = await create_technical_error(
                session=session,
                source="google_calendar",
                error_code=error.__class__.__name__,
                error_message=str(error),
                details={"source": "background_oauth_monitor"},
            )
            await self._send_technical_error_notification(
                session=session,
                error=technical_error,
                now=now,
            )
            return

        if refreshed_tokens is not None:
            await upsert_google_oauth_credentials(
                session=session,
                refresh_token=credentials.refresh_token,
                access_token=refreshed_tokens.access_token,
                access_token_expires_at=refreshed_tokens.expires_at,
                scope=refreshed_tokens.scope or credentials.scope,
                token_type=refreshed_tokens.token_type or credentials.token_type,
            )

    async def _send_google_oauth_expiry_warning(
        self,
        session: AsyncSession,
        credentials: GoogleOAuthCredential,
        now: datetime,
    ) -> None:
        if self._settings.telegram_admin_id is None:
            return

        warning_minutes = max(0, self._settings.background_google_oauth_expiry_warning_minutes)
        if warning_minutes <= 0:
            return

        expires_at = _normalize_utc(credentials.access_token_expires_at)
        if expires_at is None:
            return

        seconds_left = int((expires_at - now).total_seconds())
        if seconds_left <= 0 or seconds_left > warning_minutes * 60:
            return

        minutes_left = max(1, seconds_left // 60)
        dedupe_key = (
            f"{NOTIFICATION_TECHNICAL_OAUTH_EXPIRING}:"
            f"{credentials.id}:{expires_at.isoformat()}"
        )
        text = (
            "Предупреждение: Google OAuth access token скоро истекает.\n"
            f"Осталось примерно: {minutes_left} мин.\n"
            "Бот попробует обновить токен автоматически, но если придет сообщение "
            "о повторной авторизации — переподключите Google OAuth в админке."
        )
        await self._attempt_send_notification(
            session=session,
            dedupe_key=dedupe_key,
            notification_type=NOTIFICATION_TECHNICAL_OAUTH_EXPIRING,
            target_telegram_user_id=self._settings.telegram_admin_id,
            text=text,
            sent_event="technical_oauth_expiry_warning_sent",
            scheduled_for=expires_at,
        )

    async def _send_admin_pending_12h_reminders(
        self,
        session: AsyncSession,
        now: datetime,
    ) -> None:
        if self._settings.telegram_admin_id is None:
            return
        created_before = now - timedelta(hours=self._settings.background_admin_reminder_after_hours)
        items = await list_requests_for_admin_12h_reminder(
            session=session,
            created_before=created_before,
        )
        for request, user in items:
            dedupe_key = f"{NOTIFICATION_ADMIN_PENDING_12H}:{request.id}"
            text = (
                "Напоминание: заявка ожидает согласования более 12 часов.\n"
                f"Заявка #{request.id}\n"
                f"Пользователь tg: {user.telegram_user_id}\n"
                f"Дата: {request.meeting_date.isoformat()} "
                f"{request.start_time.strftime('%H:%M')}-{request.end_time.strftime('%H:%M')}"
            )
            await self._attempt_send_notification(
                session=session,
                dedupe_key=dedupe_key,
                notification_type=NOTIFICATION_ADMIN_PENDING_12H,
                target_telegram_user_id=self._settings.telegram_admin_id,
                text=text,
                request_id=request.id,
                scheduled_for=created_before,
                sent_event="admin_reminder_sent",
            )

    async def _send_meeting_2h_reminders(
        self,
        session: AsyncSession,
        now: datetime,
    ) -> None:
        schedule_settings = await get_schedule_settings(session)
        date_from = (now - timedelta(days=1)).date()
        date_to = (now + timedelta(days=2)).date()
        requests_with_users = await list_approved_requests_with_users_in_date_range(
            session=session,
            date_from=date_from,
            date_to=date_to,
        )
        for request, user in requests_with_users:
            meeting_start_at = _meeting_start_at_utc(
                request,
                timezone_name=schedule_settings.timezone,
            )
            seconds_before_meeting = (meeting_start_at - now).total_seconds()
            reminder_target_seconds = (
                self._settings.background_meeting_reminder_before_minutes * 60
            )
            check_window_seconds = (
                max(self._settings.background_reminders_check_interval_seconds, 60) * 2
            )
            if seconds_before_meeting < 0:
                continue
            if abs(seconds_before_meeting - reminder_target_seconds) > check_window_seconds:
                continue

            user_text = (
                "Напоминание: встреча начнется через 2 часа.\n"
                f"Заявка #{request.id}\n"
                f"Дата: {request.meeting_date.isoformat()} "
                f"{request.start_time.strftime('%H:%M')}-{request.end_time.strftime('%H:%M')}"
            )
            await self._attempt_send_notification(
                session=session,
                dedupe_key=f"{NOTIFICATION_USER_MEETING_2H}:{request.id}",
                notification_type=NOTIFICATION_USER_MEETING_2H,
                target_telegram_user_id=user.telegram_user_id,
                text=user_text,
                request_id=request.id,
                scheduled_for=meeting_start_at - timedelta(hours=2),
                sent_event="user_reminder_sent",
            )

            if self._settings.telegram_admin_id is not None:
                admin_text = (
                    "Напоминание администратору: встреча через 2 часа.\n"
                    f"Заявка #{request.id}\n"
                    f"Пользователь tg: {user.telegram_user_id}\n"
                    f"Дата: {request.meeting_date.isoformat()} "
                    f"{request.start_time.strftime('%H:%M')}-{request.end_time.strftime('%H:%M')}"
                )
                await self._attempt_send_notification(
                    session=session,
                    dedupe_key=f"{NOTIFICATION_ADMIN_MEETING_2H}:{request.id}",
                    notification_type=NOTIFICATION_ADMIN_MEETING_2H,
                    target_telegram_user_id=self._settings.telegram_admin_id,
                    text=admin_text,
                    request_id=request.id,
                    scheduled_for=meeting_start_at - timedelta(hours=2),
                    sent_event="admin_reminder_sent",
                )

    async def _send_technical_error_notification(
        self,
        session: AsyncSession,
        error: TechnicalError,
        now: datetime,
    ) -> None:
        if self._settings.telegram_admin_id is None:
            return
        notification_type = NOTIFICATION_TECHNICAL_ERROR
        if (error.error_code or "").lower().startswith("googleauthrequired"):
            notification_type = NOTIFICATION_TECHNICAL_AUTH_LOST
        text = (
            "Техническое уведомление по Google Calendar.\n"
            f"Ошибка #{error.id}\n"
            f"Код: {error.error_code or '-'}\n"
            f"Сообщение: {error.error_message}"
        )
        await self._attempt_send_notification(
            session=session,
            dedupe_key=f"{notification_type}:{error.id}",
            notification_type=notification_type,
            target_telegram_user_id=self._settings.telegram_admin_id,
            text=text,
            technical_error_id=error.id,
            sent_event="technical_notification_sent",
            scheduled_for=error.created_at,
        )

    async def _attempt_send_notification(
        self,
        session: AsyncSession,
        dedupe_key: str,
        notification_type: str,
        target_telegram_user_id: int | None,
        text: str,
        sent_event: str,
        request_id: int | None = None,
        technical_error_id: int | None = None,
        scheduled_for: datetime | None = None,
    ) -> None:
        now = datetime.now(UTC)
        delivery = await get_or_create_notification_delivery(
            session=session,
            dedupe_key=dedupe_key,
            notification_type=notification_type,
            target_telegram_user_id=target_telegram_user_id,
            request_id=request_id,
            technical_error_id=technical_error_id,
            scheduled_for=scheduled_for,
        )
        if delivery.status == NotificationDeliveryStatus.SENT:
            return
        if (
            delivery.status == NotificationDeliveryStatus.FAILED
            and delivery.next_retry_at is None
        ):
            return
        next_retry_at = _normalize_utc(delivery.next_retry_at)
        if next_retry_at is not None and next_retry_at > now:
            return
        if self._bot is None or target_telegram_user_id is None:
            await mark_notification_delivery_retry(
                session=session,
                delivery=delivery,
                now=now,
                error_text="Bot is not initialized or target chat is not configured.",
                retry_delay_seconds=self._settings.background_notification_retry_delay_seconds,
                max_attempts=self._settings.background_notification_max_attempts,
                retryable=False,
            )
            logger.error(
                "Notification is not sent: bot or target is unavailable.",
                extra={
                    "event": "notification_not_sent",
                    "notification_type": notification_type,
                    "dedupe_key": dedupe_key,
                },
            )
            return

        try:
            await self._bot.send_message(chat_id=target_telegram_user_id, text=text)
        except TelegramAPIError as error:
            retryable, retry_after = _is_retryable_telegram_error(error)
            retry_delay = (
                retry_after
                if retry_after is not None
                else self._settings.background_notification_retry_delay_seconds
            )
            await mark_notification_delivery_retry(
                session=session,
                delivery=delivery,
                now=now,
                error_text=str(error),
                retry_delay_seconds=retry_delay,
                max_attempts=self._settings.background_notification_max_attempts,
                retryable=retryable,
            )
            if retryable and delivery.status == NotificationDeliveryStatus.PENDING:
                logger.warning(
                    "Notification retry is scheduled.",
                    extra={
                        "event": "notification_retry_scheduled",
                        "notification_type": notification_type,
                        "dedupe_key": dedupe_key,
                        "attempts": delivery.attempts,
                        "next_retry_at": (
                            _normalize_utc(delivery.next_retry_at).isoformat()
                            if _normalize_utc(delivery.next_retry_at) is not None
                            else None
                        ),
                    },
                )
            else:
                logger.error(
                    "Notification is not sent.",
                    extra={
                        "event": "notification_not_sent",
                        "notification_type": notification_type,
                        "dedupe_key": dedupe_key,
                        "attempts": delivery.attempts,
                    },
                )
            await create_technical_error(
                session=session,
                source="telegram_notifications",
                request_id=request_id,
                error_code=error.__class__.__name__,
                error_message=str(error),
                details={
                    "notification_type": notification_type,
                    "dedupe_key": dedupe_key,
                },
            )
            return

        await mark_notification_delivery_sent(session=session, delivery=delivery, sent_at=now)
        logger.info(
            "Notification sent.",
            extra={
                "event": sent_event,
                "notification_type": notification_type,
                "dedupe_key": dedupe_key,
                "request_id": request_id,
                "technical_error_id": technical_error_id,
            },
        )
