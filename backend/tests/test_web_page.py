"""Página del chat web de la demo: Acme Store, con un aviso discreto de que es una demo.

La página es un marco que carga el widget del propio servidor (`/widget.js`, decisión 031), sin
scripts ni estilos de otros servidores. test_web_chat.py y test_widget.py prueban el
comportamiento.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import build_simulated_stack

from app.channels.web.routes import WebDeps, web_deps
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.main import app


@pytest.fixture
def client(conn: psycopg.Connection) -> Iterator[TestClient]:
    stack = build_simulated_stack(conn)
    address = f"web:demo-{uuid.uuid4().hex[:8]}"
    register_whatsapp_channel(conn, stack.seed.tenant_id, phone_number_id=address, kind="web")
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent, address=address
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_la_pagina_del_chat_es_la_de_acme_store_y_avisa_que_es_una_demo(
    client: TestClient,
) -> None:
    for path in ("/", "/chat"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        html = response.text
        assert "<title>Acme Store" in html
        assert "Demo · Acme Store es una tienda ficticia" in html
        assert 'name="viewport"' in html  # se usa desde el celular
    script = client.get("/widget.js").text
    assert "/chat/session" in script and "/chat/messages" in script


def test_la_pagina_no_carga_recursos_externos(client: TestClient) -> None:
    html = client.get("/").text
    assert re.findall(r"<script[^>]+src=\"([^\"]+)\"", html) == ["/widget.js"]  # el propio
    assert not re.search(r"<link[^>]+stylesheet", html)
    assert not re.search(r"(?:src|href)=\"https?://", html)


def test_sin_el_chat_de_la_demo_configurado_la_pagina_igual_carga(
    conn: psycopg.Connection,
) -> None:
    """En una base sin `make demo-setup` la página responde: el widget avisa que no hay chat."""
    app.dependency_overrides[web_deps] = (
        lambda: WebDeps(
            connection=lambda: nullcontext(conn),
            agent=lambda c: None,  # type: ignore[arg-type, return-value]
            address=f"web:no-existe-{uuid.uuid4().hex[:8]}",
        )
    )
    try:
        client = TestClient(app)
        assert client.get("/").status_code == 200
        assert client.get("/chat/widget/config").status_code == 404
    finally:
        app.dependency_overrides.clear()
