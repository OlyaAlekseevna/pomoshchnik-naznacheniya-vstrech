from fastapi.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def test_health_ok_when_external_checks_are_skipped(monkeypatch) -> None:
    monkeypatch.setenv("APP_SKIP_EXTERNAL_CHECKS", "true")
    get_settings.cache_clear()

    app = create_app()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["checks"]["postgresql"] == "skipped"
    assert payload["checks"]["redis"] == "skipped"
