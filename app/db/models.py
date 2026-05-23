from datetime import date, datetime, time

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin
from app.db.defaults import (
    DEFAULT_AVAILABLE_DURATIONS_MINUTES,
    DEFAULT_NOTIFICATION_TEMPLATES,
    DEFAULT_USER_WITHOUT_INVITATION_TEXT,
    DEFAULT_WORKING_DAYS,
)
from app.db.enums import (
    GoogleEventStatus,
    NotificationDeliveryStatus,
    RequestChangedByRole,
    RequestStatus,
    ReservationStatus,
)


class User(TimestampMixin, Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    invited_access_granted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_blocked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    data_deletion_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    requests: Mapped[list["ConsultationRequest"]] = relationship(back_populates="user")


class ConsultationRequest(TimestampMixin, Base):
    __tablename__ = "consultation_requests"
    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="duration_minutes_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    phone: Mapped[str] = mapped_column(String(50), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    meeting_goal: Mapped[str] = mapped_column(Text, nullable=False)
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    meeting_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, name="request_status", native_enum=False),
        nullable=False,
        index=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    alternative_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    alternative_start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    alternative_end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    reservation_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    personal_data_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    user: Mapped["User"] = relationship(back_populates="requests")
    status_history: Mapped[list["RequestStatusHistory"]] = relationship(back_populates="request")
    reservations: Mapped[list["SlotReservation"]] = relationship(back_populates="request")
    google_events: Mapped[list["GoogleCalendarEvent"]] = relationship(back_populates="request")


class ScheduleSettings(TimestampMixin, Base):
    __tablename__ = "schedule_settings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="Asia/Yekaterinburg")
    workday_start: Mapped[time] = mapped_column(Time, nullable=False)
    workday_end: Mapped[time] = mapped_column(Time, nullable=False)
    working_days: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: list(DEFAULT_WORKING_DAYS),
    )
    available_durations_minutes: Mapped[list[int]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: list(DEFAULT_AVAILABLE_DURATIONS_MINUTES),
    )
    min_notice_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=120)
    buffer_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    max_consultations_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    booking_horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=28)
    notification_templates: Mapped[dict[str, str]] = mapped_column(
        JSON,
        nullable=False,
        default=lambda: dict(DEFAULT_NOTIFICATION_TEMPLATES),
    )
    user_without_invitation_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=DEFAULT_USER_WITHOUT_INVITATION_TEXT,
    )


class ForbiddenDate(Base):
    __tablename__ = "forbidden_dates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    day: Mapped[date] = mapped_column(Date, nullable=False, unique=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ForbiddenPeriod(Base):
    __tablename__ = "forbidden_periods"
    __table_args__ = (
        CheckConstraint("start_at < end_at", name="forbidden_period_valid_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SlotReservation(Base):
    __tablename__ = "slot_reservations"
    __table_args__ = (
        CheckConstraint("start_at < end_at", name="reservation_valid_range"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("consultation_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[ReservationStatus] = mapped_column(
        Enum(ReservationStatus, name="reservation_status", native_enum=False),
        nullable=False,
        index=True,
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    request: Mapped["ConsultationRequest"] = relationship(back_populates="reservations")


class GoogleCalendarEvent(Base):
    __tablename__ = "google_calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("consultation_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    event_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_in_google_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    creation_status: Mapped[GoogleEventStatus] = mapped_column(
        Enum(GoogleEventStatus, name="google_event_status", native_enum=False),
        nullable=False,
        index=True,
    )
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    request: Mapped["ConsultationRequest"] = relationship(back_populates="google_events")


class GoogleOAuthCredential(TimestampMixin, Base):
    __tablename__ = "google_oauth_credentials"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    refresh_token: Mapped[str] = mapped_column(Text, nullable=False)
    access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_type: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RequestStatusHistory(Base):
    __tablename__ = "request_status_history"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    request_id: Mapped[int] = mapped_column(
        ForeignKey("consultation_requests.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[RequestStatus] = mapped_column(
        Enum(RequestStatus, name="request_status", native_enum=False),
        nullable=False,
    )
    changed_by_role: Mapped[RequestChangedByRole] = mapped_column(
        Enum(RequestChangedByRole, name="request_changed_by_role", native_enum=False),
        nullable=False,
    )
    changed_by_telegram_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    request: Mapped["ConsultationRequest"] = relationship(back_populates="status_history")


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    admin_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    action_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("consultation_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    payload: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TechnicalError(Base):
    __tablename__ = "technical_errors"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("consultation_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    error_code: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, nullable=False)
    details: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class NotificationDelivery(TimestampMixin, Base):
    __tablename__ = "notification_deliveries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True, index=True)
    notification_type: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    request_id: Mapped[int | None] = mapped_column(
        ForeignKey("consultation_requests.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    technical_error_id: Mapped[int | None] = mapped_column(
        ForeignKey("technical_errors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    target_telegram_user_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )
    status: Mapped[NotificationDeliveryStatus] = mapped_column(
        Enum(NotificationDeliveryStatus, name="notification_delivery_status", native_enum=False),
        nullable=False,
        index=True,
        default=NotificationDeliveryStatus.PENDING,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_for: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
