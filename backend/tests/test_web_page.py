"""Página del chat web de la demo: Acme Store, con un aviso discreto de que es una demo.

La página es un solo archivo, sin scripts ni estilos externos, y usa las rutas del canal web
(test_web_chat.py prueba el comportamiento).
"""

import re

from fastapi.testclient import TestClient

from app.main import app


def test_la_pagina_del_chat_es_la_de_acme_store_y_avisa_que_es_una_demo() -> None:
    client = TestClient(app)
    for path in ("/", "/chat"):
        response = client.get(path)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        html = response.text
        assert "<title>Acme Store" in html
        assert "Demo de Enterprise Data AI" in html
        assert 'name="viewport"' in html  # se usa desde el celular
        assert "/chat/session" in html and "/chat/messages" in html


def test_la_pagina_no_carga_recursos_externos() -> None:
    html = TestClient(app).get("/").text
    assert not re.search(r"<script[^>]+src=", html)
    assert not re.search(r"<link[^>]+stylesheet", html)
