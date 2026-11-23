"""Hito 13, tarea 5 — test de aceptación: Agent, configurar sin miedo (decisión 087).

- Cada opción de cada pestaña (Rules, Actions, Topics, Brand & voice, Sales, Language) tiene
  una línea de ayuda y un ejemplo, y ningún control queda suelto, sin su explicación.
- La marca y la voz comparten una vista previa del chat real que se actualiza al escribir, con
  una respuesta de ejemplo armada con la voz.
- Guardar muestra un aviso de "saved" que se va solo.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import build_simulated_stack
from test_panel_ingles import _harbor_data

from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

OWNER, PASSWORD = "maya@harborsupply.example", "clave-de-harbor-supply"
TABS = ["agent", "agent/actions", "agent/topics", "agent/voice", "agent/sales", "agent/language"]
CONTROL = re.compile(r"<(input|select|textarea)\b([^>]*)>", re.S)


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[uuid.UUID]:
    monkeypatch.setattr(auth, "DEFAULT_LANGUAGE", "en")
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Harbor Supply", OWNER, PASSWORD)
    _harbor_data(conn, stack, company.tenant_id)
    with tenant_scope(conn, company.tenant_id):
        conn.execute("update case_types set active = true")
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield company.tenant_id
    finally:
        app.dependency_overrides.clear()


def login() -> TestClient:
    client = TestClient(app)
    assert client.post("/panel/login", data={"email": OWNER, "password": PASSWORD}).is_success
    return client


def fields(page: str) -> list[str]:
    return re.findall(r'<div class="field[^"]*">.*?(?=<div class="field|<div class="formbar|'
                      r"</form>|</details>|$)", page, re.S)  # fmt: skip


def main_of(page: str) -> str:
    found = re.search(r'<main id="main">(.*)</main>', page, re.S)
    assert found is not None
    return found.group(1)


@pytest.mark.parametrize("tab", TABS)
def test_cada_opcion_tiene_ayuda_y_ejemplo(world: uuid.UUID, tab: str) -> None:
    page = main_of(login().get(f"/panel/{tab}").text)
    blocks = fields(page)
    assert blocks, tab
    for block in blocks:
        label = re.search(r"<label[^>]*>(.*?)</label>", block, re.S)
        name = label.group(1) if label else block[:80]
        assert '<p class="help">' in block, (tab, name)
        assert '<p class="example"><span>Example:</span>' in block, (tab, name)
    # Ningún control queda fuera de una opción explicada (salvo los ocultos y los botones).
    inside = "".join(blocks)
    for kind, attrs in CONTROL.findall(page):
        if 'type="hidden"' in attrs or 'name="pick"' in attrs:
            continue
        assert attrs in inside, (tab, kind, attrs[:80])


def test_la_vista_previa_sigue_a_la_marca_y_a_la_voz(world: uuid.UUID) -> None:
    page = login().get("/panel/agent/voice").text
    assert 'id="widget-form"' in page and 'id="voice-form"' in page
    preview = re.search(r'<div class="card preview" id="brand-preview".*?</script>', page, re.S)
    assert preview is not None
    assert 'data-mode="inline"' in page and 'data-target="#widget-preview"' in page
    # El script escucha los dos formularios y arma una respuesta de ejemplo con la voz.
    assert 'getElementById(\n"voice-form")' in page or '"voice-form"' in page
    assert "sample:sample()" in page and 'val(v,"firma")' in page and "emojis" in page
    # La respuesta de ejemplo se muestra en el chat de la vista previa.
    widget_js = TestClient(app).get("/widget.js").text
    assert "cfg.sample" in widget_js


def test_guardar_muestra_saved(world: uuid.UUID) -> None:
    client = login()
    page = client.get("/panel/agent/voice").text
    token = re.search(r'name="csrf" value="([^"]+)"', page)
    assert token is not None
    saved = client.post(
        "/panel/configuracion/voz",
        data={"csrf": token.group(1), "tono": "Warm and short", "firma": "— Harbor"},
    )
    toast = re.search(r'<div class="toast" role="status">(.*?)</div>', saved.text, re.S)
    assert toast is not None and "saved" in toast.group(1).lower()
    assert 'value="Warm and short"' in saved.text
