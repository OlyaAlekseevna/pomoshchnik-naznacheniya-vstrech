from aiogram.fsm.state import State, StatesGroup


class BookingFlowState(StatesGroup):
    choosing_consultation = State()
    choosing_duration = State()
    choosing_date = State()
    choosing_slot = State()
    entering_full_name = State()
    entering_phone = State()
    entering_email = State()
    entering_goal = State()
    confirming_consent = State()
    confirming_summary = State()
    editing_goal = State()


class AdminFlowState(StatesGroup):
    entering_alternative_slot = State()
    editing_setting_value = State()
    entering_manual_meeting = State()
