"""Infrastructure/health tests for the current placeholder API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routes import health as health_module
from app.config import Settings
from app.main import create_app


def test_health_without_database(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "error"
    assert body["timestamp"].endswith("Z") or "+" in body["timestamp"]


def test_health_with_database_ok(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> None:
    """The route reports ``ok`` when the probe succeeds; the probe itself is mocked."""
    monkeypatch.setattr(health_module, "check_database", lambda url: url == "postgresql://x")
    with TestClient(create_app(Settings(database_url="postgresql://x", frontend_dir="none"))) as c:
        assert c.get("/health").json()["database"] == "ok"


def test_legacy_api_health_path_is_gone(client: TestClient) -> None:
    """Healthcheck path is /health; /api/health was a defect in the old prod compose."""
    assert client.get("/api/health").status_code == 404


def test_error_shape_matches_legacy_contract(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert set(body) == {"detail", "status_code"}
    assert body["status_code"] == 404
