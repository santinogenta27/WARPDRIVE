"""Despliegue del chat web en Render (render.yaml, plan gratis): la configuración arranca la app
real, no lleva secretos escritos y define los límites de uso de la demo pública."""

from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from app.main import app

ROOT = Path(__file__).resolve().parents[2]
SECRETS = {"DATABASE_URL", "GEMINI_API_KEY", "GROQ_API_KEY", "ENCRYPTION_KEY"}


def _service() -> dict[str, Any]:
    blueprint = yaml.safe_load((ROOT / "render.yaml").read_text(encoding="utf-8"))
    services = blueprint["services"]
    assert len(services) == 1
    service: dict[str, Any] = services[0]
    return service


def test_el_blueprint_despliega_la_api_en_el_plan_gratis() -> None:
    service = _service()
    assert service["type"] == "web" and service["runtime"] == "python"
    assert service["plan"] == "free"
    assert "backend/requirements.txt" in service["buildCommand"]
    assert "uvicorn app.main:app" in service["startCommand"]
    assert "--port $PORT" in service["startCommand"] and "0.0.0.0" in service["startCommand"]
    health = TestClient(app).get(service["healthCheckPath"])
    assert health.status_code == 200


def test_los_secretos_se_cargan_en_render_y_no_en_el_repositorio() -> None:
    env = {var["key"]: var for var in _service()["envVars"]}
    for key in SECRETS:
        assert env[key] == {"key": key, "sync": False}, key  # se piden al crear el servicio
    assert env["PYTHONPATH"]["value"] == "backend"
    assert env["PYTHON_VERSION"]["value"].startswith("3.12.")


def test_el_chat_usa_el_modelo_de_groq_que_mejor_resolvio_los_evals() -> None:
    """gpt-oss-20b resolvió 10/10 casos de Acme (decisión 020); Gemini queda para embeddings y
    de respaldo si Groq se queda sin cuota."""
    env = {var["key"]: var.get("value") for var in _service()["envVars"]}
    assert env["LLM_PROVIDER"] == "groq"
    assert env["GROQ_AGENT_MODEL"] == env["GROQ_CLASSIFIER_MODEL"] == "openai/gpt-oss-20b"
    assert "GEMINI_API_KEY" in env  # embeddings


def test_la_demo_publica_tiene_limites_y_toma_la_ip_del_proxy() -> None:
    env = {var["key"]: var.get("value") for var in _service()["envVars"]}
    assert int(env["WEB_CHAT_IP_DAILY_LIMIT"]) > 0
    assert int(env["WEB_CHAT_DAILY_LIMIT"]) >= int(env["WEB_CHAT_IP_DAILY_LIMIT"])
    assert env["WEB_CHAT_CLIENT_IP"] != "direct"  # detrás del proxy de Render
