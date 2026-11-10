"""Login del panel (decisión 022): firma de la sesión, vencimiento y configuración desde .env."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.panel import auth
from app.panel.html import local_time


def test_las_horas_del_panel_se_muestran_en_hora_argentina() -> None:
    """La base guarda en UTC; el equipo de Acme lee la hora de Buenos Aires (UTC-3)."""
    assert local_time(datetime(2026, 11, 9, 21, 49, tzinfo=UTC)) == "09/11 18:49"
    assert local_time("2026-11-10T02:05:30+00:00", "%d/%m %H:%M:%S") == "09/11 23:05:30"


def test_el_login_lee_la_configuracion_del_env_aunque_no_este_en_el_entorno(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """En local las claves están solo en .env: el login no depende de que otra ruta lo cargue."""
    for key in ("PANEL_EMAIL", "PANEL_PASSWORD", "PANEL_SECRET", "ENCRYPTION_KEY"):
        monkeypatch.setenv(key, "x")  # al terminar, el entorno vuelve a como estaba
        monkeypatch.delenv(key)
    env = tmp_path / ".env"
    env.write_text(
        "PANEL_EMAIL=demo@acme.example\nPANEL_PASSWORD=clave-local\nENCRYPTION_KEY=k\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(auth, "ENV_FILE", env)
    assert auth.configured()
    assert auth.check_credentials("Demo@Acme.example ", "clave-local")
    assert not auth.check_credentials("demo@acme.example", "otra")
    assert auth.read_session(auth.new_session("demo@acme.example")) == "demo@acme.example"


def test_una_sesion_adulterada_o_vencida_no_vale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PANEL_SECRET", "secreto-de-test")
    token = auth.new_session("demo@acme.example", now=1000.0)
    assert auth.read_session(token, now=1001.0) == "demo@acme.example"
    assert auth.read_session(token, now=1000.0 + auth.SESSION_SECONDS + 1) is None
    payload, signature = token.rsplit(".", 1)
    assert auth.read_session(f"{payload}.{'0' * len(signature)}", now=1001.0) is None
    assert auth.valid_csrf(token, auth.csrf_token(token))
    assert not auth.valid_csrf(token, "x")
