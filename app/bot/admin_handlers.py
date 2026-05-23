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
    apply_setting_update,
    approve_request,
    build_history_text,
    build_request_card,
    build_settings_summary,
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
from app.db.repositories import get_schedule_settings
from app.domain.exceptions import BusinessRuleViolation

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
    await _safe_answer(message, "Access denied: admin only.")
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
    await _safe_callback(query, "Access denied")
    if query.message is not None:
        await _safe_answer(query.message, "Access denied: admin only.")
    return False


def _extract_request_id(raw_callback_data: str) -> int:
    return int(raw_callback_data.rsplit(":", maxsplit=1)[1])


def _setting_prompt(setting_key: str) -> str:
    prompts = {
        "working_days": "Enter weekdays as comma-separated values: monday,tuesday,...",
        "working_hours": "Enter working hours in format HH:MM-HH:MM",
        "durations": "Enter durations in minutes, comma-separated. Example: 15,30,45,90",
        "min_notice": "Enter min notice in minutes. Example: 120",
        "buffer": "Enter buffer in minutes. Example: 60",
        "daily_limit": "Enter daily consultations limit. Example: 3",
        "horizon": "Enter booking horizon in days. Example: 28",
        "forbidden_date": (
            "Enter forbidden date. Optional reason via '|'. "
            "Example: 2026-06-01|holiday"
        ),
        "forbidden_period": (
            "Enter forbidden period. Optional reason via '|'. "
            "Example: 2026-06-01 10:00 - 2026-06-01 14:00|maintenance"
        ),
        "new_request_text": "Enter new text template for admin new-request notification.",
    }
    return prompts.get(setting_key, "Enter value:")


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
    await _safe_answer(message, "Admin panel:", reply_markup=admin_main_keyboard())


@router.callback_query(F.data == "admin:menu")
async def admin_menu(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.clear()
    await _safe_callback(query, "Opened admin menu.")
    if query.message is not None:
        await _safe_answer(query.message, "Admin panel:", reply_markup=admin_main_keyboard())


@router.callback_query(F.data == "admin:req:list")
async def list_requests(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.clear()
    session_factory = _require_session_factory()
    async with session_factory() as session:
        requests = await get_requests_for_admin(session=session, limit=20)
        if not requests:
            await _safe_callback(query, "No requests.")
            if query.message is not None:
                await _safe_answer(query.message, "No requests found.")
            return
        await _safe_callback(query, "Requests loaded.")
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
    await _safe_callback(query, "Settings loaded.")
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
    await _safe_callback(query, "Send new value.")
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
        await _safe_answer(message, "No setting selected. Open /admin again.")
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
            await _safe_answer(message, f"Invalid value: {error}")
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


@router.callback_query(F.data.startswith("admin:req:approve:"))
async def approve_request_action(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    await state.clear()
    request_id = _extract_request_id(query.data)
    session_factory = _require_session_factory()
    async with session_factory() as session:
        try:
            request = await approve_request(
                session=session,
                request_id=request_id,
                admin_telegram_id=query.from_user.id,
            )
        except (LookupError, BusinessRuleViolation):
            await _safe_callback(query, "Cannot approve this request.")
            return
        user = await get_user_for_request(session, request)
        await session.commit()
    logger.info(
        "Admin approved request.",
        extra={
            "event": "admin_request_approved",
            "request_id": request_id,
            "telegram_user_id": query.from_user.id,
        },
    )
    await _safe_callback(query, "Request approved.")
    if query.message is not None:
        await _safe_answer(query.message, f"Request #{request_id} approved.")
    if user is not None and query.message is not None:
        await _safe_notify_user(
            query.message.bot.send_message,
            user.telegram_user_id,
            f"Your request #{request_id} was approved.",
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
                reason="Rejected by admin",
            )
        except (LookupError, BusinessRuleViolation):
            await _safe_callback(query, "Cannot reject this request.")
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
    await _safe_callback(query, "Request rejected.")
    if query.message is not None:
        await _safe_answer(query.message, f"Request #{request_id} rejected.")
    if user is not None and query.message is not None:
        await _safe_notify_user(
            query.message.bot.send_message,
            user.telegram_user_id,
            f"Your request #{request_id} was rejected.",
        )


@router.callback_query(F.data.startswith("admin:req:alt_slot:"))
async def reject_with_alternative_start(query: CallbackQuery, state: FSMContext) -> None:
    if not await _admin_guard_for_callback(query):
        return
    request_id = _extract_request_id(query.data)
    await state.set_state(AdminFlowState.entering_alternative_slot)
    await state.update_data(admin_alt_request_id=request_id)
    await _safe_callback(query, "Send alternative slot.")
    if query.message is not None:
        await _safe_answer(
            query.message,
            "Enter alternative slot in format YYYY-MM-DD HH:MM-HH:MM",
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
        await _safe_answer(message, f"Invalid format: {error}")
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
            await _safe_answer(message, "Cannot set alternative slot for this request.")
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
            f"Request #{request_id} rejected with reason: {ALTERNATIVE_REJECTION_REASON}. "
            f"Alternative: {alternative_date.isoformat()} "
            f"{alternative_start_time.strftime('%H:%M')}-{alternative_end_time.strftime('%H:%M')}"
        ),
        reply_markup=admin_main_keyboard(),
    )
    if user is not None:
        await _safe_notify_user(
            message.bot.send_message,
            user.telegram_user_id,
            (
                f"Your request #{request_id} was rejected. "
                f"Alternative slot: {alternative_date.isoformat()} "
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
    await _safe_callback(query, "History loaded.")
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
            await _safe_callback(query, "User not found.")
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
    await _safe_callback(query, "User blocked.")
    if query.message is not None:
        await _safe_answer(query.message, f"User #{user.id} blocked.")


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
            await _safe_callback(query, "User not found.")
            return
        await session.commit()
    await _safe_callback(query, "User unblocked.")
    if query.message is not None:
        await _safe_answer(query.message, f"User #{user.id} unblocked.")


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
            await _safe_callback(query, "Request not found.")
            return
        user = await get_user_for_request(session, request)
        await session.commit()
    logger.info(
        "Admin created meeting manually.",
        extra={"event": "admin_manual_meeting_created", "request_id": request_id},
    )
    await _safe_callback(query, "Meeting created manually.")
    if query.message is not None:
        await _safe_answer(query.message, f"Manual meeting created for request #{request_id}.")
    if user is not None and query.message is not None:
        await _safe_notify_user(
            query.message.bot.send_message,
            user.telegram_user_id,
            f"Your meeting for request #{request_id} was created manually by admin.",
        )
