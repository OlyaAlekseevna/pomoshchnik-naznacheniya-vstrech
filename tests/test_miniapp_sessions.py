from datetime import UTC, datetime, timedelta

from app.miniapp.sessions import InMemoryMiniAppSessionStore


def test_session_store_create_get_delete() -> None:
    store = InMemoryMiniAppSessionStore()
    session = store.create(telegram_user_id=9001, role="user", ttl_minutes=10)
    loaded = store.get(session.token)
    assert loaded is not None
    assert loaded.telegram_user_id == 9001
    assert loaded.role == "user"

    store.delete(session.token)
    assert store.get(session.token) is None


def test_session_store_expires_session() -> None:
    store = InMemoryMiniAppSessionStore()
    session = store.create(telegram_user_id=9002, role="admin", ttl_minutes=1)
    store._sessions[session.token] = session.__class__(
        token=session.token,
        telegram_user_id=session.telegram_user_id,
        role=session.role,
        created_at=session.created_at - timedelta(minutes=2),
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    assert store.get(session.token) is None

