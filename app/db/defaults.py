DEFAULT_WORKING_DAYS = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

DEFAULT_AVAILABLE_DURATIONS_MINUTES = [15, 30, 45, 90]

DEFAULT_NOTIFICATION_TEMPLATES = {
    "new_request_admin": "Новая заявка на консультацию.",
    "request_approved_user": "Ваша заявка согласована.",
    "request_rejected_user": "Ваша заявка отклонена.",
}

DEFAULT_USER_WITHOUT_INVITATION_TEXT = (
    "Запись доступна только по персональной ссылке-приглашению. "
    "Если вы получили ссылку от владельца календаря, откройте ее из сообщения-приглашения "
    "или попросите отправить ссылку повторно."
)
