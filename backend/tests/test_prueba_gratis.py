"""Prueba gratis self-service — test de aceptación (decisión 075).

Un visitante pone la URL de su tienda y su email en `/probar`, confirma el email con un código y
se arma una empresa de prueba: el rastreo de su sitio carga sus políticas y lee sus productos, y
el mismo agente atiende con pedidos de ejemplo `DEMO-…` sobre la API simulada. El visitante
aprueba el reembolso en un mini panel, puede pasar a piloto conservando lo cargado (sin los
pedidos de ejemplo) o pedir hablar con el operador. Límites: una prueba por email y por dominio,
30 mensajes, tope de costo, protección contra bots y borrado a los 7 días (job idempotente).
"""

import json
import re
import time
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.channels.web.routes import WebDeps, web_deps
from app.companies.agent import build_guardian
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.jobs.queue import Job
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.mailer import Email
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps
from app.policy.guardian import ContactContext
from app.trial import catalog, service
from app.trial.routes import form_stamp

SITE = "https://tienda-sol.example"
EMAIL = "duena@tienda-sol.example"

MATE = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "Mate de calabaza",
    "image": ["https://tienda-sol.example/img/mate.jpg"],
    "offers": {"@type": "Offer", "price": "12500", "priceCurrency": "ARS"},
}
HOME_GRAPH = {
    "@context": "https://schema.org",
    "@graph": [
        {"@type": "Organization", "name": "Tienda Sol"},
        {
            "@type": "Product",
            "name": "Bombilla de alpaca",
            "image": {"url": "https://tienda-sol.example/img/bombilla.jpg"},
            "offers": [{"@type": "Offer", "price": 4800, "priceCurrency": "ARS"}],
            "url": "https://tienda-sol.example/productos/bombilla",
        },
    ],
}
PAGES = {
    "/": (
        "<title>Tienda Sol</title>"
        f'<script type="application/ld+json">{json.dumps(HOME_GRAPH)}</script>'
        '<a href="/politicas/devoluciones">Devoluciones</a> <a href="/envios">Envíos</a>'
        ' <a href="/productos/mate">Mate</a> <a href="/productos/termo">Termo</a>'
        "<p>Mates y termos artesanales.</p>"
    ),
    "/politicas/devoluciones": (
        "<title>Devoluciones</title><p>Tenés 30 días desde que recibís tu compra para "
        "devolverla. El reembolso se hace al mismo medio de pago.</p>"
    ),
    "/envios": "<title>Envíos</title><p>Despachamos en 48 horas hábiles a todo el país.</p>",
    "/productos/mate": (
        f'<title>Mate de calabaza</title><script type="application/ld+json">{json.dumps(MATE)}'
        "</script><p>Mate curado a mano.</p>"
    ),
    "/productos/termo": (
        '<title>Termo</title><meta property="og:type" content="product">'
        '<meta property="og:title" content="Termo acero 1 L">'
        '<meta property="og:image" content="https://tienda-sol.example/img/termo.jpg">'
        '<meta property="product:price:amount" content="38900">'
        '<meta property="product:price:currency" content="ARS"><p>Termo de acero.</p>'
    ),
}


def _site() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "tienda-sol.example", request.url
        body = PAGES.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler))


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    def send(self, email: Email) -> None:
        self.sent.append(email)


@pytest.fixture
def embedder(conn: psycopg.Connection) -> VoyageEmbedder:
    from simulated.voyage import voyage_transport

    return VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )


@pytest.fixture
def box(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, embedder: VoyageEmbedder
) -> Iterator[_Outbox]:
    monkeypatch.setenv("ALLOW_PRIVATE_SYSTEM_URLS", "1")  # el dominio de prueba no resuelve
    monkeypatch.setenv("PANEL_EMAIL", "santino@plataforma.example")
    monkeypatch.setenv("PANEL_PASSWORD", "clave-del-operador")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://agente.example")
    auth._failures.clear()
    service.reset_sandboxes()
    outbox = _Outbox()
    monkeypatch.setattr("app.panel.signup_routes.mailer_from_env", lambda: outbox)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=None,
        guardian=build_guardian,
        embedder=lambda c: embedder,
        kick_worker=lambda: None,
        mailer=lambda: outbox,
    )

    def no_agent(c: psycopg.Connection) -> Any:
        raise AssertionError("con el límite alcanzado el agente no corre")

    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn), agent=no_agent
    )
    try:
        yield outbox
    finally:
        app.dependency_overrides.clear()
        service.reset_sandboxes()


def _start(
    client: TestClient, site: str = SITE, email: str = EMAIL, **extra: str
) -> httpx.Response:
    data = {"sitio": site, "email": email, "t": form_stamp(time.time() - 10), "web": ""}
    data.update(extra)
    return client.post("/probar", data=data, follow_redirects=False)


def _code(email: Email) -> str:
    found = re.search(r"\b(\d{6})\b", email.body)
    assert found is not None, email.body
    return found.group(1)


def _built(
    conn: psycopg.Connection, client: TestClient, box: _Outbox, embedder: VoyageEmbedder
) -> tuple[str, service.Trial]:
    response = _start(client)
    assert response.status_code == 303, response.text
    token = response.headers["location"].rsplit("/", 1)[-1]
    confirmed = client.post(
        f"/probar/{token}/codigo", data={"codigo": _code(box.sent[-1])}, follow_redirects=False
    )
    assert confirmed.status_code == 303
    trial = service.find(conn, token)
    assert trial is not None and trial.status == "building" and trial.tenant_id is not None
    service.run_build(conn, trial.id, embedder=embedder, http=_site())
    found = service.find(conn, token)
    assert found is not None
    return token, found


def test_lee_los_productos_de_los_datos_estructurados() -> None:
    found = catalog.extract(PAGES["/"], SITE + "/") + catalog.extract(
        PAGES["/productos/mate"], SITE + "/productos/mate"
    )
    found += catalog.extract(PAGES["/productos/termo"], SITE + "/productos/termo")
    by_name = {p.name: p for p in found}
    assert set(by_name) == {"Bombilla de alpaca", "Mate de calabaza", "Termo acero 1 L"}
    assert (
        by_name["Mate de calabaza"].price == 12500 and by_name["Mate de calabaza"].currency == "ARS"
    )
    assert by_name["Mate de calabaza"].image == "https://tienda-sol.example/img/mate.jpg"
    assert by_name["Mate de calabaza"].url == SITE + "/productos/mate"
    assert by_name["Bombilla de alpaca"].image.endswith("bombilla.jpg")
    assert by_name["Termo acero 1 L"].price == 38900
    assert catalog.extract("<p>sin productos</p>", SITE) == []


def test_de_punta_a_punta_prueba_piloto_y_contacto(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    client = TestClient(app)
    assert "Probalo con tu tienda" in client.get("/probar").text
    assert "Try it with your store" in client.get("/try").text

    response = _start(client)
    token = response.headers["location"].rsplit("/", 1)[-1]
    assert box.sent[-1].to == EMAIL
    assert "código" in client.get(f"/probar/{token}").text.lower()
    wrong = client.post(f"/probar/{token}/codigo", data={"codigo": "000000"})
    assert wrong.status_code == 400

    code = _code(box.sent[-1])
    assert (
        client.post(
            f"/probar/{token}/codigo", data={"codigo": code}, follow_redirects=False
        ).status_code
        == 303
    )
    trial = service.find(conn, token)
    assert trial is not None and trial.tenant_id is not None
    tenant = trial.tenant_id
    progress = client.get(f"/probar/{token}/estado").json()
    assert progress["status"] == "building"

    service.run_build(conn, trial.id, embedder=embedder, http=_site())
    service.run_build(conn, trial.id, embedder=embedder, http=_site())  # idempotente
    state = client.get(f"/probar/{token}/estado").json()
    assert state["status"] == "ready", state
    assert state["pages"] == 5
    names = {p["name"] for p in state["products"]}
    assert names == {"Bombilla de alpaca", "Mate de calabaza", "Termo acero 1 L"}
    orders = {o["id"]: o for o in state["orders"]}
    assert set(orders) == {"DEMO-1001", "DEMO-1002", "DEMO-1003"}
    assert orders["DEMO-1001"]["status"] == "shipped" and orders["DEMO-1001"]["delay"]
    assert orders["DEMO-1002"]["status"] == "delivered"
    assert orders["DEMO-1003"]["status"] == "paid"

    with tenant_scope(conn, tenant):
        docs = conn.execute("select count(*) from documents").fetchone()
        text = " ".join(r[0] for r in conn.execute("select text from chunks").fetchall())
        settings = conn.execute("select settings from tenants where id = %s", (tenant,)).fetchone()
    assert docs is not None and docs[0] == 6  # 5 páginas + el catálogo, sin duplicar
    assert "30 días" in text and "Mate de calabaza" in text
    assert settings is not None and settings[0]["trial"] and settings[0]["behind_the_scenes"]

    # La página: el agente con "detrás de escena", los pedidos de ejemplo y las sugerencias.
    page = client.get(f"/probar/{token}").text
    for expected in (
        "Pedidos de ejemplo",
        "DEMO-1001",
        "Empezar mi piloto",
        "Hablá con nuestro equipo por WhatsApp",  # decisión 101
    ):
        assert expected in page
    assert f"/demo/{state['chat_slug']}" in page
    behind = client.get(f"/demo/{state['chat_slug']}")
    assert behind.status_code == 200
    # Sin la tienda ficticia de la demo: se confundiría con la del visitante.
    assert '<body class="trial">' in behind.text and "es una tienda ficticia" not in behind.text

    # El flujo, por el guardián del mismo agente: verificación con el email del visitante.
    guardian = build_guardian(conn, tenant)
    with tenant_scope(conn, tenant):
        contact = conn.execute(
            # Como queda después de verificarse con el código que le llega a su email.
            "insert into contacts (tenant_id, phone_hash, verified_phone_hash, verified_until)"
            " values (%s, %s, %s, now() + interval '1 day') returning id",
            (tenant, hash_phone("web:visitante"), hash_phone(service.SAMPLE_PHONE)),
        ).fetchone()
        assert contact is not None
        case = conn.execute(
            "insert into cases (tenant_id, contact_id) values (%s, %s) returning id",
            (tenant, contact[0]),
        ).fetchone()
        assert case is not None
    who = ContactContext(contact[0], hash_phone(service.SAMPLE_PHONE), True)
    allowed = {"consultar_pedido", "ofrecer_cupon", "buscar_productos", "armar_carrito"}
    allowed |= {"iniciar_reembolso"}

    def run(tool: str, params: dict[str, Any]) -> Any:
        return guardian.execute(
            tenant_id=tenant,
            case_id=case[0],
            contact=who,
            tool_name=tool,
            tool_input=params,
            allowed=allowed,
        )

    # El cliente de los pedidos de ejemplo es el visitante: el código le llega a su email.
    customers = service.sandbox(conn, tenant).get("/customers", params={"email": EMAIL}).json()
    assert [c["phone"] for c in customers["customers"]] == [service.SAMPLE_PHONE]
    order = run("consultar_pedido", {"order_id": "DEMO-1001"})
    assert order.status == "executed" and order.result["data"]["tracking_number"]
    assert run("ofrecer_cupon", {"order_id": "DEMO-1001", "percent": 10}).status == "executed"
    assert run("ofrecer_cupon", {"order_id": "DEMO-1003", "percent": 40}).status == "denied"
    products = run("buscar_productos", {"query": "mate"})
    assert products.status == "executed"
    mate = products.result["data"]["products"][0]
    assert mate["price"] == 12500 and mate["image"].endswith("mate.jpg")
    cart = run("armar_carrito", {"items": [{"sku": mate["variants"][0]["sku"], "quantity": 1}]})
    assert cart.status == "executed"
    # Una tienda que no es Shopify: el link de compra lleva a la página real del producto, con el
    # ref de la conversación (decisión 103).
    url = cart.result["data"]["url"]
    assert url.startswith(f"{mate['url']}?ref=ea-"), url
    refund = run("iniciar_reembolso", {"order_id": "DEMO-1002", "amount": 1})
    assert refund.status == "approval_required", refund

    # El mini panel: el visitante aprueba su propio reembolso.
    pending = client.get(f"/probar/{token}/aprobaciones").json()["approvals"]
    assert len(pending) == 1
    decided = client.post(
        f"/probar/{token}/aprobaciones/{pending[0]['id']}", data={"decision": "aprobar"}
    )
    assert decided.status_code == 200 and decided.json()["status"] == "executed"
    assert client.get(f"/probar/{token}/aprobaciones").json()["approvals"] == []
    other = client.post(f"/probar/{uuid.uuid4().hex}/aprobaciones/{pending[0]['id']}", data={})
    assert other.status_code == 404

    # Hablar con el equipo: un email al operador, una sola vez.
    sent = len(box.sent)
    assert client.post(f"/probar/{token}/contacto", follow_redirects=False).status_code == 303
    client.post(f"/probar/{token}/contacto", follow_redirects=False)
    assert len(box.sent) == sent + 1
    assert box.sent[-1].to == "santino@plataforma.example"
    assert SITE in box.sent[-1].body and EMAIL in box.sent[-1].body

    # Empezar mi piloto: el registro con los datos cargados convierte la empresa de prueba.
    response = client.get(f"/probar/{token}/piloto", follow_redirects=False)
    assert response.headers["location"] == f"/registro?prueba={token}"
    form = client.get(f"/registro?prueba={token}").text
    assert EMAIL in form and 'value="Tienda Sol"' in form
    response = client.post(
        "/registro",
        data={
            "empresa": "Tienda Sol",
            "email": EMAIL,
            "clave": "clave-tienda-sol",
            "prueba": token,
        },
    )
    assert response.status_code == 200, response.text
    link = re.search(r"token=([\w-]+)", box.sent[-1].body)
    assert link is not None
    response = client.get(f"/registro/confirmar?token={link.group(1)}", follow_redirects=False)
    assert response.status_code == 303

    user = auth.company_login(conn, EMAIL, "clave-tienda-sol")
    assert user is not None and user.tenant_id == tenant  # la misma empresa, convertida
    with tenant_scope(conn, tenant):
        name, settings = conn.execute(
            "select name, settings from tenants where id = %s", (tenant,)
        ).fetchone() or (None, None)
        kept = conn.execute("select count(*) from documents").fetchone()
        approved = conn.execute("select count(*) from tools where approved").fetchone()
        connected = conn.execute("select count(*) from system_connections").fetchone()
        open_cases = conn.execute("select count(*) from cases where archived_at is null").fetchone()
    assert name == "Tienda Sol" and "trial" not in settings
    assert not settings.get("behind_the_scenes") and "monthly_budget_usd" not in settings
    assert kept is not None and kept[0] == 6  # políticas y catálogo
    assert approved == (0,) and connected == (0,) and open_cases == (0,)  # sin pedidos de ejemplo
    converted = service.find(conn, token)
    assert converted is not None and converted.status == "converted"

    # El panel del operador: la lista de pruebas para el seguimiento.
    current = app.dependency_overrides[panel_deps]()
    app.dependency_overrides[panel_deps] = lambda: replace(current, tenant_id=tenant)
    operator = TestClient(app)
    operator.post(
        "/panel/login",
        data={"email": "santino@plataforma.example", "password": "clave-del-operador"},
    )
    listing = operator.get("/panel/operator/trials")
    assert listing.status_code == 200, listing.text
    assert "tienda-sol.example" in listing.text and EMAIL in listing.text
    assert "Convertida" in listing.text and "Pidió hablar" in listing.text


def test_limites_de_la_prueba(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    client = TestClient(app)
    # Protección contra bots: campo trampa y un formulario enviado demasiado rápido.
    assert _start(client, web="http://spam.example").status_code == 400
    assert _start(client, t=form_stamp(time.time())).status_code == 400
    assert _start(client, t="falso").status_code == 400
    assert box.sent == []

    token, trial = _built(conn, client, box, embedder)
    # Una prueba por email y por dominio.
    assert _start(client).status_code == 400
    assert _start(client, email="otra@persona.example").status_code == 400
    assert _start(client, site="https://www.tienda-sol.example/").status_code == 400
    assert _start(client, site="https://otra-tienda.example", email=EMAIL).status_code == 400

    # 30 mensajes por prueba: después, el chat avisa sin correr el agente.
    assert trial.tenant_id is not None
    assert service.chat_allowance(conn, trial.tenant_id) == service.MAX_MESSAGES
    session = client.post(f"/chat/session?canal={service.view(conn, trial).chat_slug}").json()
    conn.execute("update trials set messages = %s where id = %s", (service.MAX_MESSAGES, trial.id))
    assert service.chat_allowance(conn, trial.tenant_id) == 0
    stream = client.post("/chat/messages", json={"session": session["session"], "text": "hola"})
    assert "event: limit" in stream.text and '"scope": "trial"' in stream.text

    # Tope de costo: pasado el tope, tampoco.
    conn.execute("update trials set messages = 0 where id = %s", (trial.id,))
    with tenant_scope(conn, trial.tenant_id):
        conn.execute(
            "insert into usage (tenant_id, kind, model, cost_usd) values (%s, 'llm', 'sim', %s)",
            (trial.tenant_id, service.cost_cap() + Decimal("0.01")),
        )
    assert service.chat_allowance(conn, trial.tenant_id) == 0

    # Como mucho 3 pruebas por IP por día.
    for n in range(2):
        assert (
            _start(client, site=f"https://tienda{n}.example", email=f"a{n}@x.example").status_code
            == 303
        )
    assert _start(client, site="https://tienda9.example", email="z@x.example").status_code == 400


def test_la_prueba_se_borra_a_los_7_dias_si_no_paso_a_piloto(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    client = TestClient(app)
    token, trial = _built(conn, client, box, embedder)
    assert trial.tenant_id is not None
    job = conn.execute(
        "select payload, run_after - now() from jobs where idempotency_key = %s",
        (f"trial-expire-{trial.id}",),
    ).fetchone()
    assert job is not None and job[0] == {"trial_id": str(trial.id)}
    assert timedelta(days=6, hours=23) < job[1] <= timedelta(days=7, minutes=5)

    expire = Job(
        id=uuid.uuid4(), kind=service.EXPIRE_JOB, payload=job[0], attempts=1, max_attempts=5
    )
    service.run_expire_job(conn, expire)
    service.run_expire_job(conn, expire)  # idempotente: la segunda no hace nada
    gone = conn.execute("select 1 from tenants where id = %s", (trial.tenant_id,)).fetchone()
    assert gone is None
    expired = service.find(conn, token)
    assert expired is not None and expired.status == "expired"
    assert client.get(f"/probar/{token}").status_code == 410
    assert _start(client).status_code == 400  # el límite por email sigue


def test_una_prueba_convertida_no_se_borra(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    client = TestClient(app)
    token, trial = _built(conn, client, box, embedder)
    assert trial.tenant_id is not None
    service.convert(conn, trial.id, company="Tienda Sol", email=EMAIL, password_hash="x")
    payload = {"trial_id": str(trial.id)}
    expire = Job(
        id=uuid.uuid4(), kind=service.EXPIRE_JOB, payload=payload, attempts=1, max_attempts=5
    )
    service.run_expire_job(conn, expire)
    assert conn.execute("select 1 from tenants where id = %s", (trial.tenant_id,)).fetchone()


def test_las_instrucciones_estan_en_el_idioma_de_la_empresa(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    from app.companies import review

    es, en = service.case_type_texts("es"), service.case_type_texts("en")
    assert set(es) == set(en) == set(service.CASE_TYPES)
    for kind in service.CASE_TYPES:  # mismas herramientas en los dos idiomas
        tools_es = review.parse_instructions(es[kind]).get("herramientas")
        assert tools_es == review.parse_instructions(en[kind]).get("herramientas")
    assert "Buscá el pedido" in es["where_is_my_order"]
    assert "Look up the order" in en["where_is_my_order"]
    assert "Northwind" not in " ".join([*es.values(), *en.values()])

    client = TestClient(app)
    _, trial = _built(conn, client, box, embedder)  # una prueba pedida en castellano
    assert trial.tenant_id is not None
    with tenant_scope(conn, trial.tenant_id):
        texts = dict(conn.execute("select name, instructions from case_types").fetchall())
    assert texts == es


def test_el_rastreo_de_la_prueba_lee_primero_politicas_y_productos() -> None:
    """Visto con tiendas reales: en orden de aparición, las 30 páginas se iban en categorías y
    no llegaban ni a las políticas del pie ni a las páginas de producto."""
    rank = service._priority()
    assert rank("https://t.example/policies/refund-policy") == 0
    assert rank("https://t.example/pages/envios") == 0
    assert rank("https://t.example/preguntas-frecuentes") == 0
    assert rank("https://t.example/products/mate") == 1
    assert rank("https://t.example/collections/cafe") == 2
    assert rank("https://t.example/account/login") == 4
    more = [rank(f"https://t.example/productos/p{n}") for n in range(service.PRODUCT_PAGES + 2)]
    assert more.count(1) == service.PRODUCT_PAGES - 1 and more[-1] == 3  # pocas páginas de producto


def test_el_nombre_de_la_tienda() -> None:
    assert (
        service._store_name("Café Martínez", "Tienda Online", "cafemartinez.com") == "Café Martínez"
    )
    title = "#1 Organic Coffee – Death Wish Coffee"
    assert service._store_name("", title, "deathwishcoffee.com") == "Death Wish Coffee"
    assert service._store_name("", "Tienda Sol", "tienda-sol.example") == "Tienda Sol"
    assert service._store_name("", "", "tienda-sol.example") == "tienda-sol.example"
    meta = '<meta property="og:site_name" content="Café Martínez">'
    assert catalog.site_name(meta) == "Café Martínez"


def test_las_tarjetas_de_regalo_no_son_productos_de_ejemplo() -> None:
    for name in ("Death Wish Coffee Digital Gift Card", "Tarjeta de regalo $5000", "E-Gift"):
        assert service._GIFT_CARD.search(name)
    assert not service._GIFT_CARD.search("Mate de calabaza")
