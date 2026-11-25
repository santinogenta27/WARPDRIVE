"""Prueba gratis: cualquier sitio, sin fallar feo, y que lleve al equipo (decisión 101).

Lo que vimos con https://www.allbirds.com: un solo producto (cada talle como uno distinto), el
agente presentándose como "Prueba · allbirds.com" y una sugerencia con el nombre largo del talle.
Además: el email al operador con el informe de calidad, el botón de WhatsApp con la tienda, el
resumen después de 5 mensajes, el aviso de velocidad y los mensajes claros para cada sitio que no
se puede leer.
"""

import html
import json
import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.audit import log
from app.companies.agent import build_guardian
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.jobs.queue import Job
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.mailer import Email
from app.main import app
from app.panel.routes import PanelDeps, panel_deps
from app.trial import build, language, models, service, sources

REPO = Path(__file__).resolve().parents[2]
SITE = "https://remeras-sol.example"
EMAIL = "duena@remeras-sol.example"
OPERATOR = "equipo@plataforma.example"


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    def send(self, email: Email) -> None:
        self.sent.append(email)

    def to(self, address: str) -> list[Email]:
        return [e for e in self.sent if e.to == address]


@pytest.fixture
def embedder(conn: psycopg.Connection) -> VoyageEmbedder:
    from simulated.voyage import voyage_transport

    return VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )


@pytest.fixture
def box(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Outbox]:
    monkeypatch.setenv("ALLOW_PRIVATE_SYSTEM_URLS", "1")  # el dominio de prueba no resuelve
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://agente.example")
    monkeypatch.setenv("PANEL_EMAIL", OPERATOR)
    monkeypatch.setattr(sources, "MIN_INTERVAL", 0.0)
    service.reset_sandboxes()
    outbox = _Outbox()
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=None,
        guardian=build_guardian,
        embedder=lambda c: None,  # type: ignore[arg-type,return-value]
        kick_worker=lambda: None,
        mailer=lambda: outbox,
    )
    try:
        yield outbox
    finally:
        app.dependency_overrides.clear()
        service.reset_sandboxes()


def _trial(
    conn: psycopg.Connection, box: _Outbox, site: str = SITE, lang: str = "en"
) -> tuple[str, service.Trial]:
    email = f"duena@{urlsplit(site).hostname}"
    token = service.start(conn, box, site=site, email=email, language=lang, ip_hash="t")
    code = re.search(r"\b(\d{6})\b", box.sent[-1].body)
    assert code is not None
    trial = service.confirm(conn, token, code.group(1))
    assert trial.tenant_id is not None
    return token, trial


def _job(conn: psycopg.Connection, trial_id: uuid.UUID, phase: str = "") -> Job:
    rows = conn.execute(
        "select id, payload from jobs where kind = %s and payload->>'trial_id' = %s"
        " order by coalesce((payload->>'attempt')::int, 0) desc,"
        " coalesce((payload->>'round')::int, 0) desc",
        (service.JOB_KIND, str(trial_id)),
    ).fetchall()
    for job_id, payload in rows:
        if (payload.get("phase") or "") == phase:
            return Job(job_id, service.JOB_KIND, payload, attempts=1, max_attempts=3)
    raise AssertionError(f"no hay job {phase!r}")


def _http(routes: dict[str, Any]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        full = path + (f"?{request.url.query.decode()}" if request.url.query else "")
        answer = routes.get(full, routes.get(path))
        if answer is None:
            return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
        if isinstance(answer, httpx.Response):
            return answer
        if isinstance(answer, (dict, list)):
            return httpx.Response(200, json=answer)
        kind = "application/xml" if path.endswith(".xml") else "text/html"
        return httpx.Response(200, text=answer, headers={"content-type": kind})

    return httpx.Client(transport=httpx.MockTransport(handler))


# -- una tienda de Shopify como Allbirds -----------------------------------------------------------
def _shopify_products(count: int) -> list[dict[str, Any]]:
    products = []
    colors = ["Mushroom (Mushroom Sole)", "Natural Black (Black Sole)"]
    for n, color in enumerate(colors):  # un producto por color, los talles como variantes
        products.append(
            {
                "id": n,
                "title": f"Men's Runner NZ Slip On - {color}",
                "handle": f"mens-runner-nz-{n}",
                "body_html": "<p>Liviana y cómoda.</p>",
                "published_at": "2026-01-01",
                "product_type": "Shoes",
                "tags": [],
                "variants": [
                    {"title": s, "option1": s, "price": "98.00", "available": True}
                    for s in ("8", "8.5", "9")
                ],
                "images": [{"src": f"https://cdn.shopify.com/runner-{n}.jpg"}],
            }
        )
    products.append({**products[0], "id": 50, "title": "Digital Gift Card", "handle": "gift"})
    for n in range(count):
        products.append(
            {
                "id": 100 + n,
                "title": f"Wool Sock {n}",
                "handle": f"wool-sock-{n}",
                "body_html": "<p>Medias de lana.</p>",
                "published_at": "2026-01-01",
                "product_type": "Socks",
                "tags": [],
                "variants": [{"title": "M", "option1": "M", "price": "12.00", "available": True}],
                "images": [{"src": f"https://cdn.shopify.com/sock-{n}.jpg"}],
            }
        )
    return products


def _shopify_site(count: int = 160) -> httpx.Client:
    products = _shopify_products(count)
    return _http(
        {
            "/": '<title>Remeras Sol</title><link href="https://cdn.shopify.com/s.css">'
            "<p>Zapatillas y medias de lana.</p>",
            "/meta.json": {"name": "Remeras Sol", "currency": "USD"},
            "/products.json?limit=250&page=1": {"products": products[:250]},
            "/policies/shipping-policy": "<title>Shipping policy</title><p>We ship in 2 days.</p>",
            "/policies/refund-policy": "<title>Refund policy</title><p>30 days to return.</p>",
        }
    )


def test_shopify_de_punta_a_punta_con_informe_y_aviso_al_operador(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    token, trial = _trial(conn, box)
    job = _job(conn, trial.id)
    build.run_job(conn, job, embedder=embedder, http=_shopify_site(), mailer=box)
    build.run_job(conn, job, embedder=embedder, http=_shopify_site(), mailer=box)  # sin duplicar

    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready", ready.progress if ready else None
    assert ready.name == "Remeras Sol"
    assert len(ready.products) == service.MAX_PRODUCTS  # 161 sin la tarjeta de regalo; tope 150
    runner = ready.products[0]
    assert runner["name"] == "Men's Runner NZ Slip On"
    assert {"Mushroom (Mushroom Sole)", "Natural Black (Black Sole)", "8.5"} <= set(
        runner["variants"]
    )
    quality = ready.progress["quality"]
    assert quality["source"] == "shopify_json" and quality["by_source"] == {"shopify_json": 150}
    assert quality["discarded"]["gift_card"] == 1
    assert {"shipping", "returns"} <= set(quality["policies"])
    # Los pedidos de ejemplo usan productos distintos entre sí.
    orders = service.view(conn, ready).orders
    assert len({o["items"][0]["name"] for o in orders}) == 3

    # Un solo aviso al operador, con el informe; y uno al visitante.
    (notice,) = box.to(OPERATOR)
    assert "Remeras Sol" in notice.subject
    for expected in (
        "remeras-sol.example",
        "duena@remeras-sol.example",
        "English",
        "150",
        f"https://agente.example/probar/{token}",
        "shopify_json",
        "gift_card: 1",
        "shipping",
    ):
        assert expected in notice.body, (expected, notice.body)
    assert len(box.to("duena@remeras-sol.example")) == 2  # el código y "está lista"
    with tenant_scope(conn, ready.tenant_id):  # type: ignore[arg-type]
        docs = conn.execute("select count(*) from documents").fetchone()
    assert docs is not None and docs[0] == 4  # portada, 2 políticas y el catálogo


def test_nombre_de_la_tienda_y_sugerencias_cortas(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    token, trial = _trial(conn, box)
    assert trial.tenant_id is not None
    service.run_build(conn, trial.id, embedder=embedder, http=_shopify_site(5))
    agent = models.TrialAgent(conn, build_guardian(conn, trial.tenant_id), "en")
    name, _, _ = agent._tenant(trial.tenant_id)
    assert name == "Remeras Sol"  # nunca "Prueba · …" (el nombre interno)
    page = html.unescape(TestClient(app).get(f"/probar/{token}").text)
    assert "I'm looking for the Men's Runner NZ" in page
    assert "Size" not in page and "Mushroom (Mushroom Sole)" not in page.split("Try these")[1][:400]
    instructions = service.case_type_texts("en")["shopping"]
    assert "only" in instructions and "alternatives" in instructions


def test_contesta_en_el_idioma_del_visitante(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    assert language.detect("Hi! Where is my order DEMO-1001?") == "en"
    assert language.detect("Hola, ¿dónde está mi pedido?") == "es"
    assert language.detect("Quiero devolver el pedido DEMO-1002") == "es"
    assert language.detect("DEMO-1001") is None and language.detect("123456") is None

    _, trial = _trial(conn, box, lang="es")  # entró por /probar
    assert trial.tenant_id is not None
    service.run_build(conn, trial.id, embedder=embedder, http=_shopify_site(5))
    agent = models.TrialAgent(conn, build_guardian(conn, trial.tenant_id), "es")
    contact = uuid.uuid4()
    agent.use_language_of(trial.tenant_id, contact, "Hi! Do you have wool socks?")
    assert agent._tenant(trial.tenant_id)[1:] == ("english", "en")
    agent.use_language_of(trial.tenant_id, contact, "Hola, ¿tienen medias de lana?")
    assert agent._tenant(trial.tenant_id)[2] == "es"


def test_whatsapp_con_la_tienda_en_el_idioma_de_la_prueba(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    client = TestClient(app)
    for lang, button, message in (
        (
            "en",
            "Talk to our team on WhatsApp",
            "Hi! I just tried the AI agent with Remeras Sol and want to talk about a pilot.",
        ),
        (
            "es",
            "Hablá con nuestro equipo por WhatsApp",
            "¡Hola! Acabo de probar el agente de IA con Remeras Sol y quiero hablar de un piloto.",
        ),
    ):
        site = SITE if lang == "en" else "https://remeras-sol-ar.example"
        token, trial = _trial(conn, box, site=site, lang=lang)
        service.run_build(conn, trial.id, embedder=embedder, http=_shopify_site(5))
        page = client.get(f"/probar/{token}").text
        assert button in html.unescape(page) and "Santino" not in page
        before = len(box.to(OPERATOR))
        response = client.get(f"/probar/{token}/whatsapp", follow_redirects=False)
        assert response.status_code == 303
        target = urlsplit(response.headers["location"])
        assert f"{target.scheme}://{target.netloc}{target.path}" == "https://wa.me/5491128492480"
        assert parse_qs(target.query)["text"] == [message]
        client.get(f"/probar/{token}/whatsapp", follow_redirects=False)  # un solo aviso
        assert len(box.to(OPERATOR)) == before + 1


def _case(
    conn: psycopg.Connection, tenant_id: uuid.UUID, contact: uuid.UUID, text: str, status: str
) -> uuid.UUID:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into cases (tenant_id, contact_id, status) values (%s, %s, %s) returning id",
            (tenant_id, contact, status),
        ).fetchone()
        assert row is not None
        conn.execute(
            "insert into case_messages (tenant_id, case_id, role, text) values"
            " (%s, %s, 'customer', %s), (%s, %s, 'agent', 'Listo.')",
            (tenant_id, row[0], text, tenant_id, row[0]),
        )
    return row[0]


def test_resumen_despues_de_5_mensajes(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    client = TestClient(app)
    token, trial = _trial(conn, box)
    tenant = trial.tenant_id
    assert tenant is not None
    service.run_build(conn, trial.id, embedder=embedder, http=_shopify_site(5))
    assert client.get(f"/probar/{token}/resumen").json()["show"] is False

    with tenant_scope(conn, tenant):
        contact = conn.execute(
            "insert into contacts (tenant_id, phone_hash) values (%s, %s) returning id",
            (tenant, hash_phone("web:visitante")),
        ).fetchone()
    assert contact is not None
    solved = _case(conn, tenant, contact[0], "Where is my order DEMO-1001?", "open")
    log.record(conn, tenant, solved, "verification_checked", {"result": "verified"})
    log.record(conn, tenant, solved, "tool_call", {"tool": "ofrecer_cupon", "status": "denied"})
    log.record(
        conn,
        tenant,
        solved,
        "tool_call",
        {"tool": "iniciar_reembolso", "status": "approval_required"},
    )
    handed = _case(conn, tenant, contact[0], "I want to talk to a person", "escalated")
    log.record(conn, tenant, handed, "escalated", {"causa": "pedido"})
    conn.execute("update trials set messages = 5 where id = %s", (trial.id,))

    summary = client.get(f"/probar/{token}/resumen").json()
    assert summary["show"] is True and summary["messages"] == 5
    assert summary["resolved"] == ["Where is my order DEMO-1001?"]
    assert summary["escalated"] == 1
    assert summary["actions"] == {"verified": 1, "offer_denied": 1, "refund_pending": 1}
    page = html.unescape(client.get(f"/probar/{token}").text)
    assert 'id="summary"' in page and "Start my pilot" in page
    assert f"/probar/{token}/resumen" in page
    # Con los 30 mensajes usados, también.
    conn.execute("update trials set messages = 30 where id = %s", (trial.id,))
    assert client.get(f"/probar/{token}/resumen").json()["show"] is True


SPEED_EN = (
    "Heads up: this free trial runs on a shared demo model, so replies can take a few extra "
    "seconds. In your pilot, the agent runs on dedicated, faster models and replies in seconds."
)
BUILD_EN = (
    "Building your trial on our shared demo setup. Your real agent is set up once and is always "
    "ready."
)


def test_aviso_de_velocidad_en_try_y_probar(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    client = TestClient(app)
    token_en, trial_en = _trial(conn, box)
    token_es, trial_es = _trial(conn, box, site="https://remeras-sol-ar.example", lang="es")
    building_en = html.unescape(client.get(f"/probar/{token_en}").text)
    building_es = html.unescape(client.get(f"/probar/{token_es}").text)
    assert BUILD_EN in building_en and BUILD_EN not in building_es
    assert "Armamos tu prueba en nuestro entorno de demo compartido." in building_es

    for trial in (trial_en, trial_es):
        service.run_build(conn, trial.id, embedder=embedder, http=_shopify_site(5))
    ready_en = html.unescape(client.get(f"/probar/{token_en}").text)
    ready_es = html.unescape(client.get(f"/probar/{token_es}").text)
    assert SPEED_EN in ready_en and SPEED_EN not in ready_es
    assert "Typing…" in ready_en
    assert "Still working on it — the trial runs on a shared demo model." in ready_en
    assert "Atención: esta prueba gratis usa un modelo de demo compartido" in ready_es
    assert "Escribiendo…" in ready_es and "Todavía estoy en eso" in ready_es
    # La página lee el indicador del widget: estas clases tienen que seguir existiendo.
    widget = (REPO / "backend/app/channels/web/static/widget.js").read_text("utf-8")
    assert '"row agent typing"' in widget and 'el("span", "label"' in widget


# -- sitios que no son una tienda o que no se pueden leer ------------------------------------------
CLINIC = {
    "/": "<title>Clínica Dental Sur</title>"
    "<meta property='og:site_name' content='Clínica Dental Sur'>"
    "<h1>Implantes, ortodoncia y limpiezas</h1><a href='/servicios'>Servicios</a>"
    "<a href='/contacto'>Contacto</a><p>Atendemos de lunes a viernes de 9 a 19.</p>",
    "/servicios": "<title>Servicios</title><p>Implantes desde la primera consulta.</p>",
    "/contacto": "<title>Contacto</title><p>Escribinos a turnos@clinica.example.</p>",
}


def test_sitio_que_no_es_tienda_sin_pedidos_inventados(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    token, trial = _trial(conn, box, site="https://clinica-sur.example", lang="es")
    service.run_build(conn, trial.id, embedder=embedder, http=_http(CLINIC))
    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready", ready
    assert ready.progress["mode"] == "services" and ready.products == []
    assert service.view(conn, ready).orders == []
    page = html.unescape(TestClient(app).get(f"/probar/{token}").text)
    assert "DEMO-1001" not in page and "Pedidos de ejemplo" not in page
    assert "¿Qué servicios ofrecen?" in page
    assert ready.name == "Clínica Dental Sur"
    with tenant_scope(conn, ready.tenant_id):  # type: ignore[arg-type]
        active = conn.execute("select count(*) from case_types where active").fetchone()
    assert active == (0,)  # sin pedidos ni productos: atiende con las páginas del sitio


def test_sitio_bloqueado_ofrece_la_prueba_de_ejemplo(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    client = TestClient(app)
    token, trial = _trial(conn, box, site="https://bloqueada.example")
    blocked = _http({"/": httpx.Response(403, text="Forbidden")})
    service.run_build(conn, trial.id, embedder=embedder, http=blocked)
    failed = service.find(conn, token)
    assert failed is not None and failed.status == "failed"
    assert failed.progress["error_code"] == "blocked"
    page = html.unescape(client.get(f"/probar/{token}").text)
    assert "blocks automated visits" in page
    assert "Continue with a sample trial" in page and "Talk to our team on WhatsApp" in page

    assert client.post(f"/probar/{token}/ejemplo", follow_redirects=False).status_code == 303
    again = service.find(conn, token)
    assert again is not None and again.status == "building" and again.progress["sample"] is True
    build.run_job(conn, _job(conn, trial.id), embedder=embedder, http=blocked)
    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready" and ready.progress["mode"] == "sample"
    assert len(ready.products) == 3


@pytest.mark.parametrize(
    ("routes", "code", "words"),
    [
        (
            {"/": '<html><body><div id="root"></div><script src="/app.js"></script></body></html>'},
            "js_only",
            "JavaScript",
        ),
        (
            {
                "/": httpx.Response(302, headers={"location": "https://cerrada.example/password"}),
                "/password": '<form action="/password"><input type="password"></form>',
            },
            "password",
            "password",
        ),
    ],
    ids=["solo-javascript", "con-contrasena"],
)
def test_mensajes_claros_cuando_no_se_puede_leer(
    conn: psycopg.Connection,
    box: _Outbox,
    embedder: VoyageEmbedder,
    routes: dict[str, Any],
    code: str,
    words: str,
) -> None:
    token, trial = _trial(conn, box, site="https://cerrada.example")
    service.run_build(conn, trial.id, embedder=embedder, http=_http(routes))
    failed = service.find(conn, token)
    assert failed is not None and failed.status == "failed"
    assert failed.progress["error_code"] == code
    state = TestClient(app).get(f"/probar/{token}/estado").json()
    assert words in state["message"]


def test_un_dominio_que_no_existe(
    conn: psycopg.Connection, box: _Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOW_PRIVATE_SYSTEM_URLS", "")
    with pytest.raises(service.TrialError) as error:
        service.start(
            conn, box, site="no-existe-de-verdad.invalid", email=EMAIL, language="en", ip_hash="t"
        )
    assert "couldn't find" in str(error.value) and "no-existe-de-verdad.invalid" in str(error.value)


# -- sitemap: los primeros ahora, el resto en segundo plano ----------------------------------------
def _sitemap_site(count: int) -> dict[str, Any]:
    def page(n: int) -> str:
        data = {
            "@context": "https://schema.org",
            "@type": "Product",
            "name": f"Vela {n}",
            "image": f"https://velas.example/img/{n}.jpg",
            "offers": {"@type": "Offer", "price": "8.900,00", "priceCurrency": "ARS"},
        }
        return (
            f'<title>Vela {n}</title><script type="application/ld+json">{json.dumps(data)}</script>'
        )

    urls = "".join(
        f"<url><loc>https://velas.example/productos/vela-{n}/</loc></url>" for n in range(count)
    )
    routes: dict[str, Any] = {
        "/": "<title>Velas</title><p>Velas de soja artesanales.</p>",
        "/sitemap.xml": f"<urlset>{urls}</urlset>",
    }
    routes.update({f"/productos/vela-{n}/": page(n) for n in range(count)})
    return routes


def test_el_resto_del_catalogo_se_suma_en_segundo_plano(
    conn: psycopg.Connection, box: _Outbox, embedder: VoyageEmbedder
) -> None:
    routes = _sitemap_site(25)
    token, trial = _trial(conn, box, site="https://velas.example", lang="es")
    service.run_build(conn, trial.id, embedder=embedder, http=_http(routes))
    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready"
    assert len(ready.products) == sources.FIRST_PRODUCTS
    assert ready.products[0]["price"] == 8900.0 and ready.products[0]["currency"] == "ARS"
    rest = _job(conn, trial.id, "rest")
    build.run_rest(conn, rest.payload, embedder=embedder, http=_http(routes))
    build.run_rest(conn, rest.payload, embedder=embedder, http=_http(routes))  # idempotente
    done = service.find(conn, token)
    assert done is not None and len(done.products) == 25
    assert done.progress["quality"]["by_source"] == {"structured_data": 25}
    found = service.sandbox(conn, done.tenant_id).get("/products", params={"query": "vela 24"})  # type: ignore[arg-type]
    # La búsqueda devuelve los que mejor coinciden (el primero, el pedido) y cuántos más hay.
    assert found.json()["products"][0]["name"] == "Vela 24"
    assert len(found.json()["products"]) == service.SEARCH_RESULTS
    assert found.json()["total_matches"] == 25


def test_el_modelo_como_ultimo_recurso_con_tope_de_20_llamadas(
    conn: psycopg.Connection,
    box: _Outbox,
    embedder: VoyageEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = {
        f"/productos/silla-{n}/": f"<title>Silla {n}</title><h1>Silla {n}</h1><p>$ 45.990</p>"
        for n in range(30)
    }
    urls = "".join(f"<url><loc>https://sillas.example{p}</loc></url>" for p in pages)
    routes: dict[str, Any] = {
        "/": "<title>Sillas</title><p>Sillas de diseño.</p>",
        "/sitemap.xml": f"<urlset>{urls}</urlset>",
        **pages,
    }
    calls: list[str] = []

    def asker(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Any:
        def ask(prompt: str) -> tuple[str, int]:
            calls.append(prompt)
            number = re.search(r"Silla (\d+)", prompt)
            name = f"Silla {number.group(1)}" if number else "Silla"
            return json.dumps({"name": name, "price_text": "$ 45.990", "currency": "ARS"}), 50

        return ask

    monkeypatch.setattr(build, "_model_asker", asker)
    token, trial = _trial(conn, box, site="https://sillas.example", lang="es")
    service.run_build(conn, trial.id, embedder=embedder, http=_http(routes))
    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready"
    quality = ready.progress["quality"]
    assert len(calls) <= sources.MAX_LLM_CALLS and quality["llm_calls"] == len(calls)
    assert quality["source"] == "llm_validado" and quality["llm_tokens"] == 50 * len(calls)
    assert ready.products and all(p["price"] == 45990.0 for p in ready.products)


def test_el_segundo_plano_va_por_tramos_y_no_frena_otros_armados(
    conn: psycopg.Connection,
    box: _Outbox,
    embedder: VoyageEmbedder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Visto en el QA: un armado de 15 s esperó 58 s en la cola detrás del segundo plano de otra
    prueba. Cada parte trabaja un tramo corto y encola el resto, después de lo que esté en cola."""
    routes = _sitemap_site(25)
    token, trial = _trial(conn, box, site="https://velas.example", lang="es")
    service.run_build(conn, trial.id, embedder=embedder, http=_http(routes))
    rest = _job(conn, trial.id, "rest")
    monkeypatch.setattr(build, "REST_SLICE_SECONDS", 0.0)  # el tramo se acaba enseguida
    build.run_rest(conn, rest.payload, embedder=embedder, http=_http(routes))
    following = conn.execute(
        "select payload, run_after > now() from jobs where idempotency_key = %s",
        (build.rest_key(trial.id, rest.payload["attempt"], 0, 1),),
    ).fetchone()
    assert following is not None and following[1] is True  # después de lo que ya está en cola
    assert len(following[0]["products"]) == 25 - sources.FIRST_PRODUCTS
    monkeypatch.setattr(build, "REST_SLICE_SECONDS", 20.0)
    build.run_rest(conn, following[0], embedder=embedder, http=_http(routes))
    done = service.find(conn, token)
    assert done is not None and len(done.products) == 25
    assert done.progress["background"] == "done"
