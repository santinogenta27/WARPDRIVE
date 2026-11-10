"""Login del panel (decisión 022): firma de la sesión, vencimiento y configuración desde .env."""

from pathlib import Path

import pytest

from app.panel import auth


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
