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

    telegram_bot_token: SecretStr | None = None
    telegram_invite_token: str | None = None
    telegram_polling_enabled: bool = True
    telegram_drop_pending_updates_on_start: bool = False

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
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
