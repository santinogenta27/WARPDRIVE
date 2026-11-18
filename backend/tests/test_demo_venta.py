"""Hito 10, punto 7 — test de aceptación: el paso de venta en la demo de Northwind
(decisión 065).

El cliente pregunta por una campera, el agente recomienda una con stock y le manda el link de
compra. En la demo, el link lleva a la página de carrito de la propia demo
(`/demo/{slug}/cart/...`), que permite una compra simulada (sin cobrar). Esa compra queda como
venta atribuida al agente.
"""

import re
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack
from test_northwind import _without_the_demo_northwind

from app.channels.web.routes import WebDeps, web_deps
from app.demo import northwind
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.main import app
from app.sales import attribution

SLUG = northwind.company()["chat_slug"]


def test_la_sugerencia_de_venta_esta_en_el_widget() -> None:
    assert "Do you have a rain jacket in size M?" in northwind.company()["widget"]["suggestions"]


def test_en_la_demo_el_link_lleva_al_carrito_de_la_demo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://demo.example/")
    assert northwind.demo_checkout_base() == f"https://demo.example/demo/{SLUG}"
    api = northwind.sandbox_client(checkout_base=northwind.demo_checkout_base())
    made = api.post("/checkouts", json={"items": [{"sku": "NW-TRJ-BLK-M", "quantity": 1}]})
    assert made.json()["url"] == f"https://demo.example/demo/{SLUG}/cart/NW-TRJ-BLK-M:1"


@pytest.fixture
def web(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[TestClient, SimulatedStack, Any]]:
    """La demo de Northwind por su chat web, con la API simulada del agente compartida con la
    página de carrito (como en la demo)."""
    import httpx
    from simulated.voyage import voyage_transport

    stack = build_simulated_stack(conn, company="northwind")
    monkeypatch.setattr(northwind, "shared_northwind", lambda: stack.acme)
    embedder = VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )
    _without_the_demo_northwind(conn)
    setup = northwind.ensure_northwind(conn, embedder, password="clave-de-northwind-demo")
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent
    )
    try:
        yield TestClient(app), stack, setup.tenant_id
    finally:
        app.dependency_overrides.clear()


def _chat(web: TestClient, session: str, text: str) -> str:
    response = web.post("/chat/messages", json={"session": session, "text": text})
    assert response.status_code == 200, response.text
    return response.text


def test_la_campera_el_link_y_la_compra_simulada(web: Any) -> None:
    client, stack, tenant = web
    session = client.post(f"/chat/session?canal={SLUG}").json()["session"]
    first = _chat(client, session, "Do you have a rain jacket in size M?")
    assert "$149" in first and "Black" in first
    text = _chat(client, session, "Great, I'll take the black one")
    url = next(u for u in re.findall(r"https?://[^\s\"\\]+", text) if "/cart/" in u)
    assert "/cart/NW-TRJ-BLK-M:1?" in url
    ref = re.search(r"ref=(ea-[0-9a-f]+)", url)
    assert ref is not None

    page = client.get(f"/demo/{SLUG}/cart/NW-TRJ-BLK-M:1", params={"ref": ref.group(1)})
    assert page.status_code == 200, page.text
    assert "Trail Runner Jacket" in page.text and "$149.00" in page.text
    assert "nothing is charged" in page.text
    done = client.post(f"/demo/{SLUG}/cart/complete", data={"ref": ref.group(1)})
    assert done.status_code == 200 and "Thank you" in done.text
    assert attribution.sync(stack.conn, tenant, stack.agent._guardian) == 1
    [total] = attribution.totals(stack.conn, tenant)
    assert (total.sales, float(total.amount)) == (1, 149.0)


def test_un_carrito_de_un_producto_que_no_existe_da_404(web: Any) -> None:
    client, _, _ = web
    assert client.get(f"/demo/{SLUG}/cart/NW-XXX:1").status_code == 404
