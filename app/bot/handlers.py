from __future__ import annotations

import logging
from datetime import UTC, date, datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import (
    BACK_TEXT,
    BOOK_TEXT,
    MY_REQUESTS_TEXT,
    back_keyboard,
    consent_keyboard,
    consultation_keyboard,
    dates_keyboard,
    duration_keyboard,
    main_menu_keyboard,
    request_actions_keyboard,
    slots_keyboard,
    summary_keyboard,
    week_title,
)
from app.bot.states import BookingFlowState
from app.bot.user_flow_service import (
    CONSULTATION_KIND,
    SlotChoice,
    build_user_requests_text,
    calculate_slots_for_date,
    can_submit_with_consent,
    cancel_request_for_user,
    create_request_from_draft,
    ensure_slot_still_available,
    get_schedule_settings_or_fail,
    get_user_requests,
    is_request_editable,
    is_valid_email,
    slot_rules_from_settings,
    update_request_goal_for_user,
)
from app.core.config import get_settings
from app.db.defaults import DEFAULT_USER_WITHOUT_INVITATION_TEXT
from app.db.repositories import get_or_create_user_by_telegram_id, get_user_by_telegram_id
from app.domain.exceptions import BusinessRuleViolation
from app.domain.lifecycle import BookingDraftState, update_draft_date, update_draft_duration
from app.domain.scheduling import build_week_window

router = Router(name="user-flow-router")
logger = logging.getLogger(__name__)

_session_factory: async_sessionmaker[AsyncSession] | None = None


def configure_session_factory(session_factory: async_sessionmaker[AsyncSession]) -> None:
    global _session_factory
    _session_factory = session_factory


def _require_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        raise RuntimeError("Session factory is not configured for bot handlers.")
    return _session_factory


def _extract_start_token(message_text: str) -> str | None:
    parts = message_text.split(maxsplit=1)
    if len(parts) == 2:
        return parts[1].strip()
    return None


def _with_event(data: dict[str, object], event: str) -> dict[str, object]:
    payload = {"event": event}
    payload.update(data)
    return payload


async def _safe_answer(message: Message, text: str, **kwargs) -> None:
    try:
        await message.answer(text, **kwargs)
    except TelegramAPIError:
        logger.exception(
            "Failed to send message to user.",
            extra=_with_event(
                {"telegram_user_id": message.from_user.id if message.from_user else None},
                "message_send_error",
            ),
        )


async def _safe_reply_callback(query: CallbackQuery, text: str) -> None:
    try:
        await query.answer(text)
    except TelegramAPIError:
        logger.exception(
            "Failed to answer callback query.",
            extra=_with_event(
                {"telegram_user_id": query.from_user.id if query.from_user else None},
                "message_send_error",
            ),
        )


def _draft_from_state(state_data: dict[str, object]) -> BookingDraftState:
    return BookingDraftState(
        duration_minutes=state_data.get("duration_minutes"),
        selected_date=(
            date.fromisoformat(state_data["selected_date"])
            if state_data.get("selected_date")
            else None
        ),
        slot_start_time=(
            datetime.fromisoformat(state_data["slot_start"]).timetz().replace(tzinfo=None)
            if state_data.get("slot_start")
            else None
        ),
        slot_end_time=(
            datetime.fromisoformat(state_data["slot_end"]).timetz().replace(tzinfo=None)
            if state_data.get("slot_end")
            else None
        ),
    )


async def _save_draft_to_state(state: FSMContext, draft: BookingDraftState) -> None:
    payload: dict[str, object] = {
        "duration_minutes": draft.duration_minutes,
        "selected_date": draft.selected_date.isoformat() if draft.selected_date else None,
    }
    if draft.slot_start_time is None or draft.slot_end_time is None:
        payload["slot_start"] = None
        payload["slot_end"] = None
    await state.update_data(**payload)


async def _show_dates_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    if data.get("duration_minutes") is None:
        await state.set_state(BookingFlowState.choosing_duration)
        await _safe_answer(
            message,
            "Choose consultation duration first.",
            reply_markup=duration_keyboard(),
        )
        return

    session_factory = _require_session_factory()
    async with session_factory() as session:
        settings = await get_schedule_settings_or_fail(session)
    week_offset = int(data.get("week_offset", 0))
    today = datetime.now(UTC).date()
    week = build_week_window(
        today=today,
        week_offset=week_offset,
        booking_horizon_days=settings.booking_horizon_days,
    )
    await state.set_state(BookingFlowState.choosing_date)
    await _safe_answer(
        message,
        f"{week_title(week.week_start, week.week_end)}\nChoose a date:",
        reply_markup=dates_keyboard(week=week, week_offset=week_offset),
    )


async def _show_slots_step(message: Message, state: FSMContext, meeting_date: date) -> None:
    data = await state.get_data()
    duration_minutes = int(data["duration_minutes"])
    session_factory = _require_session_factory()
    async with session_factory() as session:
        settings = await get_schedule_settings_or_fail(session)
        rules = slot_rules_from_settings(settings)
        slots = await calculate_slots_for_date(
            session=session,
            meeting_date=meeting_date,
            duration_minutes=duration_minutes,
            rules=rules,
        )
    await state.set_state(BookingFlowState.choosing_slot)
    await _safe_answer(
        message,
        "Choose a free slot:" if slots else "No free slots for this date. Choose another date.",
        reply_markup=slots_keyboard(slots) if slots else back_keyboard(),
    )


def _summary_text(data: dict[str, object]) -> str:
    return (
        "Please confirm your request:\n"
        f"Type: {data.get('consultation_kind', CONSULTATION_KIND)}\n"
        f"Duration: {data.get('duration_minutes')} min\n"
        f"Date: {data.get('selected_date')}\n"
        f"Slot: {data.get('slot_label')}\n"
        f"Name: {data.get('full_name')}\n"
        f"Phone: {data.get('phone')}\n"
        f"Email: {data.get('email')}\n"
        f"Goal: {data.get('meeting_goal')}"
    )


@router.message(CommandStart())
async def on_start(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return

    settings = get_settings()
    invite_token = (
        settings.telegram_invite_token
        if hasattr(settings, "telegram_invite_token")
        else None
    )
    provided_token = _extract_start_token(message.text or "")
    if invite_token and provided_token != invite_token:
        logger.info(
            "User opened bot without valid invitation token.",
            extra=_with_event(
                {"telegram_user_id": message.from_user.id},
                "user_without_invitation",
            ),
        )
        await state.clear()
        await _safe_answer(
            message,
            DEFAULT_USER_WITHOUT_INVITATION_TEXT,
            reply_markup=main_menu_keyboard(),
        )
        return

    session_factory = _require_session_factory()
    async with session_factory() as session:
        await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=message.from_user.id,
            invited_access_granted=True,
            first_name=message.from_user.first_name,
            last_name=message.from_user.last_name,
            username=message.from_user.username,
        )
        await session.commit()

    await state.clear()
    await _safe_answer(
        message,
        "Welcome! Use the menu below to create a booking request.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(F.text == BOOK_TEXT)
async def start_booking_flow(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    logger.info(
        "User started booking flow.",
        extra=_with_event({"telegram_user_id": message.from_user.id}, "user_booking_started"),
    )
    await state.clear()
    await state.update_data(
        week_offset=0,
        consultation_kind=CONSULTATION_KIND,
        consent_given=False,
    )
    await state.set_state(BookingFlowState.choosing_consultation)
    await _safe_answer(
        message,
        "Choose consultation type:",
        reply_markup=consultation_keyboard(),
    )


@router.message(F.text == MY_REQUESTS_TEXT)
async def show_user_history(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    session_factory = _require_session_factory()
    async with session_factory() as session:
        user = await get_user_by_telegram_id(session, message.from_user.id)
        if user is None:
            await _safe_answer(message, "No requests yet.")
            return
        requests = await get_user_requests(session, user.id)
    await state.clear()
    if not requests:
        await _safe_answer(message, "No requests yet.", reply_markup=main_menu_keyboard())
        return

    await _safe_answer(
        message,
        build_user_requests_text(requests),
        reply_markup=main_menu_keyboard(),
    )
    for item in requests:
        actions = request_actions_keyboard(item.id, editable=is_request_editable(item.status))
        if actions is not None:
            await _safe_answer(message, f"Actions for request #{item.id}", reply_markup=actions)


@router.callback_query(F.data == "consultation:select")
async def on_consultation_selected(query: CallbackQuery, state: FSMContext) -> None:
    await _safe_reply_callback(query, "Consultation selected.")
    await state.set_state(BookingFlowState.choosing_duration)
    await _safe_answer(
        query.message,
        "Choose duration:",
        reply_markup=duration_keyboard(),
    )


@router.callback_query(F.data.startswith("duration:"))
async def on_duration_selected(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        return
    duration = int(query.data.split(":", maxsplit=1)[1])
    data = await state.get_data()
    draft = _draft_from_state(data)
    previous_duration = draft.duration_minutes
    draft = update_draft_duration(draft, duration)
    await _save_draft_to_state(state, draft)
    await state.update_data(duration_minutes=duration, week_offset=0)

    logger.info(
        "User selected duration.",
        extra=_with_event(
            {"telegram_user_id": query.from_user.id, "duration_minutes": duration},
            "user_selected_duration",
        ),
    )
    if previous_duration is not None and previous_duration != duration:
        logger.info(
            "User changed previously selected value.",
            extra=_with_event(
                {"telegram_user_id": query.from_user.id, "field": "duration"},
                "user_changed_selection",
            ),
        )

    await _safe_reply_callback(query, "Duration selected.")
    await _show_dates_step(query.message, state)


@router.callback_query(F.data.startswith("week:"))
async def on_week_switched(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        return
    week_offset = int(query.data.split(":", maxsplit=1)[1])
    await state.update_data(week_offset=week_offset)
    logger.info(
        "User switched week.",
        extra=_with_event(
            {"telegram_user_id": query.from_user.id, "week_offset": week_offset},
            "user_selected_week",
        ),
    )
    await _safe_reply_callback(query, "Week updated.")
    await _show_dates_step(query.message, state)


@router.callback_query(F.data.startswith("date:"))
async def on_date_selected(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        return
    selected_date = date.fromisoformat(query.data.split(":", maxsplit=1)[1])
    data = await state.get_data()
    draft = _draft_from_state(data)
    previous_date = draft.selected_date
    draft = update_draft_date(draft, selected_date=selected_date)
    await _save_draft_to_state(state, draft)
    await state.update_data(selected_date=selected_date.isoformat())

    logger.info(
        "User selected date.",
        extra=_with_event(
            {"telegram_user_id": query.from_user.id, "selected_date": selected_date.isoformat()},
            "user_selected_date",
        ),
    )
    if previous_date is not None and previous_date != selected_date:
        logger.info(
            "User changed previously selected value.",
            extra=_with_event(
                {"telegram_user_id": query.from_user.id, "field": "date"},
                "user_changed_selection",
            ),
        )

    await _safe_reply_callback(query, "Date selected.")
    await _show_slots_step(query.message, state, selected_date)


@router.callback_query(F.data.startswith("slot:"))
async def on_slot_selected(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        return
    raw_slot = query.data.split(":", maxsplit=1)[1]
    slot = SlotChoice.decode(raw_slot)
    await state.update_data(
        slot_start=slot.start_at.isoformat(),
        slot_end=slot.end_at.isoformat(),
        slot_label=slot.label,
    )
    logger.info(
        "User selected slot.",
        extra=_with_event(
            {"telegram_user_id": query.from_user.id, "slot": slot.label},
            "user_selected_slot",
        ),
    )
    await _safe_reply_callback(query, "Slot selected.")
    await state.set_state(BookingFlowState.entering_full_name)
    await _safe_answer(
        query.message,
        "Enter your full name:",
        reply_markup=back_keyboard(),
    )


@router.message(BookingFlowState.entering_full_name, F.text.casefold() == BACK_TEXT.casefold())
async def back_from_name(message: Message, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    selected_date_raw = (await state.get_data())["selected_date"]
    await _show_slots_step(message, state, date.fromisoformat(selected_date_raw))


@router.message(BookingFlowState.entering_full_name)
async def on_full_name(message: Message, state: FSMContext) -> None:
    await state.update_data(full_name=(message.text or "").strip())
    await state.set_state(BookingFlowState.entering_phone)
    await _safe_answer(message, "Enter your phone number:", reply_markup=back_keyboard())


@router.message(BookingFlowState.entering_phone, F.text.casefold() == BACK_TEXT.casefold())
async def back_from_phone(message: Message, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await state.set_state(BookingFlowState.entering_full_name)
    await _safe_answer(message, "Enter your full name:", reply_markup=back_keyboard())


@router.message(BookingFlowState.entering_phone)
async def on_phone(message: Message, state: FSMContext) -> None:
    await state.update_data(phone=(message.text or "").strip())
    await state.set_state(BookingFlowState.entering_email)
    await _safe_answer(message, "Enter your email:", reply_markup=back_keyboard())


@router.message(BookingFlowState.entering_email, F.text.casefold() == BACK_TEXT.casefold())
async def back_from_email(message: Message, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await state.set_state(BookingFlowState.entering_phone)
    await _safe_answer(message, "Enter your phone number:", reply_markup=back_keyboard())


@router.message(BookingFlowState.entering_email)
async def on_email(message: Message, state: FSMContext) -> None:
    candidate = (message.text or "").strip()
    if not is_valid_email(candidate):
        logger.warning(
            "User entered invalid email.",
            extra=_with_event({"input": candidate}, "user_invalid_email"),
        )
        await _safe_answer(message, "Invalid email format. Please enter a valid email.")
        return
    await state.update_data(email=candidate)
    await state.set_state(BookingFlowState.entering_goal)
    await _safe_answer(message, "Describe the meeting goal:", reply_markup=back_keyboard())


@router.message(BookingFlowState.entering_goal, F.text.casefold() == BACK_TEXT.casefold())
async def back_from_goal(message: Message, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await state.set_state(BookingFlowState.entering_email)
    await _safe_answer(message, "Enter your email:", reply_markup=back_keyboard())


@router.message(BookingFlowState.entering_goal)
async def on_goal(message: Message, state: FSMContext) -> None:
    await state.update_data(meeting_goal=(message.text or "").strip())
    await state.set_state(BookingFlowState.confirming_consent)
    await _safe_answer(
        message,
        "Please confirm consent for personal data processing.",
        reply_markup=consent_keyboard(),
    )


@router.callback_query(F.data == "consent:yes")
async def on_consent_confirmed(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        return
    await state.update_data(consent_given=True)
    logger.info(
        "User confirmed consent.",
        extra=_with_event({"telegram_user_id": query.from_user.id}, "user_consent_confirmed"),
    )
    await _safe_reply_callback(query, "Consent saved.")
    data = await state.get_data()
    await state.set_state(BookingFlowState.confirming_summary)
    await _safe_answer(query.message, _summary_text(data), reply_markup=summary_keyboard())


@router.callback_query(F.data == "submit:request")
async def on_submit_request(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        return
    data = await state.get_data()
    if not can_submit_with_consent(bool(data.get("consent_given"))):
        await _safe_reply_callback(query, "Consent is required.")
        await _safe_answer(query.message, "You must confirm consent before submission.")
        return

    selected_slot = SlotChoice(
        start_at=datetime.fromisoformat(data["slot_start"]),
        end_at=datetime.fromisoformat(data["slot_end"]),
    )

    session_factory = _require_session_factory()
    async with session_factory() as session:
        user = await get_or_create_user_by_telegram_id(
            session=session,
            telegram_user_id=query.from_user.id,
            invited_access_granted=True,
            first_name=query.from_user.first_name,
            last_name=query.from_user.last_name,
            username=query.from_user.username,
        )
        settings = await get_schedule_settings_or_fail(session)
        rules = slot_rules_from_settings(settings)
        available_slots = await calculate_slots_for_date(
            session=session,
            meeting_date=selected_slot.start_at.date(),
            duration_minutes=int(data["duration_minutes"]),
            rules=rules,
        )
        try:
            ensure_slot_still_available(selected_slot, available_slots)
        except BusinessRuleViolation:
            await _safe_reply_callback(query, "Selected slot is not available now.")
            await _safe_answer(query.message, "Slot is no longer available. Choose another one.")
            await state.set_state(BookingFlowState.choosing_slot)
            await _show_slots_step(query.message, state, selected_slot.start_at.date())
            return

        request = await create_request_from_draft(
            session=session,
            user=user,
            full_name=data["full_name"],
            phone=data["phone"],
            email=data["email"],
            meeting_goal=data["meeting_goal"],
            duration_minutes=int(data["duration_minutes"]),
            slot_choice=selected_slot,
            personal_data_consent=True,
        )
        await session.commit()

    logger.info(
        "User submitted request.",
        extra=_with_event(
            {"telegram_user_id": query.from_user.id, "request_id": request.id},
            "user_request_submitted",
        ),
    )
    await _safe_reply_callback(query, "Request sent.")
    await state.clear()
    await _safe_answer(
        query.message,
        f"Request #{request.id} created and sent for approval.",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data.startswith("req_edit:"))
async def on_edit_request(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        return
    request_id = int(query.data.split(":", maxsplit=1)[1])
    await state.set_state(BookingFlowState.editing_goal)
    await state.update_data(editing_request_id=request_id)
    await _safe_reply_callback(query, "Send a new goal text.")
    await _safe_answer(
        query.message,
        f"Enter new goal for request #{request_id}:",
        reply_markup=back_keyboard(),
    )


@router.callback_query(F.data.startswith("req_cancel:"))
async def on_cancel_request(query: CallbackQuery, state: FSMContext) -> None:
    if query.from_user is None:
        return
    request_id = int(query.data.split(":", maxsplit=1)[1])
    session_factory = _require_session_factory()
    async with session_factory() as session:
        user = await get_user_by_telegram_id(session, query.from_user.id)
        if user is None:
            await _safe_answer(query.message, "User is not registered.")
            return
        try:
            canceled_request = await cancel_request_for_user(
                session=session,
                request_id=request_id,
                user_id=user.id,
                telegram_user_id=query.from_user.id,
            )
        except (LookupError, BusinessRuleViolation):
            await _safe_reply_callback(query, "Request cannot be canceled.")
            await _safe_answer(query.message, "This request cannot be canceled in current status.")
            return
        await session.commit()

    await state.clear()
    await _safe_reply_callback(query, "Request canceled.")
    await _safe_answer(
        query.message,
        f"Request #{canceled_request.id} canceled.",
        reply_markup=main_menu_keyboard(),
    )


@router.message(BookingFlowState.editing_goal, F.text.casefold() == BACK_TEXT.casefold())
async def back_from_edit_goal(message: Message, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await state.clear()
    await _safe_answer(message, "Back to menu.", reply_markup=main_menu_keyboard())


@router.message(BookingFlowState.editing_goal)
async def on_new_goal_for_request(message: Message, state: FSMContext) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    request_id = int(data["editing_request_id"])
    session_factory = _require_session_factory()
    async with session_factory() as session:
        user = await get_user_by_telegram_id(session, message.from_user.id)
        if user is None:
            await _safe_answer(message, "User is not registered.")
            return
        try:
            updated_request = await update_request_goal_for_user(
                session=session,
                request_id=request_id,
                user_id=user.id,
                telegram_user_id=message.from_user.id,
                new_goal=(message.text or "").strip(),
            )
        except (LookupError, BusinessRuleViolation):
            await _safe_answer(message, "This request cannot be edited in current status.")
            return
        await session.commit()
    await state.clear()
    await _safe_answer(
        message,
        f"Request #{updated_request.id} updated.",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "nav:to_menu")
async def nav_to_menu(query: CallbackQuery, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await state.clear()
    await _safe_reply_callback(query, "Back to menu.")
    await _safe_answer(query.message, "Main menu.", reply_markup=main_menu_keyboard())


@router.callback_query(F.data == "nav:to_consultation")
async def nav_to_consultation(query: CallbackQuery, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await state.set_state(BookingFlowState.choosing_consultation)
    await _safe_reply_callback(query, "Back.")
    await _safe_answer(
        query.message,
        "Choose consultation type:",
        reply_markup=consultation_keyboard(),
    )


@router.callback_query(F.data == "nav:to_duration")
async def nav_to_duration(query: CallbackQuery, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await state.set_state(BookingFlowState.choosing_duration)
    await _safe_reply_callback(query, "Back.")
    await _safe_answer(query.message, "Choose duration:", reply_markup=duration_keyboard())


@router.callback_query(F.data == "nav:to_date")
async def nav_to_date(query: CallbackQuery, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await _safe_reply_callback(query, "Back.")
    await _show_dates_step(query.message, state)


@router.callback_query(F.data == "nav:to_goal")
async def nav_to_goal(query: CallbackQuery, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await state.set_state(BookingFlowState.entering_goal)
    await _safe_reply_callback(query, "Back.")
    await _safe_answer(query.message, "Describe the meeting goal:", reply_markup=back_keyboard())


@router.callback_query(F.data == "nav:to_consent")
async def nav_to_consent(query: CallbackQuery, state: FSMContext) -> None:
    logger.info("User pressed back.", extra=_with_event({}, "user_pressed_back"))
    await state.set_state(BookingFlowState.confirming_consent)
    await _safe_reply_callback(query, "Back.")
    await _safe_answer(
        query.message,
        "Please confirm consent for personal data processing.",
        reply_markup=consent_keyboard(),
    )
