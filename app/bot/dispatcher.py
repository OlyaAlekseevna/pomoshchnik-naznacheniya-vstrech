import logging

from aiogram import Bot, Dispatcher

from app.bot.handlers import router

logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher.include_router(router)
    return dispatcher


def create_bot(bot_token: str | None) -> Bot | None:
    if not bot_token:
        logger.warning(
            "Telegram bot token is not provided. Bot API calls are disabled.",
            extra={"event": "telegram_token_missing"},
        )
        return None
    return Bot(token=bot_token)
