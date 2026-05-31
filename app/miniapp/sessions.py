from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_urlsafe


@dataclass(frozen=True)
class MiniAppSession:
    token: str
    telegram_user_id: int
    role: str
    created_at: datetime
    expires_at: datetime


class InMemoryMiniAppSessionStore:
    """Temporary in-memory storage for Mini App sessions."""

    def __init__(self) -> None:
        self._sessions: dict[str, MiniAppSession] = {}

    def create(self, telegram_user_id: int, role: str, ttl_minutes: int) -> MiniAppSession:
        now = datetime.now(UTC)
        ttl = max(1, ttl_minutes)
        token = token_urlsafe(48)
        session = MiniAppSession(
            token=token,
            telegram_user_id=telegram_user_id,
            role=role,
            created_at=now,
            expires_at=now + timedelta(minutes=ttl),
        )
        self._sessions[token] = session
        self._cleanup_expired(now)
        return session

    def get(self, token: str) -> MiniAppSession | None:
        now = datetime.now(UTC)
        session = self._sessions.get(token)
        if session is None:
            return None
        if session.expires_at <= now:
            self._sessions.pop(token, None)
            return None
        self._cleanup_expired(now)
        return session

    def delete(self, token: str) -> None:
        self._sessions.pop(token, None)

    def _cleanup_expired(self, now: datetime) -> None:
        expired_tokens = [
            token for token, session in self._sessions.items() if session.expires_at <= now
        ]
        for token in expired_tokens:
            self._sessions.pop(token, None)
