"""stage2 initial schema

Revision ID: 20260522_01
Revises:
Create Date: 2026-05-22 10:00:00.000000
"""

from collections.abc import Sequence
from datetime import time as dt_time

import sqlalchemy as sa

from alembic import op
from app.db.defaults import (
    DEFAULT_AVAILABLE_DURATIONS_MINUTES,
    DEFAULT_NOTIFICATION_TEMPLATES,
    DEFAULT_USER_WITHOUT_INVITATION_TEXT,
    DEFAULT_WORKING_DAYS,
)
from app.db.enums import GoogleEventStatus, RequestChangedByRole, RequestStatus, ReservationStatus

# revision identifiers, used by Alembic.
revision: str = "20260522_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    request_status_enum = sa.Enum(RequestStatus, name="request_status", native_enum=False)
    request_changed_by_role_enum = sa.Enum(
        RequestChangedByRole,
        name="request_changed_by_role",
        native_enum=False,
    )
    reservation_status_enum = sa.Enum(
        ReservationStatus,
        name="reservation_status",
        native_enum=False,
    )
    google_event_status_enum = sa.Enum(
        GoogleEventStatus,
        name="google_event_status",
        native_enum=False,
    )

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("first_name", sa.String(length=255), nullable=True),
        sa.Column("last_name", sa.String(length=255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "invited_access_granted",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("is_blocked", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("data_deletion_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("telegram_user_id", name=op.f("uq_users_telegram_user_id")),
    )
    op.create_index(op.f("ix_users_telegram_user_id"), "users", ["telegram_user_id"], unique=False)

    op.create_table(
        "consultation_requests",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("meeting_goal", sa.Text(), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("meeting_date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.Column("status", request_status_enum, nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("alternative_date", sa.Date(), nullable=True),
        sa.Column("alternative_start_time", sa.Time(), nullable=True),
        sa.Column("alternative_end_time", sa.Time(), nullable=True),
        sa.Column("reservation_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("personal_data_consent", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_minutes > 0",
            name=op.f("ck_consultation_requests_duration_minutes_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_consultation_requests_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_consultation_requests")),
    )
    op.create_index(
        op.f("ix_consultation_requests_user_id"),
        "consultation_requests",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_consultation_requests_meeting_date"),
        "consultation_requests",
        ["meeting_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_consultation_requests_status"),
        "consultation_requests",
        ["status"],
        unique=False,
    )

    op.create_table(
        "schedule_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("workday_start", sa.Time(), nullable=False),
        sa.Column("workday_end", sa.Time(), nullable=False),
        sa.Column("working_days", sa.JSON(), nullable=False),
        sa.Column("available_durations_minutes", sa.JSON(), nullable=False),
        sa.Column("min_notice_minutes", sa.Integer(), nullable=False),
        sa.Column("buffer_minutes", sa.Integer(), nullable=False),
        sa.Column("max_consultations_per_day", sa.Integer(), nullable=False),
        sa.Column("booking_horizon_days", sa.Integer(), nullable=False),
        sa.Column("notification_templates", sa.JSON(), nullable=False),
        sa.Column("user_without_invitation_text", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schedule_settings")),
    )

    op.create_table(
        "forbidden_dates",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_forbidden_dates")),
        sa.UniqueConstraint("day", name=op.f("uq_forbidden_dates_day")),
    )
    op.create_index(op.f("ix_forbidden_dates_day"), "forbidden_dates", ["day"], unique=False)

    op.create_table(
        "forbidden_periods",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "start_at < end_at",
            name=op.f("ck_forbidden_periods_forbidden_period_valid_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_forbidden_periods")),
    )

    op.create_table(
        "slot_reservations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", reservation_status_enum, nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "start_at < end_at",
            name=op.f("ck_slot_reservations_reservation_valid_range"),
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["consultation_requests.id"],
            name=op.f("fk_slot_reservations_request_id_consultation_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_slot_reservations")),
    )
    op.create_index(
        op.f("ix_slot_reservations_request_id"),
        "slot_reservations",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_slot_reservations_status"),
        "slot_reservations",
        ["status"],
        unique=False,
    )

    op.create_table(
        "google_calendar_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("google_event_id", sa.String(length=255), nullable=True),
        sa.Column("event_url", sa.String(length=1024), nullable=True),
        sa.Column("created_in_google_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("creation_status", google_event_status_enum, nullable=False),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["consultation_requests.id"],
            name=op.f("fk_google_calendar_events_request_id_consultation_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_google_calendar_events")),
    )
    op.create_index(
        op.f("ix_google_calendar_events_request_id"),
        "google_calendar_events",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_google_calendar_events_creation_status"),
        "google_calendar_events",
        ["creation_status"],
        unique=False,
    )

    op.create_table(
        "request_status_history",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=False),
        sa.Column("status", request_status_enum, nullable=False),
        sa.Column("changed_by_role", request_changed_by_role_enum, nullable=False),
        sa.Column("changed_by_telegram_id", sa.BigInteger(), nullable=True),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["consultation_requests.id"],
            name=op.f("fk_request_status_history_request_id_consultation_requests"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_request_status_history")),
    )
    op.create_index(
        op.f("ix_request_status_history_request_id"),
        "request_status_history",
        ["request_id"],
        unique=False,
    )

    op.create_table(
        "admin_audit_logs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("admin_telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("action_type", sa.String(length=120), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=True),
        sa.Column("target_user_id", sa.Integer(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["consultation_requests.id"],
            name=op.f("fk_admin_audit_logs_request_id_consultation_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["target_user_id"],
            ["users.id"],
            name=op.f("fk_admin_audit_logs_target_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_logs")),
    )
    op.create_index(
        op.f("ix_admin_audit_logs_admin_telegram_id"),
        "admin_audit_logs",
        ["admin_telegram_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_admin_audit_logs_action_type"),
        "admin_audit_logs",
        ["action_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_admin_audit_logs_request_id"),
        "admin_audit_logs",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_admin_audit_logs_target_user_id"),
        "admin_audit_logs",
        ["target_user_id"],
        unique=False,
    )

    op.create_table(
        "technical_errors",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=120), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["consultation_requests.id"],
            name=op.f("fk_technical_errors_request_id_consultation_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_technical_errors_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_technical_errors")),
    )
    op.create_index(
        op.f("ix_technical_errors_source"),
        "technical_errors",
        ["source"],
        unique=False,
    )
    op.create_index(
        op.f("ix_technical_errors_request_id"),
        "technical_errors",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_technical_errors_user_id"),
        "technical_errors",
        ["user_id"],
        unique=False,
    )

    settings_table = sa.table(
        "schedule_settings",
        sa.column("timezone", sa.String),
        sa.column("workday_start", sa.Time),
        sa.column("workday_end", sa.Time),
        sa.column("working_days", sa.JSON),
        sa.column("available_durations_minutes", sa.JSON),
        sa.column("min_notice_minutes", sa.Integer),
        sa.column("buffer_minutes", sa.Integer),
        sa.column("max_consultations_per_day", sa.Integer),
        sa.column("booking_horizon_days", sa.Integer),
        sa.column("notification_templates", sa.JSON),
        sa.column("user_without_invitation_text", sa.Text),
    )
    op.bulk_insert(
        settings_table,
        [
            {
                "timezone": "Asia/Yekaterinburg",
                "workday_start": dt_time(hour=10, minute=0),
                "workday_end": dt_time(hour=18, minute=0),
                "working_days": DEFAULT_WORKING_DAYS,
                "available_durations_minutes": DEFAULT_AVAILABLE_DURATIONS_MINUTES,
                "min_notice_minutes": 120,
                "buffer_minutes": 60,
                "max_consultations_per_day": 3,
                "booking_horizon_days": 28,
                "notification_templates": DEFAULT_NOTIFICATION_TEMPLATES,
                "user_without_invitation_text": DEFAULT_USER_WITHOUT_INVITATION_TEXT,
            }
        ],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_technical_errors_user_id"), table_name="technical_errors")
    op.drop_index(op.f("ix_technical_errors_request_id"), table_name="technical_errors")
    op.drop_index(op.f("ix_technical_errors_source"), table_name="technical_errors")
    op.drop_table("technical_errors")

    op.drop_index(op.f("ix_admin_audit_logs_target_user_id"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_request_id"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_action_type"), table_name="admin_audit_logs")
    op.drop_index(op.f("ix_admin_audit_logs_admin_telegram_id"), table_name="admin_audit_logs")
    op.drop_table("admin_audit_logs")

    op.drop_index(op.f("ix_request_status_history_request_id"), table_name="request_status_history")
    op.drop_table("request_status_history")

    op.drop_index(
        op.f("ix_google_calendar_events_creation_status"),
        table_name="google_calendar_events",
    )
    op.drop_index(op.f("ix_google_calendar_events_request_id"), table_name="google_calendar_events")
    op.drop_table("google_calendar_events")

    op.drop_index(op.f("ix_slot_reservations_status"), table_name="slot_reservations")
    op.drop_index(op.f("ix_slot_reservations_request_id"), table_name="slot_reservations")
    op.drop_table("slot_reservations")

    op.drop_table("forbidden_periods")

    op.drop_index(op.f("ix_forbidden_dates_day"), table_name="forbidden_dates")
    op.drop_table("forbidden_dates")

    op.drop_table("schedule_settings")

    op.drop_index(op.f("ix_consultation_requests_status"), table_name="consultation_requests")
    op.drop_index(op.f("ix_consultation_requests_meeting_date"), table_name="consultation_requests")
    op.drop_index(op.f("ix_consultation_requests_user_id"), table_name="consultation_requests")
    op.drop_table("consultation_requests")

    op.drop_index(op.f("ix_users_telegram_user_id"), table_name="users")
    op.drop_table("users")
