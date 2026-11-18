"""Hito 8, tarea 3 — test de aceptación de la empresa de demo en inglés (decisión 038).

Northwind Outfitters es una tienda ficticia de ropa con su propio tenant en inglés:
- sus documentos de políticas (envíos, devoluciones, cambios, garantía, descuentos) y su catálogo;
- clientes de prueba y pedidos: uno atrasado, uno con monto alto para reembolso, uno para cambio
  de talle y uno ya despachado;
- una API simulada con su OpenAPI.

Un comando la crea o la regenera (`make demo-setup`), sin duplicar nada.
"""

import json
import re
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import psycopg
import pytest
import runner
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator
from simulated.stack import build_simulated_stack

from app import i18n
from app.channels.web.routes import WebDeps, web_deps
from app.companies.agent import build_guardian
from app.companies.connection import mode_for
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.demo import northwind
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps
from app.policy.guardian import ContactContext
from app.tools.manifest import MANIFEST_SCHEMA
from northwind_api import OPENAPI_PATH, canonical, create_app

SPEC: dict[str, Any] = json.loads(Path(OPENAPI_PATH).read_text(encoding="utf-8"))
ADDRESS = {"address": "500 Pine St, Seattle, WA 98101"}
SIZE_L = {"item_sku": "NW-MCS-NVY-M", "new_size": "L"}

# (método, ruta del contrato, ruta concreta, cuerpo, headers, código esperado)
EXAMPLES: list[tuple[str, str, str, dict[str, Any] | None, dict[str, str], int]] = [
    ("get", "/orders", "/orders?customer_phone=%2B15555550101", None, {}, 200),
    ("get", "/customers", "/customers?email=emily%40example.com", None, {}, 200),
    ("get", "/products", "/products?query=jacket&size=M", None, {}, 200),
    (
        "post",
        "/checkouts",
        "/checkouts",
        {"items": [{"sku": "NW-TRJ-BLK-M", "quantity": 1}]},
        {},
        201,
    ),
    (
        "post",
        "/checkouts",
        "/checkouts",
        {"items": [{"sku": "NW-TRJ-BLU-M", "quantity": 1}]},
        {},
        409,
    ),
    ("get", "/orders/{order_id}", "/orders/NW-4521", None, {}, 200),
    ("get", "/orders/{order_id}", "/orders/NW-9999", None, {}, 404),
    ("patch", "/orders/{order_id}/address", "/orders/NW-4545/address", ADDRESS, {}, 200),
    ("patch", "/orders/{order_id}/address", "/orders/NW-9999/address", ADDRESS, {}, 404),
    ("patch", "/orders/{order_id}/address", "/orders/NW-4540/address", ADDRESS, {}, 409),
    ("post", "/orders/{order_id}/cancel", "/orders/NW-4545/cancel", None, {}, 200),
    ("post", "/orders/{order_id}/cancel", "/orders/NW-9999/cancel", None, {}, 404),
    ("post", "/orders/{order_id}/cancel", "/orders/NW-4540/cancel", None, {}, 409),
    ("post", "/orders/{order_id}/refunds", "/orders/NW-4528/refunds", {"amount": 213}, {}, 201),
    ("post", "/orders/{order_id}/refunds", "/orders/NW-9999/refunds", {"amount": 9}, {}, 404),
    ("post", "/orders/{order_id}/refunds", "/orders/NW-4521/refunds", {"amount": 9}, {}, 409),
    # Hito 8, tarea 4 (decisión 039): cambio, cupón y envío express.
    ("post", "/orders/{order_id}/exchanges", "/orders/NW-4533/exchanges", SIZE_L, {}, 201),
    ("post", "/orders/{order_id}/exchanges", "/orders/NW-9999/exchanges", SIZE_L, {}, 404),
    ("post", "/orders/{order_id}/exchanges", "/orders/NW-4521/exchanges", SIZE_L, {}, 409),
    ("post", "/orders/{order_id}/coupons", "/orders/NW-4521/coupons", {"percent": 10}, {}, 201),
    ("post", "/orders/{order_id}/coupons", "/orders/NW-9999/coupons", {"percent": 10}, {}, 404),
    (
        "post",
        "/orders/{order_id}/express-shipping",
        "/orders/NW-4521/express-shipping",
        None,
        {},
        201,
    ),
    (
        "post",
        "/orders/{order_id}/express-shipping",
        "/orders/NW-9999/express-shipping",
        None,
        {},
        404,
    ),
]
# Una sola cortesía por pedido: después de un cupón, ni otro cupón ni el envío express.
COURTESY_CONFLICTS = [
    ("post", "/orders/{order_id}/coupons", {"percent": 10}),
    ("post", "/orders/{order_id}/express-shipping", None),
]


def _resolve(node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        target: Any = SPEC
        for part in node["$ref"].removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node


# -- 1. la API simulada cumple su OpenAPI ------------------------------------------------------
@pytest.mark.parametrize(("method", "path", "url", "body", "headers", "status"), EXAMPLES)
def test_la_api_responde_segun_su_contrato(
    method: str,
    path: str,
    url: str,
    body: dict[str, Any] | None,
    headers: dict[str, str],
    status: int,
) -> None:
    response = TestClient(create_app()).request(method.upper(), url, json=body, headers=headers)
    assert response.status_code == status, response.text
    documented = _resolve(SPEC["paths"][path][method]["responses"][str(status)])
    schema = documented["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": SPEC["components"]}).validate(response.json())


@pytest.mark.parametrize(("method", "path", "body"), COURTESY_CONFLICTS)
def test_una_segunda_cortesia_para_el_mismo_pedido_es_un_conflicto(
    method: str, path: str, body: dict[str, Any] | None
) -> None:
    client = TestClient(create_app())
    assert client.post("/orders/NW-4521/coupons", json={"percent": 5}).status_code == 201
    response = client.request(method.upper(), path.replace("{order_id}", "NW-4521"), json=body)
    assert response.status_code == 409
    documented = _resolve(SPEC["paths"][path][method]["responses"]["409"])
    schema = documented["content"]["application/json"]["schema"]
    Draft202012Validator({**schema, "components": SPEC["components"]}).validate(response.json())


def test_todas_las_respuestas_documentadas_estan_probadas() -> None:
    documented = {
        (method, path, int(code))
        for path, operations in SPEC["paths"].items()
        for method, operation in operations.items()
        for code in operation["responses"]
    }
    tested = {(m, p, s) for m, p, _, _, _, s in EXAMPLES}
    tested |= {(m, p, 409) for m, p, _ in COURTESY_CONFLICTS}
    assert documented == tested


@pytest.mark.parametrize("written", ["NW-4521", "nw-4521", "NW4521", "#4521", "4521", " nw 4521 "])
def test_el_numero_de_pedido_se_acepta_como_lo_escriba_el_cliente(written: str) -> None:
    assert canonical(written) == "NW-4521"
    if "#" not in written:  # en una URL, "#" empieza el fragmento
        assert TestClient(create_app()).get(f"/orders/{written.strip()}").status_code == 200


def test_los_pedidos_de_la_demo() -> None:
    client = TestClient(create_app())
    late = client.get("/orders/NW-4521").json()
    assert (
        late["status"] == "shipped"
        and late["delay"]["new_estimated_delivery"] > (late["estimated_delivery"])
    )
    refund = client.get("/orders/NW-4528").json()
    assert refund["status"] == "delivered" and refund["total"] > 100
    exchange = client.get("/orders/NW-4533").json()
    assert exchange["status"] == "delivered" and exchange["items"][0]["size"] == "M"
    shipped = client.get("/orders/NW-4540").json()
    assert shipped["status"] == "shipped" and shipped["delay"] is None
    assert {
        o["currency"] for o in client.get("/orders?customer_phone=%2B15555550101").json()["orders"]
    } == {"USD"}


def test_un_reembolso_con_la_misma_clave_no_se_duplica() -> None:
    client = TestClient(create_app())
    headers = {"Idempotency-Key": "case-1:NW-4528"}
    first = client.post("/orders/NW-4528/refunds", json={"amount": 213}, headers=headers).json()
    again = client.post("/orders/NW-4528/refunds", json={"amount": 213}, headers=headers).json()
    assert first == again


# -- 2. el tenant: idioma, documentos, tipos de caso y herramientas ---------------------------
@pytest.fixture
def embedder(conn: psycopg.Connection) -> VoyageEmbedder:
    import httpx
    from simulated.voyage import voyage_transport

    return VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )


def _without_the_demo_northwind(conn: psycopg.Connection) -> None:
    """Estos tests crean su propio Northwind. La base de desarrollo es compartida y puede tener
    el de la demo (`make demo-setup`): dentro de la transacción del test, que se revierte, le
    sacan la marca, el chat y el email del panel para que no choquen con el nuevo."""
    rows = conn.execute(
        "select id from tenants where settings ->> 'demo' = %s", (northwind.DEMO_KEY,)
    ).fetchall()
    for (tenant,) in rows:
        with tenant_scope(conn, tenant):
            conn.execute(
                "update channels set phone_number_id = phone_number_id || ':otro'"
                " where tenant_id = %s",
                (tenant,),
            )
            conn.execute(
                "update users set email = email || '.otro' where tenant_id = %s", (tenant,)
            )
    conn.execute(
        "update tenants set settings = settings - 'demo' where settings ->> 'demo' = %s",
        (northwind.DEMO_KEY,),
    )


def _manifests(conn: psycopg.Connection, tenant_id: Any) -> dict[str, dict[str, Any]]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute("select name, manifest, approved from tools").fetchall()
    assert all(approved for _, _, approved in rows)
    return {name: manifest for name, manifest, _ in rows}


def test_northwind_es_una_empresa_en_ingles_configurada_como_cualquier_otra(
    conn: psycopg.Connection, embedder: VoyageEmbedder
) -> None:
    seed = northwind.seed_northwind(conn, embedder)
    tenant = seed.tenant_id
    assert i18n.tenant_language(conn, tenant) == "en"
    with tenant_scope(conn, tenant):
        name, settings = conn.execute(
            "select name, settings from tenants where id = %s", (tenant,)
        ).fetchone() or (None, None)
        sources = {r[0] for r in conn.execute("select source from documents").fetchall()}
        types = dict(conn.execute("select name, active from case_types").fetchall())
    assert name == "Northwind Outfitters" and settings["tone"] == "english"
    assert {
        "shipping",
        "returns_and_refunds",
        "exchanges",
        "warranty",
        "discounts_and_coupons",
        "catalog",
    } <= sources
    assert all(types.values()) and {"where_is_my_order", "refund", "exchange"} <= set(types)
    manifests = _manifests(conn, tenant)
    for manifest in manifests.values():
        Draft202012Validator(MANIFEST_SCHEMA).validate(manifest)
    refund = manifests["iniciar_reembolso"]
    # Un reembolso de más de 100 USD pide aprobación; hasta 100, se hace solo.
    assert (refund["risk"], refund["approval_over"]) == ("medium", 100)
    assert mode_for(conn, tenant) == "demo"


def test_el_reembolso_sobre_100_usd_pide_aprobacion_y_el_menor_se_hace(
    conn: psycopg.Connection, embedder: VoyageEmbedder
) -> None:
    seed = northwind.seed_northwind(conn, embedder)
    guardian = build_guardian(conn, seed.tenant_id)
    with tenant_scope(conn, seed.tenant_id):
        case = conn.execute(
            "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
            (seed.tenant_id, seed.contacts["emily"]),
        ).fetchone()
    assert case is not None
    emily = ContactContext(seed.contacts["emily"], hash_phone("+15555550101"), verified=True)
    allowed = {"iniciar_reembolso"}
    high = guardian.execute(
        tenant_id=seed.tenant_id,
        case_id=case[0],
        contact=emily,
        tool_name="iniciar_reembolso",
        tool_input={"order_id": "NW-4528", "amount": 213},
        allowed=allowed,
    )
    assert high.status == "approval_required"
    sophia = ContactContext(seed.contacts["sophia"], hash_phone("+15555550103"), verified=True)
    low = guardian.execute(
        tenant_id=seed.tenant_id,
        case_id=case[0],
        contact=sophia,
        tool_name="iniciar_reembolso",
        tool_input={"order_id": "NW-4550", "amount": 56},
        allowed=allowed,
    )
    assert low.status == "executed", low.result


def test_regenerar_no_duplica_y_conserva_la_clave(
    conn: psycopg.Connection, embedder: VoyageEmbedder
) -> None:
    _without_the_demo_northwind(conn)
    first = northwind.ensure_northwind(conn, embedder, password="clave-de-northwind-demo")
    assert first.created and first.password == "clave-de-northwind-demo"

    def counts() -> tuple[int, int, int, int]:
        with tenant_scope(conn, first.tenant_id):
            row = conn.execute(
                "select (select count(*) from documents), (select count(*) from chunks),"
                " (select count(*) from tools), (select count(*) from case_types)"
            ).fetchone()
        assert row is not None
        return row[0], row[1], row[2], row[3]

    before = counts()
    second = northwind.ensure_northwind(conn, embedder)
    assert not second.created and second.tenant_id == first.tenant_id
    assert second.password is None  # la clave no cambia ni se vuelve a mostrar
    assert counts() == before
    user = auth.company_login(conn, "demo@northwind.example", "clave-de-northwind-demo")
    assert user is not None and user.company == "Northwind Outfitters"


# -- 3. el chat y el panel de Northwind --------------------------------------------------------
@pytest.fixture
def web(conn: psycopg.Connection, embedder: VoyageEmbedder) -> Iterator[TestClient]:
    _without_the_demo_northwind(conn)
    northwind.ensure_northwind(conn, embedder, password="clave-de-northwind-demo")
    stack = build_simulated_stack(conn, company="northwind")
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=lambda c: stack.agent
    )
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        kick_worker=lambda: None,
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_el_chat_de_northwind_en_ingles_con_su_marca(web: TestClient) -> None:
    page = web.get("/c/northwind-outfitters").text
    assert '<html lang="en">' in page and "Northwind Outfitters · Customer support" in page
    config = web.get("/chat/widget/config?canal=northwind-outfitters").json()
    assert config["lang"] == "en" and config["accent"] == "#2f5d50"
    assert config["suggestions"][0] == "Where is my order NW-4521?"


def test_el_panel_de_northwind_en_ingles(web: TestClient) -> None:
    response = web.post(
        "/panel/login",
        data={"email": "demo@northwind.example", "password": "clave-de-northwind-demo"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    settings = web.get("/panel/configuracion").text
    assert '<html lang="en">' in settings
    for text in ("Where is my order", "Refund", "Demo system (simulated API)", "Exchange"):
        assert text in settings, text


# -- 4. evals en inglés -----------------------------------------------------------------------
def test_los_evals_de_northwind_pasan_con_el_modelo_simulado(conn: psycopg.Connection) -> None:
    stack = build_simulated_stack(conn, company="northwind")
    report = runner.run_evals(stack, company="northwind")
    failed = {c.id: c.checks for c in report.cases if not c.resolved}
    assert failed == {} and report.critical_failures == []
    assert report.resolution_rate == 1.0


def test_los_casos_en_ingles_no_tienen_texto_en_espanol() -> None:
    cases = json.loads(runner.cases_path("northwind").read_text(encoding="utf-8"))
    assert len(cases) >= 12
    for case in cases:
        assert not re.search(r"[áéíóúñ¿¡]", json.dumps(case, ensure_ascii=False)), case["id"]
