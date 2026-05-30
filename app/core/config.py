from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "google-calendar-bot"
    app_env: str = "local"
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    log_level: str = "INFO"
    app_skip_external_checks: bool = False
    max_active_requests_per_user: int = 1

    telegram_bot_token: SecretStr | None = None
    telegram_invite_token: str | None = None
    telegram_polling_enabled: bool = True
    telegram_drop_pending_updates_on_start: bool = False
    telegram_admin_id: int | None = None

    background_jobs_enabled: bool = True
    background_reservation_check_interval_seconds: int = 60
    background_reminders_check_interval_seconds: int = 60
    background_technical_check_interval_seconds: int = 120
    background_admin_reminder_after_hours: int = 12
    background_meeting_reminder_before_minutes: int = 120
    background_notification_retry_delay_seconds: int = 60
    background_notification_max_attempts: int = 3
    background_technical_errors_lookback_hours: int = 48

    google_oauth_client_id: str | None = None
    google_oauth_client_secret: SecretStr | None = None
    google_oauth_redirect_uri: str = "urn:ietf:wg:oauth:2.0:oob"
    google_calendar_id: str = "primary"
    google_oauth_scopes: str = "https://www.googleapis.com/auth/calendar"

    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "calendar_bot"
    postgres_user: str = "calendar_user"
    postgres_password: SecretStr = SecretStr("calendar_password")

    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    @property
    def sqlalchemy_url(self) -> str:
        password = self.postgres_password.get_secret_value()
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    def safe_dump(self) -> dict[str, str | int | bool]:
        return {
            "app_name": self.app_name,
            "app_env": self.app_env,
            "app_host": self.app_host,
            "app_port": self.app_port,
            "log_level": self.log_level,
            "app_skip_external_checks": self.app_skip_external_checks,
            "max_active_requests_per_user": self.max_active_requests_per_user,
            "postgres_host": self.postgres_host,
            "postgres_port": self.postgres_port,
            "postgres_db": self.postgres_db,
            "postgres_user": self.postgres_user,
            "redis_host": self.redis_host,
            "redis_port": self.redis_port,
            "redis_db": self.redis_db,
            "telegram_token_provided": bool(self.telegram_bot_token),
            "telegram_invite_token_configured": bool(self.telegram_invite_token),
            "telegram_polling_enabled": self.telegram_polling_enabled,
            "telegram_drop_pending_updates_on_start": self.telegram_drop_pending_updates_on_start,
            "telegram_admin_id_configured": self.telegram_admin_id is not None,
            "background_jobs_enabled": self.background_jobs_enabled,
            "background_reservation_check_interval_seconds": (
                self.background_reservation_check_interval_seconds
            ),
            "background_reminders_check_interval_seconds": (
                self.background_reminders_check_interval_seconds
            ),
            "background_technical_check_interval_seconds": (
                self.background_technical_check_interval_seconds
            ),
            "background_admin_reminder_after_hours": self.background_admin_reminder_after_hours,
            "background_meeting_reminder_before_minutes": (
                self.background_meeting_reminder_before_minutes
            ),
            "background_notification_retry_delay_seconds": (
                self.background_notification_retry_delay_seconds
            ),
            "background_notification_max_attempts": self.background_notification_max_attempts,
            "background_technical_errors_lookback_hours": (
                self.background_technical_errors_lookback_hours
            ),
            "google_oauth_client_id_configured": bool(self.google_oauth_client_id),
            "google_oauth_client_secret_configured": bool(self.google_oauth_client_secret),
            "google_calendar_id": self.google_calendar_id,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
