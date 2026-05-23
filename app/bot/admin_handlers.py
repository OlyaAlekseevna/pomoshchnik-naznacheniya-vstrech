from __future__ import annotations

import logging
from collections.abc import Callable

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.admin_service import (
    ALTERNATIVE_REJECTION_REASON,
    SlotUnavailableOnApprovalError,
    apply_setting_update,
    approve_request_with_calendar,
    build_google_oauth_instructions,
    build_history_text,
    build_request_card,
    build_settings_summary,
    connect_google_oauth_with_code,
    get_request_history,
    get_requests_for_admin,
    get_user_for_request,
    is_admin_telegram_id,
    manual_create_meeting_for_user,
    parse_alternative_slot,
    reject_request,
    reject_with_alternative_slot,
    toggle_user_block,
)
from app.bot.keyboards import (
    admin_main_keyboard,
    admin_request_actions_keyboard,
    admin_settings_keyboard,
)
from app.bot.states import AdminFlowState
from app.core.config import get_settings
from app.db.repositories import get_request_by_id, get_schedule_settings
from app.domain.exceptions import BusinessRuleViolation
from app.services.google_calendar import (
    GoogleAuthRequiredError,
    GoogleCalendarService,
    GoogleIntegrationError,
    GooglePermissionDeniedError,
)

router = Router(name="admin-flow-router")
logger = logging.getLogger(__name__)

_session_factory: async_sessionmaker[AsyncSession] | None = None


def configure_session_factory(session_factory: async_sessionmaker[AsyncSession]) -> None:
    global _session_factory
    _session_factory = session_factory


def _require_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Session factory is not configured for admin bot handlers.")
    return _session_factory


def _google_service_from_settings() -> GoogleCalendarService:
    return GoogleCalendarService(get_settings())


async def _safe_answer(message: Message, text: str, **kwargs) -> None:
    try:
        await message.answer(text, **kwargs)
    except TelegramAPIError:
        logger.exception(
            "Failed to send admin message.",
            extra={
                "event": "message_send_error",
                "telegram_user_id": message.from_user.id if message.from_user else None,
            },
        )


async def _safe_callback(query: CallbackQuery, text: str) -> None:
    try:
        await query.answer(text)
    except TelegramAPIError:
        logger.exception(
            "Failed to answer admin callback.",
            extra={
                "event": "message_send_error",
                "telegram_user_id": query.from_user.id if query.from_user else None,
            },
        )


async def _safe_notify_user(
    bot_send: Callable[[int, str], object],
    user_telegram_id: int,
    text: str,
) -> None:
    try:
        await bot_send(user_telegram_id, text)
    except TelegramAPIError:
        logger.exception(
            "Failed to send notification to user.",
            extra={"event": "message_send_error", "target_telegram_user_id": user_telegram_id},
        )


async def _admin_guard_for_message(message: Message) -> bool:
    settings = get_settings()
    telegram_id = message.from_user.id if message.from_user else None
    if is_admin_telegram_id(telegram_id, settings):
        return True
    logger.warning(
        "Non-admin tried to open admin section.",
        extra={"event": "admin_access_denied", "telegram_user_id": telegram_id},
    )
    await _safe_answer(message, "Доступ запрещен: раздел только для администратора.")
    return False


async def _admin_guard_for_callback(query: CallbackQuery) -> bool:
    settings = get_settings()
    telegram_id = query.from_user.id if query.from_user else None
    if is_admin_telegram_id(telegram_id, settings):
        return True
    logger.warning(
        "Non-admin tried to execute admin callback.",
        extra={
            "event": "admin_access_denied",
            "telegram_user_id": telegram_id,
            "callback_data": query.data,
        },
    )
    await _safe_callback(query, "Доступ запрещен")
    if query.message is not None:
        await _safe_answer(query.message, "Доступ запрещен: раздел только для администратора.")
    return False


def _extract_request_id(raw_callback_data: str) -> int:
    return int(raw_callback_data.rsplit(":", maxsplit=1)[1])


def _setting_prompt(setting_key: str) -> str:
    prompts = {
        "working_days": "Введите дни недели через запятую: monday,tuesday,...",
        "working_hours": "Введите рабочие часы в формате HH:MM-HH:MM",
        "durations": "Введите длительности в минутах через запятую. Пример: 15,30,45,90",
        "min_notice": "Введите минимальное время до встречи в минутах. Пример: 120",
        "buffer": "Введите буфер в минутах. Пример: 60",
        "daily_limit": "Введите лимит консультаций в день. Пример: 3",
        "horizon": "Введите горизонт записи в днях. Пример: 28",
        "forbidden_date": (
            "Введите запрещенную дату. Причина опционально через '|'. "
            "Пример: 2026-06-01|выходной"
        ),
        "forbidden_period": (
            "Введите запрещенный период. Причина опционально через '|'. "
            "Пример: 2026-06-01 10:00 - 2026-06-01 14:00|технические работы"
        ),
        "new_request_text": "Введите новый шаблон уведомления администратору о заявке.",
    }
    return prompts.get(setting_key, "Введите значение:")


@router.message(Command("admin"))
async def open_admin_panel(message: Message, state: FSMContext) -> None:
    if not await _admin_guard_for_message(message):
        return
    await state.clear()
    logger.info(
        "Admin opened admin section.",
        extra={
            "event": "admin_section_opened",
            "telegram_user_id": message.from_user.id if message.from_user else None,
        },
    )
    await _safe_answer(message, "Панель администратора:", reply_markup=admin_main_keyboard())


@router.callback_query(F.data == "admin:menu")
async def admin_menu(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.clear()
    await _safe_callback(query, "Открыто админ-меню.")
    if query.message is not None:
        await _safe_answer(
            query.message,
            "Панель администратора:",
            reply_markup=admin_main_keyboard(),
        )


@router.callback_query(F.data == "admin:req:list")
async def list_requests(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.clear()
    session_factory = _require_session_factory()
    async with session_factory() as session:
        requests = await get_requests_for_admin(session=session, limit=20)
        if not requests:
            await _safe_callback(query, "Заявок нет.")
            if query.message is not None:
                await _safe_answer(query.message, "Заявки не найдены.")
            return
        await _safe_callback(query, "Заявки загружены.")
        if query.message is None:
            return
        for item in requests:
            user = await get_user_for_request(session=session, request=item)
            await _safe_answer(
                query.message,
                build_request_card(item, user),
                reply_markup=admin_request_actions_keyboard(
                    request_id=item.id,
                    is_user_blocked=user.is_blocked if user is not None else False,
                ),
            )


@router.callback_query(F.data == "admin:settings")
async def open_settings(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.clear()
    session_factory = _require_session_factory()
    async with session_factory() as session:
        settings = await get_schedule_settings(session)
    await _safe_callback(query, "Настройки загружены.")
    if query.message is not None:
        await _safe_answer(
            query.message,
            build_settings_summary(settings),
            reply_markup=admin_settings_keyboard(),
        )


@router.callback_query(F.data.startswith("admin:set:"))
async def select_setting_to_edit(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    setting_key = query.data.split(":", maxsplit=2)[2]
    await state.set_state(AdminFlowState.editing_setting_value)
    await state.update_data(admin_setting_key=setting_key)
    await _safe_callback(query, "Отправьте новое значение.")
    if query.message is not None:
        await _safe_answer(query.message, _setting_prompt(setting_key))


@router.message(AdminFlowState.editing_setting_value)
async def apply_setting_value(message: Message, state: FSMContext) -> None:
    if not await _admin_guard_for_message(message):
        return
    data = await state.get_data()
    setting_key = str(data.get("admin_setting_key", "")).strip()
    if not setting_key:
        await state.clear()
        await _safe_answer(message, "Параметр не выбран. Откройте /admin снова.")
        return
    raw_value = (message.text or "").strip()
    session_factory = _require_session_factory()
    async with session_factory() as session:
        try:
            summary_text = await apply_setting_update(
                session=session,
                admin_telegram_id=message.from_user.id,
                setting_key=setting_key,
                raw_value=raw_value,
            )
        except Exception as error:
            await _safe_answer(message, f"Некорректное значение: {error}")
            return
        await session.commit()

    if setting_key == "forbidden_date":
        logger.info(
            "Admin added forbidden date.",
            extra={
                "event": "admin_forbidden_date_added",
                "telegram_user_id": message.from_user.id,
            },
        )
    else:
        logger.info(
            "Admin changed setting.",
            extra={
                "event": "admin_setting_updated",
                "telegram_user_id": message.from_user.id,
                "setting_key": setting_key,
            },
        )
    await state.clear()
    await _safe_answer(message, summary_text, reply_markup=admin_settings_keyboard())


@router.callback_query(F.data == "admin:google:connect")
async def start_google_connect(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.set_state(AdminFlowState.entering_google_auth_code)
    settings = get_settings()
    instructions = build_google_oauth_instructions(
        settings=settings,
        service=_google_service_from_settings(),
    )
    await _safe_callback(query, "Инструкция отправлена.")
    if query.message is not None:
        await _safe_answer(
            query.message,
            instructions,
            reply_markup=admin_settings_keyboard(),
        )


@router.message(AdminFlowState.entering_google_auth_code)
async def finish_google_connect(message: Message, state: FSMContext) -> None:
    if not await _admin_guard_for_message(message):
        return
    authorization_code = (message.text or "").strip()
    if not authorization_code:
        await _safe_answer(message, "Код пустой. Вставьте code из шага Google OAuth.")
        return

    session_factory = _require_session_factory()
    async with session_factory() as session:
        try:
            result_text = await connect_google_oauth_with_code(
                session=session,
                admin_telegram_id=message.from_user.id,
                authorization_code=authorization_code,
                service=_google_service_from_settings(),
            )
        except (GoogleIntegrationError, ValueError) as error:
            await _safe_answer(message, f"Не удалось подключить Google OAuth: {error}")
            return
        await session.commit()
    await state.clear()
    await _safe_answer(message, result_text, reply_markup=admin_settings_keyboard())


@router.callback_query(F.data.startswith("admin:req:approve:"))
async def approve_request_action(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.clear()
    request_id = _extract_request_id(query.data)
    session_factory = _require_session_factory()
    async with session_factory() as session:
        try:
            result = await approve_request_with_calendar(
                session=session,
                request_id=request_id,
                admin_telegram_id=query.from_user.id,
                settings=get_settings(),
                service=_google_service_from_settings(),
            )
        except LookupError:
            await _safe_callback(query, "Нельзя согласовать эту заявку.")
            return
        except SlotUnavailableOnApprovalError:
            await session.commit()
            request = await get_request_by_id(session, request_id=request_id)
            user = await get_user_for_request(session, request) if request is not None else None
            await _safe_callback(query, "Слот уже занят.")
            if query.message is not None:
                await _safe_answer(
                    query.message,
                    (
                        f"Заявка #{request_id}: слот уже занят при повторной проверке. "
                        "Попросите пользователя выбрать новое время."
                    ),
                )
            if user is not None and query.message is not None:
                await _safe_notify_user(
                    query.message.bot.send_message,
                    user.telegram_user_id,
                    (
                        f"По заявке #{request_id} выбранный слот уже занят. "
                        "Пожалуйста, создайте новую заявку на другое время."
                    ),
                )
            return
        except GoogleAuthRequiredError:
            await _safe_callback(query, "Нужна повторная авторизация Google.")
            if query.message is not None:
                await _safe_answer(
                    query.message,
                    (
                        "Google OAuth требует повторной авторизации. "
                        "Откройте раздел «Подключить Google Calendar» в /admin."
                    ),
                )
            return
        except (GooglePermissionDeniedError, GoogleIntegrationError) as error:
            await session.commit()
            await _safe_callback(query, "Ошибка Google Calendar.")
            if query.message is not None:
                await _safe_answer(
                    query.message,
                    f"Не удалось создать событие в Google Calendar: {error}",
                )
            return
        except (BusinessRuleViolation, RuntimeError):
            await _safe_callback(query, "Нельзя согласовать эту заявку.")
            return
        await session.commit()
    logger.info(
        "Admin approved request.",
        extra={
            "event": "admin_request_approved",
            "request_id": request_id,
            "telegram_user_id": query.from_user.id,
        },
    )
    await _safe_callback(query, "Заявка согласована.")
    if query.message is not None:
        admin_message = f"Заявка #{request_id} согласована."
        if result.event_url:
            admin_message += f"\nСсылка на событие: {result.event_url}"
        await _safe_answer(query.message, admin_message)
    if result.user is not None and query.message is not None:
        user_message = f"Ваша заявка #{request_id} согласована."
        if result.event_url:
            user_message += f"\nСсылка на событие: {result.event_url}"
        await _safe_notify_user(
            query.message.bot.send_message,
            result.user.telegram_user_id,
            user_message,
        )


@router.callback_query(F.data.startswith("admin:req:reject:"))
async def reject_request_action(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.clear()
    request_id = _extract_request_id(query.data)
    session_factory = _require_session_factory()
    async with session_factory() as session:
        try:
            request = await reject_request(
                session=session,
                request_id=request_id,
                admin_telegram_id=query.from_user.id,
                reason="Отклонено администратором",
            )
        except (LookupError, BusinessRuleViolation):
            await _safe_callback(query, "Нельзя отклонить эту заявку.")
            return
        user = await get_user_for_request(session, request)
        await session.commit()
    logger.info(
        "Admin rejected request.",
        extra={
            "event": "admin_request_rejected",
            "request_id": request_id,
            "telegram_user_id": query.from_user.id,
        },
    )
    await _safe_callback(query, "Заявка отклонена.")
    if query.message is not None:
        await _safe_answer(query.message, f"Заявка #{request_id} отклонена.")
    if user is not None and query.message is not None:
        await _safe_notify_user(
            query.message.bot.send_message,
            user.telegram_user_id,
            f"Ваша заявка #{request_id} отклонена.",
        )


@router.callback_query(F.data.startswith("admin:req:alt_slot:"))
async def reject_with_alternative_start(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    request_id = _extract_request_id(query.data)
    await state.set_state(AdminFlowState.entering_alternative_slot)
    await state.update_data(admin_alt_request_id=request_id)
    await _safe_callback(query, "Введите альтернативный слот.")
    if query.message is not None:
        await _safe_answer(
            query.message,
            "Введите альтернативный слот в формате YYYY-MM-DD HH:MM-HH:MM",
        )


@router.message(AdminFlowState.entering_alternative_slot)
async def reject_with_alternative_finish(message: Message, state: FSMContext) -> None:
    if not await _admin_guard_for_message(message):
        return
    data = await state.get_data()
    request_id = int(data.get("admin_alt_request_id"))
    raw_slot = (message.text or "").strip()
    try:
        alternative_date, alternative_start_time, alternative_end_time = parse_alternative_slot(
            raw_slot
        )
    except ValueError as error:
        await _safe_answer(message, f"Некорректный формат: {error}")
        return

    session_factory = _require_session_factory()
    async with session_factory() as session:
        try:
            request = await reject_with_alternative_slot(
                session=session,
                request_id=request_id,
                admin_telegram_id=message.from_user.id,
                alternative_date=alternative_date,
                alternative_start_time=alternative_start_time,
                alternative_end_time=alternative_end_time,
            )
        except (LookupError, BusinessRuleViolation):
            await _safe_answer(message, "Нельзя предложить слот для этой заявки.")
            return
        user = await get_user_for_request(session, request)
        await session.commit()
    await state.clear()
    logger.info(
        "Admin offered alternative slot.",
        extra={
            "event": "admin_alternative_slot_proposed",
            "request_id": request_id,
            "telegram_user_id": message.from_user.id,
        },
    )
    await _safe_answer(
        message,
        (
            f"Заявка #{request_id} отклонена с причиной: {ALTERNATIVE_REJECTION_REASON}. "
            f"Альтернатива: {alternative_date.isoformat()} "
            f"{alternative_start_time.strftime('%H:%M')}-{alternative_end_time.strftime('%H:%M')}"
        ),
        reply_markup=admin_main_keyboard(),
    )
    if user is not None:
        await _safe_notify_user(
            message.bot.send_message,
            user.telegram_user_id,
            (
                f"Ваша заявка #{request_id} отклонена. "
                f"Альтернативный слот: {alternative_date.isoformat()} "
                f"{alternative_start_time.strftime('%H:%M')}-{alternative_end_time.strftime('%H:%M')}."
            ),
        )


@router.callback_query(F.data.startswith("admin:req:history:"))
async def show_request_history(query: CallbackQuery) -> None:
    if not await _admin_guard_for_callback(query):
        return
    request_id = _extract_request_id(query.data)
    session_factory = _require_session_factory()
    async with session_factory() as session:
        history = await get_request_history(session=session, request_id=request_id)
    await _safe_callback(query, "История загружена.")
    if query.message is not None:
        await _safe_answer(
            query.message,
            build_history_text(request_id=request_id, history_items=history),
        )


@router.callback_query(F.data.startswith("admin:req:block_user:"))
async def block_user_for_request(query: CallbackQuery) -> None:
    if not await _admin_guard_for_callback(query):
        return
    request_id = _extract_request_id(query.data)
    session_factory = _require_session_factory()
    async with session_factory() as session:
        try:
            user = await toggle_user_block(
                session=session,
                request_id=request_id,
                admin_telegram_id=query.from_user.id,
                blocked=True,
            )
        except LookupError:
            await _safe_callback(query, "Пользователь не найден.")
            return
        await session.commit()
    logger.info(
        "Admin blocked user.",
        extra={
            "event": "admin_user_blocked",
            "request_id": request_id,
            "target_user_id": user.id,
        },
    )
    await _safe_callback(query, "Пользователь заблокирован.")
    if query.message is not None:
        await _safe_answer(query.message, f"Пользователь #{user.id} заблокирован.")


@router.callback_query(F.data.startswith("admin:req:unblock_user:"))
async def unblock_user_for_request(query: CallbackQuery) -> None:
    if not await _admin_guard_for_callback(query):
        return
    request_id = _extract_request_id(query.data)
    session_factory = _require_session_factory()
    async with session_factory() as session:
        try:
            user = await toggle_user_block(
                session=session,
                request_id=request_id,
                admin_telegram_id=query.from_user.id,
                blocked=False,
            )
        except LookupError:
            await _safe_callback(query, "Пользователь не найден.")
            return
        await session.commit()
    await _safe_callback(query, "Пользователь разблокирован.")
    if query.message is not None:
        await _safe_answer(query.message, f"Пользователь #{user.id} разблокирован.")


@router.callback_query(F.data.startswith("admin:req:manual_create:"))
async def manual_create_for_request(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.clear()
    request_id = _extract_request_id(query.data)
    session_factory = _require_session_factory()
    async with session_factory() as session:
        try:
            request = await manual_create_meeting_for_user(
                session=session,
                request_id=request_id,
                admin_telegram_id=query.from_user.id,
            )
        except LookupError:
            await _safe_callback(query, "Заявка не найдена.")
            return
        user = await get_user_for_request(session, request)
        await session.commit()
    logger.info(
        "Admin created meeting manually.",
        extra={"event": "admin_manual_meeting_created", "request_id": request_id},
    )
    await _safe_callback(query, "Встреча создана вручную.")
    if query.message is not None:
        await _safe_answer(
            query.message,
            f"Встреча по заявке #{request_id} создана вручную.",
        )
    if user is not None and query.message is not None:
        await _safe_notify_user(
            query.message.bot.send_message,
            user.telegram_user_id,
            f"Ваша встреча по заявке #{request_id} создана администратором вручную.",
        )
