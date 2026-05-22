from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message

router = Router(name="base-router")


@router.message(CommandStart())
async def on_start(message: Message) -> None:
    await message.answer(
        "Бот запущен. Сейчас включен только базовый каркас MVP."
    )


@router.message(F.text)
async def echo_text(message: Message) -> None:
    await message.answer(
        "Каркас приложения активен. Основной flow будет добавлен на следующих этапах."
    )
