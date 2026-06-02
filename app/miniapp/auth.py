from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qsl


class TelegramInitDataError(ValueError):
    """Raised when Telegram WebApp initData is invalid."""


@dataclass(frozen=True)
class TelegramWebAppUser:
    telegram_user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None
    language_code: str | None


@dataclass(frozen=True)
class TelegramInitDataPayload:
    user: TelegramWebAppUser
    auth_date: datetime
    query_id: str | None


def parse_and_validate_init_data(
    init_data: str,
    bot_token: str,
    max_age_seconds: int,
    now: datetime | None = None,
) -> TelegramInitDataPayload:
    if not init_data.strip():
        raise TelegramInitDataError("initData is empty.")

    pairs = parse_qsl(init_data, keep_blank_values=True)
    values = dict(pairs)
    incoming_hash = values.get("hash")
    if incoming_hash is None:
        raise TelegramInitDataError("hash is missing.")

    data_lines = [f"{key}={value}" for key, value in pairs if key != "hash"]
    data_check_string = "\n".join(sorted(data_lines))

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(calculated_hash, incoming_hash):
        raise TelegramInitDataError("Invalid initData hash.")

    auth_date_raw = values.get("auth_date")
    if auth_date_raw is None:
        raise TelegramInitDataError("auth_date is missing.")
    try:
        auth_ts = int(auth_date_raw)
    except ValueError as error:
        raise TelegramInitDataError("auth_date must be an integer timestamp.") from error

    auth_date = datetime.fromtimestamp(auth_ts, tz=UTC)
    now_value = now or datetime.now(UTC)
    age_seconds = (now_value - auth_date).total_seconds()
    if age_seconds < 0:
        raise TelegramInitDataError("auth_date is in the future.")
    if age_seconds > max(1, max_age_seconds):
        raise TelegramInitDataError("initData is too old.")

    user_raw = values.get("user")
    if user_raw is None:
        raise TelegramInitDataError("user is missing.")
    try:
        user_payload = json.loads(user_raw)
    except json.JSONDecodeError as error:
        raise TelegramInitDataError("user payload JSON is invalid.") from error

    user_id = user_payload.get("id")
    if not isinstance(user_id, int):
        raise TelegramInitDataError("user.id is missing or invalid.")

    user = TelegramWebAppUser(
        telegram_user_id=user_id,
        username=user_payload.get("username"),
        first_name=user_payload.get("first_name"),
        last_name=user_payload.get("last_name"),
        language_code=user_payload.get("language_code"),
    )
    return TelegramInitDataPayload(
        user=user,
        auth_date=auth_date,
        query_id=values.get("query_id"),
    )

