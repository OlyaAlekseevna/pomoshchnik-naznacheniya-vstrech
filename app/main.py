import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from typing import Any

from aiogram.exceptions import TelegramAPIError
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.bot.dispatcher import create_bot, create_dispatcher
from app.bot.handlers import configure_session_factory
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.db.session import create_session_factory
from app.services.db import close_engine, create_engine, ping_database
from app.services.redis_client import close_redis, create_redis_client, ping_redis

logger = logging.getLogger(__name__)


def _log_polling_task_result(task: asyncio.Task[None]) -> None:
    try:
        task.result()
    except asyncio.CancelledError:
        return
    except Exception:
        logger.exception(
            "Aiogram polling terminated with error.",
            extra={"event": "aiogram_polling_error"},
        )


async def _run_external_checks(
    settings: Settings,
    engine: AsyncEngine,
    redis_client: Redis,
) -> dict[str, str]:
    checks: dict[str, str] = {}

    if settings.app_skip_external_checks:
        checks["postgresql"] = "skipped"
        checks["redis"] = "skipped"
        logger.warning(
            "External checks are skipped by APP_SKIP_EXTERNAL_CHECKS.",
            extra={"event": "external_checks_skipped"},
        )
        return checks

    try:
        await ping_database(engine)
        checks["postgresql"] = "ok"
        logger.info("PostgreSQL connection successful.", extra={"event": "postgres_connected"})
    except Exception:
        checks["postgresql"] = "error"
        logger.exception("PostgreSQL connection failed.", extra={"event": "postgres_error"})
        raise

    try:
        await ping_redis(redis_client)
        checks["redis"] = "ok"
        logger.info("Redis connection successful.", extra={"event": "redis_connected"})
    except Exception:
        checks["redis"] = "error"
        logger.exception("Redis connection failed.", extra={"event": "redis_error"})
        raise

    return checks


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or get_settings()
    configure_logging(app_settings.log_level)
    logger.info(
        "Configuration loaded.",
        extra={"event": "config_loaded", "config": app_settings.safe_dump()},
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        logger.info("Application startup initiated.", extra={"event": "app_startup_started"})

        engine: AsyncEngine = create_engine(app_settings.sqlalchemy_url)
        session_factory = create_session_factory(engine)
        redis_client: Redis = create_redis_client(app_settings.redis_url)
        dispatcher = create_dispatcher()
        configure_session_factory(session_factory)
        bot_token = (
            app_settings.telegram_bot_token.get_secret_value()
            if app_settings.telegram_bot_token is not None
            else None
        )
        bot = create_bot(bot_token)
        polling_task: asyncio.Task[None] | None = None
        if bot is not None:
            logger.info("Aiogram bot initialized.", extra={"event": "aiogram_initialized"})
            if app_settings.telegram_polling_enabled:
                logger.info(
                    "Starting aiogram polling.",
                    extra={"event": "aiogram_polling_starting"},
                )
                try:
                    await bot.delete_webhook(drop_pending_updates=True)
                    logger.info(
                        "Telegram webhook deleted before polling.",
                        extra={"event": "telegram_webhook_deleted_for_polling"},
                    )
                except TelegramAPIError:
                    logger.exception(
                        "Failed to delete Telegram webhook before polling.",
                        extra={"event": "telegram_webhook_delete_error"},
                    )
                polling_task = asyncio.create_task(
                    dispatcher.start_polling(
                        bot,
                        allowed_updates=dispatcher.resolve_used_update_types(),
                    )
                )
                polling_task.add_done_callback(_log_polling_task_result)
            else:
                logger.info(
                    "Aiogram polling is disabled by config.",
                    extra={"event": "aiogram_polling_disabled"},
                )

        application.state.settings = app_settings
        application.state.engine = engine
        application.state.redis_client = redis_client
        application.state.dispatcher = dispatcher
        application.state.bot = bot
        application.state.polling_task = polling_task

        await _run_external_checks(app_settings, engine, redis_client)

        logger.info("Application started.", extra={"event": "app_started"})
        try:
            yield
        finally:
            logger.info("Application shutdown initiated.", extra={"event": "app_shutdown_started"})
            polling_task = getattr(application.state, "polling_task", None)
            if polling_task is not None:
                polling_task.cancel()
                with suppress(asyncio.CancelledError):
                    await polling_task
                logger.info(
                    "Aiogram polling stopped.",
                    extra={"event": "aiogram_polling_stopped"},
                )
            if bot is not None:
                await bot.session.close()
            await close_redis(redis_client)
            await close_engine(engine)
            logger.info("Application stopped.", extra={"event": "app_stopped"})

    app = FastAPI(title=app_settings.app_name, lifespan=lifespan)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"message": "Google Calendar meeting bot backend is running"}

    @app.get("/health")
    async def health() -> JSONResponse:
        logger.info("Health-check called.", extra={"event": "health_check_called"})
        required_state = ("settings", "engine", "redis_client")
        if not all(hasattr(app.state, key) for key in required_state):
            logger.error(
                "Application state is not initialized yet.",
                extra={"event": "health_state_not_ready"},
            )
            return JSONResponse(
                status_code=503,
                content={
                    "status": "error",
                    "checks": {"application": "not_ready"},
                    "service": app_settings.app_name,
                },
            )

        settings_from_state: Settings = app.state.settings
        engine: AsyncEngine = app.state.engine
        redis_client: Redis = app.state.redis_client

        checks: dict[str, str]
        status_code = 200
        try:
            checks = await _run_external_checks(settings_from_state, engine, redis_client)
            status = "ok"
        except Exception as error:
            checks = {
                "postgresql": "error" if "postgres" in str(error).lower() else "unknown",
                "redis": "error" if "redis" in str(error).lower() else "unknown",
            }
            status = "error"
            status_code = 503

        payload: dict[str, str | dict[str, str] | dict[str, Any]] = {
            "status": status,
            "checks": checks,
            "service": app_settings.app_name,
        }
        return JSONResponse(status_code=status_code, content=payload)

    return app


app = create_app()
