"""stage7 notification deliveries

Revision ID: 20260523_02
Revises: 20260523_01
Create Date: 2026-05-23 22:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op
from app.db.enums import NotificationDeliveryStatus

# revision identifiers, used by Alembic.
revision: str = "20260523_02"
down_revision: str | None = "20260523_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    status_enum = sa.Enum(
        NotificationDeliveryStatus,
        name="notification_delivery_status",
        native_enum=False,
    )
    op.create_table(
        "notification_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False),
        sa.Column("notification_type", sa.String(length=120), nullable=False),
        sa.Column("request_id", sa.Integer(), nullable=True),
        sa.Column("technical_error_id", sa.Integer(), nullable=True),
        sa.Column("target_telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("status", status_enum, nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
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
            name=op.f("fk_notification_deliveries_request_id_consultation_requests"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["technical_error_id"],
            ["technical_errors.id"],
            name=op.f("fk_notification_deliveries_technical_error_id_technical_errors"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_deliveries")),
        sa.UniqueConstraint("dedupe_key", name=op.f("uq_notification_deliveries_dedupe_key")),
    )
    op.create_index(
        op.f("ix_notification_deliveries_dedupe_key"),
        "notification_deliveries",
        ["dedupe_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_deliveries_notification_type"),
        "notification_deliveries",
        ["notification_type"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_deliveries_request_id"),
        "notification_deliveries",
        ["request_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_deliveries_technical_error_id"),
        "notification_deliveries",
        ["technical_error_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_deliveries_target_telegram_user_id"),
        "notification_deliveries",
        ["target_telegram_user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_notification_deliveries_status"),
        "notification_deliveries",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_notification_deliveries_status"),
        table_name="notification_deliveries",
    )
    op.drop_index(
        op.f("ix_notification_deliveries_target_telegram_user_id"),
        table_name="notification_deliveries",
    )
    op.drop_index(
        op.f("ix_notification_deliveries_technical_error_id"),
        table_name="notification_deliveries",
    )
    op.drop_index(
        op.f("ix_notification_deliveries_request_id"),
        table_name="notification_deliveries",
    )
    op.drop_index(
        op.f("ix_notification_deliveries_notification_type"),
        table_name="notification_deliveries",
    )
    op.drop_index(
        op.f("ix_notification_deliveries_dedupe_key"),
        table_name="notification_deliveries",
    )
    op.drop_table("notification_deliveries")
