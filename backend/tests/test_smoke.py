"""Tarea 1 — test de aceptación: el monorepo arranca y el CI corre pytest."""

from fastapi.testclient import TestClient

from app.main import app


def test_health_responde_ok() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
