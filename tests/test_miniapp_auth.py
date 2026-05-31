import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

import pytest

from app.miniapp.auth import TelegramInitDataError, parse_and_validate_init_data


def _build_init_data(bot_token: str, auth_date: datetime, user: dict[str, object]) -> str:
    payload = {
        "query_id": "AAEFAKE",
        "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False),
        "auth_date": str(int(auth_date.timestamp())),
    }
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(payload.items()))
    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    payload["hash"] = hmac.new(
        secret_key,
        data_check_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return urlencode(payload)


def test_parse_and_validate_init_data_ok() -> None:
    bot_token = "123456:TEST_TOKEN"
    now = datetime.now(UTC)
    raw = _build_init_data(
        bot_token=bot_token,
        auth_date=now,
        user={
            "id": 9001,
            "username": "miniapp_user",
            "first_name": "Mini",
            "last_name": "App",
            "language_code": "ru",
        },
    )
    parsed = parse_and_validate_init_data(
        init_data=raw,
        bot_token=bot_token,
        max_age_seconds=3600,
        now=now + timedelta(seconds=5),
    )
    assert parsed.user.telegram_user_id == 9001
    assert parsed.user.username == "miniapp_user"


def test_parse_and_validate_init_data_rejects_old_payload() -> None:
    bot_token = "123456:TEST_TOKEN"
    now = datetime.now(UTC)
    raw = _build_init_data(
        bot_token=bot_token,
        auth_date=now - timedelta(hours=2),
        user={"id": 9002},
    )
    with pytest.raises(TelegramInitDataError):
        parse_and_validate_init_data(
            init_data=raw,
            bot_token=bot_token,
            max_age_seconds=60,
            now=now,
        )


def test_parse_and_validate_init_data_rejects_invalid_hash() -> None:
    bot_token = "123456:TEST_TOKEN"
    now = datetime.now(UTC)
    raw = _build_init_data(
        bot_token=bot_token,
        auth_date=now,
        user={"id": 9003},
    )
    broken = raw.replace("hash=", "hash=broken")
    with pytest.raises(TelegramInitDataError):
        parse_and_validate_init_data(
            init_data=broken,
            bot_token=bot_token,
            max_age_seconds=3600,
            now=now,
        )

