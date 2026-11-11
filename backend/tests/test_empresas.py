"""Hito 6, etapa 1 — onboarding de una empresa nueva, de punta a punta y sin comandos.

Test de aceptación: el operador crea una empresa desde el panel; su usuario sube el historial y
los documentos, corre el análisis, revisa y aprueba tipos de caso y herramientas, conecta su
sistema y prueba su agente en su propio chat. En cada pantalla y en cada endpoint, un usuario de
una empresa no ve ni toca datos de otra (decisión 026).
"""

import json
import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from cryptography.fernet import InvalidToken
from fastapi.testclient import TestClient
from simulated.stack import SimulatedStack, build_simulated_stack
from test_onboarding_pipeline import FakeOnboardingModel
from test_onboarding_pipeline import _deps as pipeline_deps

from acme_api import OPENAPI_PATH
from app.agent.loop import Agent
from app.channels.web.routes import WebDeps, web_deps
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.companies import connection as system
from app.companies import onboarding
from app.companies.create import chat_slug
from app.crypto import decrypt_for
from app.db.connection import tenant_scope
from app.jobs.queue import Job
from app.main import app
from app.panel import auth
from app.panel.company_routes import parse_multipart
from app.panel.routes import PanelDeps, panel_deps
from app.policy.guardian import Guardian
from app.tools.runtime import ToolRuntime

ACME = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "acme"
ADMIN, ADMIN_PASSWORD = "operador@plataforma.example", "clave-del-operador"
NORTE, NORTE_PASSWORD = "ana@tiendanorte.example", "clave-de-tienda-norte"
SECRET = "token-secreto-de-tienda-norte"


@dataclass
class World:
    conn: psycopg.Connection
    stack: SimulatedStack
    kicks: list[str] = field(default_factory=list)
    upstream: list[httpx.Request] = field(default_factory=list)  # pedidos al sistema de Norte
    upstream_status: int = 200

    def client_factory(self, found: system.SystemConnection, secret: str | None) -> httpx.Client:
        def handler(request: httpx.Request) -> httpx.Response:
            self.upstream.append(request)
            return httpx.Response(self.upstream_status, json={"customers": []})

        assert found.base_url is not None
        return httpx.Client(
            base_url=found.base_url,
            headers=system.auth_headers(found, secret),
            transport=httpx.MockTransport(handler),
        )

    def guardian(self, conn: psycopg.Connection, tenant_id: uuid.UUID) -> Guardian:
        if tenant_id == self.stack.seed.tenant_id:
            return Guardian(conn, ToolRuntime(self.stack.acme))
        runtime, _ = system.runtime_for(conn, tenant_id, client_factory=self.client_factory)
        return Guardian(conn, runtime)

    def agent(self, conn: psycopg.Connection, tenant_id: uuid.UUID) -> Agent:
        if tenant_id == self.stack.seed.tenant_id:
            return self.stack.agent
        return Agent(conn, self.stack.llm, self.stack.embedder, self.guardian(conn, tenant_id))

    def tenant(self, name: str) -> uuid.UUID:
        row = self.conn.execute("select id from tenants where name = %s", (name,)).fetchone()
        assert row is not None
        tenant_id: uuid.UUID = row[0]
        return tenant_id


@pytest.fixture
def world(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[World]:
    monkeypatch.setenv("PANEL_EMAIL", ADMIN)
    monkeypatch.setenv("PANEL_PASSWORD", ADMIN_PASSWORD)
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    found = World(conn, stack)
    address = f"web:acme-test-{uuid.uuid4().hex[:8]}"
    register_whatsapp_channel(conn, stack.seed.tenant_id, phone_number_id=address, kind="web")
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=found.guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: found.kicks.append("kick"),
        client_factory=found.client_factory,
    )
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn),
        agent=lambda c: stack.agent,
        address=address,
        agent_for=found.agent,
    )
    try:
        yield found
    finally:
        app.dependency_overrides.clear()


def login(email: str, password: str) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/panel/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return client


def csrf(client: TestClient, path: str = "/panel/configuracion") -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert match is not None
    return match.group(1)


def create_norte(world: World) -> TestClient:
    """El operador crea Tienda Norte y su usuario entra a su panel."""
    admin = login(ADMIN, ADMIN_PASSWORD)
    page = admin.post(
        "/panel/empresas",
        data={
            "nombre": "Tienda Norte",
            "email": NORTE,
            "clave": NORTE_PASSWORD,
            "csrf": csrf(admin, "/panel/empresas"),
        },
    )
    assert page.status_code == 200 and "Tienda Norte quedó creada" in page.text
    return login(NORTE, NORTE_PASSWORD)


def post(client: TestClient, path: str, **data: str) -> httpx.Response:
    return client.post(path, data={"csrf": csrf(client), **data}, follow_redirects=False)


def upload_history(norte: TestClient) -> httpx.Response:
    files = [
        ("archivos", (name, (ACME / "onboarding" / name).read_bytes(), "application/octet-stream"))
        for name in ("whatsapp_export.zip", "tickets.csv", "tickets.json")
    ] + [
        ("archivos", (path.name, path.read_bytes(), "text/markdown"))
        for path in sorted((ACME / "documents").glob("*.md"))
    ]
    return norte.post(
        "/panel/onboarding/fuentes",
        data={"csrf": csrf(norte, "/panel/onboarding")},
        files=files,
        follow_redirects=False,
    )


def test_la_empresa_sube_un_pdf_y_un_escaneado_se_avisa_en_la_pantalla(world: World) -> None:
    norte = create_norte(world)
    tenant_id = world.tenant("Tienda Norte")
    assert ".pdf" in norte.get("/panel/onboarding").text  # el selector de archivos lo acepta

    def upload(*names: str) -> httpx.Response:
        return norte.post(
            "/panel/onboarding/fuentes",
            data={"csrf": csrf(norte, "/panel/onboarding")},
            files=[
                ("archivos", (name, (ACME / "onboarding" / name).read_bytes(), "application/pdf"))
                for name in names
            ],
            follow_redirects=False,
        )

    # Un PDF con texto y uno escaneado en la misma carga: el primero queda, el segundo se avisa.
    both = upload("garantia.pdf", "garantia_escaneada.pdf")
    assert both.status_code == 303 and "ok=" in both.headers["location"]
    page = norte.get(both.headers["location"]).text
    assert "1 documentos" in page
    assert "garantia_escaneada.pdf es un PDF escaneado" in page
    with tenant_scope(world.conn, tenant_id):
        stored = world.conn.execute(
            "select (select count(*) from documents), (select count(*) from chunks),"
            " (select string_agg(file_path, ',') from sources)"
        ).fetchone()
    with tenant_scope(world.conn, world.stack.seed.tenant_id):
        leaked = world.conn.execute(
            "select count(*) from documents where source = 'garantia'"
        ).fetchone()
    assert stored is not None and stored[0] == 1 and stored[1] >= 1
    assert stored[2] == "garantia.pdf" and leaked == (0,)  # y solo en Tienda Norte

    # Solo el escaneado: la pantalla lo explica, sin error del servidor y sin guardar nada.
    alone = upload("garantia_escaneada.pdf")
    assert alone.status_code == 303
    page = norte.get(alone.headers["location"]).text
    assert "es un PDF escaneado" in page and "Subiste" not in page
    with tenant_scope(world.conn, tenant_id):
        count = world.conn.execute("select count(*) from sources").fetchone()
    assert count == (1,)


# -- alta de la empresa y login --------------------------------------------------------------
def test_el_operador_crea_una_empresa_con_su_propio_usuario(world: World) -> None:
    norte = create_norte(world)
    tenant_id = world.tenant("Tienda Norte")
    assert tenant_id != world.stack.seed.tenant_id
    with tenant_scope(world.conn, tenant_id):
        user = world.conn.execute("select email, role, password_hash from users").fetchone()
        counts = world.conn.execute(
            "select (select count(*) from case_types), (select count(*) from tools),"
            " (select count(*) from documents), (select count(*) from cases)"
        ).fetchone()
    assert user is not None and user[:2] == (NORTE, "owner")
    assert NORTE_PASSWORD not in user[2] and auth.verify_password(NORTE_PASSWORD, user[2])
    assert counts == (0, 0, 0, 0)  # nace vacía: nada de Acme
    assert chat_slug(world.conn, tenant_id) is not None

    home = norte.get("/panel/casos").text
    assert "Tienda Norte" in home and "Acme Store" not in home
    assert "Todavía no hay conversaciones" in home
    assert "Reiniciar demo" not in home and ">Empresas<" not in home


def test_el_alta_valida_los_datos_y_solo_la_hace_el_operador(world: World) -> None:
    norte = create_norte(world)
    admin = login(ADMIN, ADMIN_PASSWORD)
    token = csrf(admin, "/panel/empresas")
    for data, error in (
        ({"nombre": "Tienda Norte", "email": "otra@x.example", "clave": "clave-larga-1"}, "nombre"),
        ({"nombre": "Otra", "email": NORTE, "clave": "clave-larga-1"}, "email"),
        ({"nombre": "Otra", "email": ADMIN, "clave": "clave-larga-1"}, "email"),
        ({"nombre": "Otra", "email": "otra@x.example", "clave": "corta"}, "clave"),
    ):
        response = admin.post("/panel/empresas", data={**data, "csrf": token})
        assert error in response.text.lower(), data
    assert world.conn.execute("select count(*) from tenants where name = 'Otra'").fetchone() == (0,)
    # Un usuario de una empresa no crea empresas ni ve la lista.
    assert norte.get("/panel/empresas").status_code == 403
    denied = post(
        norte, "/panel/empresas", nombre="Intrusa", email="x@x.example", clave="1234567890"
    )
    assert denied.status_code == 403
    assert world.conn.execute("select 1 from tenants where name = 'Intrusa'").fetchone() is None


def test_el_login_no_acepta_otra_clave_ni_una_sesion_adulterada(world: World) -> None:
    create_norte(world)
    client = TestClient(app)
    bad = client.post("/panel/login", data={"email": NORTE, "password": "otra-clave-larga"})
    assert bad.status_code == 401
    # Una sesión firmada para Norte no sirve cambiándole la empresa: la firma no coincide.
    good = auth.new_session(NORTE, tenant_id=world.tenant("Tienda Norte"), company="N", admin=False)
    forged = auth.new_session(
        NORTE, tenant_id=world.stack.seed.tenant_id, company="Acme", admin=False
    )
    mixed = forged.rsplit(".", 1)[0] + "." + good.rsplit(".", 1)[1]
    client.cookies.set(auth.COOKIE, mixed, path="/panel")
    assert client.get("/panel/casos", follow_redirects=False).headers["location"] == "/panel/login"
    # Después de varios intentos fallidos, el login se frena aunque la clave sea la correcta.
    for _ in range(auth.MAX_FAILURES):
        TestClient(app).post("/panel/login", data={"email": NORTE, "password": "mala-clave-1"})
    blocked = TestClient(app).post(
        "/panel/login", data={"email": NORTE, "password": NORTE_PASSWORD}
    )
    assert blocked.status_code == 429


# -- aislamiento entre empresas --------------------------------------------------------------
def test_un_usuario_no_ve_ni_toca_datos_de_otra_empresa(world: World) -> None:
    stack, conn = world.stack, world.conn
    acme = stack.seed.tenant_id
    answered = stack.send("lucia", "¿Abren los domingos?")
    stack.close_open_cases()
    pending = stack.send("martin", "Quiero el reembolso del pedido 1005")
    assert pending.approval_id is not None
    with tenant_scope(conn, acme):
        type_id = conn.execute("select id from case_types where name = 'reembolso'").fetchone()
        tool_id = conn.execute("select id from tools where name = 'iniciar_reembolso'").fetchone()
        conn.execute(
            "insert into onboarding_runs (tenant_id, status, report) values (%s, 'done', %s)",
            (acme, json.dumps({"conversaciones": 7777777, "tipos": [], "adversariales": {}})),
        )
    assert type_id is not None and tool_id is not None

    norte = create_norte(world)
    # Cada pantalla muestra solo lo de Tienda Norte.
    for path in (
        "/panel/casos",
        "/panel/aprobaciones",
        "/panel/onboarding",
        "/panel/reporte",
        "/panel/configuracion",
    ):
        page = norte.get(path)
        assert page.status_code == 200, path
        # Marcas largas: una corta ("77", "1005") puede aparecer por azar dentro del token
        # anti-CSRF o del identificador del chat, que son hexadecimales al azar.
        for leak in ("Abren los domingos", "Reembolso", "Horarios y sucursales", "7777777"):
            assert leak not in page.text, (path, leak)
        assert "pedido 1005" not in page.text.lower(), path
        assert str(answered.case_id) not in page.text

    # Cada endpoint responde 404 a un id de Acme, igual que a uno que no existe, y no cambia nada.
    assert norte.get(f"/panel/casos/{answered.case_id}").status_code == 404
    assert norte.get(f"/panel/casos/{uuid.uuid4()}").status_code == 404
    attempts = [
        post(norte, f"/panel/casos/{answered.case_id}/responder", texto="Hola, soy de Norte"),
        post(norte, f"/panel/aprobaciones/{pending.approval_id}", decision="aprobar"),
        post(norte, f"/panel/aprobaciones/{pending.approval_id}", decision="rechazar"),
        post(norte, f"/panel/configuracion/tipos/{type_id[0]}", activo="no"),
        post(norte, f"/panel/configuracion/tipos/{type_id[0]}", instrucciones="objetivo: x"),
        post(norte, f"/panel/configuracion/herramientas/{tool_id[0]}", aprobada="no"),
        post(
            norte,
            f"/panel/configuracion/herramientas/{tool_id[0]}",
            limites="si",
            monto_maximo="1",
        ),
    ]
    assert [r.status_code for r in attempts] == [404] * len(attempts)
    assert norte.get("/panel/reiniciar").status_code == 403
    assert post(norte, "/panel/reiniciar").status_code == 403
    with tenant_scope(conn, acme):
        state = conn.execute(
            "select (select count(*) from case_messages where role = 'human'),"
            " (select status from approvals where id = %s),"
            " (select active from case_types where id = %s),"
            " (select approved from tools where id = %s),"
            " (select manifest ->> 'max_amount' from tools where id = %s),"
            " (select count(*) from cases where archived_at is not null)",
            (pending.approval_id, type_id[0], tool_id[0], tool_id[0]),
        ).fetchone()
    assert state == (0, "pending", True, True, "50000", 0)
    assert stack.acme_order("1005")["status"] == "delivered"  # el reembolso no se ejecutó

    # Lo que cambia Norte en su configuración no llega a Acme, ni lo que conecta.
    post(norte, "/panel/configuracion/sistema/acceso", url="https://api.norte.example", auth="none")
    assert system.mode_for(conn, acme) == "demo"
    assert system.load(conn, acme).base_url == system.DEMO_BASE_URL  # type: ignore[union-attr]

    # Y al revés: el operador, en Acme, no ve los casos de Tienda Norte.
    slug = chat_slug(conn, world.tenant("Tienda Norte"))
    chat = TestClient(app)
    session = chat.post("/chat/session", params={"canal": slug}).json()["session"]
    chat.post("/chat/messages", json={"session": session, "text": "quiero hablar con una persona"})
    inbox = norte.get("/panel/casos").text
    norte_case = re.findall(r'href="/panel/casos/([0-9a-f-]{36})"', inbox)
    assert len(norte_case) == 1
    admin = login(ADMIN, ADMIN_PASSWORD)
    assert norte_case[0] not in admin.get("/panel/casos").text
    assert admin.get(f"/panel/casos/{norte_case[0]}").status_code == 404
    assert post(admin, f"/panel/casos/{norte_case[0]}/responder", texto="Hola").status_code == 404


def test_el_chat_de_cada_empresa_es_propio(world: World) -> None:
    norte = create_norte(world)
    tenant_id = world.tenant("Tienda Norte")
    slug = chat_slug(world.conn, tenant_id)
    client = TestClient(app)
    page = client.get(f"/c/{slug}")
    assert page.status_code == 200 and "Tienda Norte" in page.text
    for leak in ("Acme", "lucia@example.com", "1001"):
        assert leak not in page.text, leak
    assert client.get("/c/no-existe-123").status_code == 404
    assert client.post("/chat/session", params={"canal": "no-existe-123"}).status_code == 503

    acme_session = client.post("/chat/session").json()["session"]
    norte_session = client.post("/chat/session", params={"canal": slug}).json()["session"]
    for session in (acme_session, norte_session):
        sent = client.post(
            "/chat/messages", json={"session": session, "text": "quiero hablar con una persona"}
        )
        assert "event: message" in sent.text
    with tenant_scope(world.conn, tenant_id):
        mine = world.conn.execute("select count(*) from cases").fetchone()
    assert mine == (1,)  # cada conversación quedó en su empresa
    # El equipo de Norte responde: lo ve la sesión de Norte y no la de Acme.
    case_id = re.findall(r'href="/panel/casos/([0-9a-f-]{36})"', norte.get("/panel/casos").text)[0]
    post(norte, f"/panel/casos/{case_id}/responder", texto="Hola, soy Ana de Tienda Norte.")
    seen = client.get("/chat/updates", params={"session": norte_session}).json()["messages"]
    assert [m["text"] for m in seen] == ["Hola, soy Ana de Tienda Norte."]
    assert client.get("/chat/updates", params={"session": acme_session}).json() == {"messages": []}


# -- onboarding desde el panel ---------------------------------------------------------------
def test_la_empresa_sube_su_historial_corre_el_analisis_y_aprueba_desde_el_panel(
    world: World,
) -> None:
    conn = world.conn
    norte = create_norte(world)
    tenant_id = world.tenant("Tienda Norte")
    acme = world.stack.seed.tenant_id

    # Sin historial no hay análisis.
    early = post(norte, "/panel/onboarding/iniciar")
    assert "error=" in early.headers["location"] and world.kicks == []

    spec = Path(OPENAPI_PATH).read_bytes()
    saved = norte.post(
        "/panel/configuracion/sistema/openapi",
        data={"csrf": csrf(norte)},
        files=[("archivo", ("openapi.json", spec, "application/json"))],
        follow_redirects=False,
    )
    assert "ok=" in saved.headers["location"]

    uploaded = upload_history(norte)
    assert "ok=" in uploaded.headers["location"], uploaded.headers["location"]
    page = norte.get("/panel/onboarding").text
    assert "whatsapp_export.zip" in page and "tickets.csv" in page
    with tenant_scope(conn, tenant_id):
        mine = conn.execute(
            "select (select count(*) from conversations), (select count(*) from documents)"
        ).fetchone()
    with tenant_scope(conn, acme):
        theirs = conn.execute("select count(*) from conversations").fetchone()
    assert mine == (100, 11) and theirs == (0,)  # el historial quedó solo en Tienda Norte
    again = upload_history(norte)  # subir lo mismo no duplica
    assert "ya+estaban" in again.headers["location"] or "ya%20estaban" in again.headers["location"]

    # "Analizar" encola el job del pipeline de esta empresa y avisa al worker.
    started = post(norte, "/panel/onboarding/iniciar")
    assert "ok=" in started.headers["location"] and world.kicks == ["kick"]
    post(norte, "/panel/onboarding/iniciar")  # dos clics: sigue habiendo un solo job
    jobs = conn.execute(
        "select id, kind, payload, attempts, max_attempts from jobs where kind = %s"
        " and payload ->> 'tenant_id' = %s",
        (onboarding.JOB_KIND, str(tenant_id)),
    ).fetchall()
    assert len(jobs) == 1
    assert "El análisis está corriendo" in norte.get("/panel/onboarding").text

    # El worker corre el job (acá, en la transacción del test y con el modelo determinístico).
    model = FakeOnboardingModel()

    def deps_factory(c: psycopg.Connection, tenant: uuid.UUID) -> Any:
        deps = pipeline_deps(c, model)
        found = system.load(c, tenant)
        assert found is not None and found.openapi is not None
        deps.openapi = found.openapi
        deps.business = onboarding.business_names(c, tenant)
        return deps

    job = Job(*jobs[0])
    onboarding.run_onboarding_job(conn, job, deps_factory, commit=False)
    conn.execute("update jobs set status = 'done' where id = %s", (job.id,))

    done = norte.get("/panel/onboarding").text
    assert done.count(">Hecha<") == 6 and "El análisis terminó" in done
    report = norte.get("/panel/reporte").text
    assert "100" in report and "conversaciones analizadas" in report and "Tienda Norte" in report

    # Lo descubierto queda sin activar ni aprobar hasta que la empresa lo revisa.
    with tenant_scope(conn, tenant_id):
        types = conn.execute(
            "select id, name, active from case_types order by volume desc"
        ).fetchall()
        tools = conn.execute("select id, name, approved from tools").fetchall()
    assert len(types) >= 7 and not any(active for _, _, active in types)
    assert [(name, approved) for _, name, approved in tools] == [("consultar_pedido", False)]
    settings = norte.get("/panel/configuracion").text
    assert "Consulta de pedido" in settings and "Sin aprobar" in settings
    assert "Usa herramientas sin aprobar" in settings

    # Aprobar pasa al agente en vivo de esa empresa (y solo de esa).
    type_id, type_name, _ = types[0]
    assert post(norte, f"/panel/configuracion/tipos/{type_id}", activo="si").status_code == 303
    tool_id = tools[0][0]
    assert (
        post(norte, f"/panel/configuracion/herramientas/{tool_id}", aprobada="si").status_code
        == 303
    )
    agent = world.agent(conn, tenant_id)
    assert list(agent._active_case_types(tenant_id)) == [type_name]
    assert [m["name"] for m in agent._guardian.manifests(tenant_id, ["consultar_pedido"])] == [
        "consultar_pedido"
    ]
    with tenant_scope(conn, acme):
        acme_types = conn.execute("select count(*) from case_types where active").fetchone()
    assert acme_types == (8,)  # Acme sigue con los suyos

    # Editar las instrucciones y los límites desde la revisión.
    edited = post(
        norte,
        f"/panel/configuracion/tipos/{type_id}",
        instrucciones=(
            "nombre: x\nobjetivo: Informar el estado del pedido\nherramientas: [consultar_pedido]"
        ),
    )
    assert "ok=" in edited.headers["location"]
    broken = post(norte, f"/panel/configuracion/tipos/{type_id}", instrucciones="sin objetivo")
    assert "error=" in broken.headers["location"]
    assert agent._active_case_types(tenant_id)[type_name][0] == "Informar el estado del pedido"

    # El chat de la empresa usa sus documentos: una consulta general se responde con su RAG.
    slug = chat_slug(conn, tenant_id)
    chat = TestClient(app)
    session = chat.post("/chat/session", params={"canal": slug}).json()["session"]
    answer = chat.post(
        "/chat/messages", json={"session": session, "text": "¿Cuánto tarda el envío?"}
    ).text
    assert "event: message" in answer and "event: error" not in answer
    with tenant_scope(conn, tenant_id):
        searched = conn.execute(
            "select payload -> 'sources' from audit_events where kind = 'rag_search'"
        ).fetchone()
    assert searched is not None and searched[0]  # encontró documentos de Tienda Norte


# -- conexión con el sistema de la empresa ---------------------------------------------------
def test_la_empresa_conecta_su_sistema_con_credenciales_cifradas_y_prueba_antes_de_activar(
    world: World,
) -> None:
    conn = world.conn
    norte = create_norte(world)
    tenant_id = world.tenant("Tienda Norte")
    assert system.mode_for(conn, tenant_id) == "sin_sistema"
    norte.post(
        "/panel/configuracion/sistema/openapi",
        data={"csrf": csrf(norte), "texto": Path(OPENAPI_PATH).read_text(encoding="utf-8")},
        files=[("archivo", ("", b"", "application/octet-stream"))],
    )
    assert system.mode_for(conn, tenant_id) == "simulado"

    # Solo HTTPS y direcciones públicas: el servidor no hace pedidos a la red interna.
    for url in (
        "http://api.norte.example",
        "https://127.0.0.1",
        "https://10.0.0.8",
        "https://localhost",
    ):
        bad = post(norte, "/panel/configuracion/sistema/acceso", url=url, auth="none")
        assert "error=" in bad.headers["location"], url
    saved = post(
        norte,
        "/panel/configuracion/sistema/acceso",
        url="https://api.norte.example/v1/",
        auth="bearer",
        credencial=SECRET,
    )
    assert "ok=" in saved.headers["location"]

    # La credencial queda cifrada con la clave de esta empresa y no se vuelve a mostrar.
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select base_url, credentials_enc, status from system_connections"
        ).fetchone()
    assert row is not None and row[0] == "https://api.norte.example/v1" and row[2] == "draft"
    assert SECRET not in row[1] and decrypt_for(tenant_id, row[1]) == SECRET
    with pytest.raises(InvalidToken):
        decrypt_for(world.stack.seed.tenant_id, row[1])
    assert SECRET not in norte.get("/panel/configuracion").text

    # Sin la prueba aprobada no se activa.
    early = post(norte, "/panel/configuracion/sistema/activar")
    assert "error=" in early.headers["location"]
    assert system.mode_for(conn, tenant_id) == "simulado"

    world.upstream_status = 401  # el sistema rechaza las credenciales
    failed = post(norte, "/panel/configuracion/sistema/probar")
    assert "error=" in failed.headers["location"] and system.load(conn, tenant_id).status == "draft"  # type: ignore[union-attr]

    world.upstream_status = 200
    passed = post(norte, "/panel/configuracion/sistema/probar")
    assert "ok=" in passed.headers["location"]
    probe = world.upstream[-1]
    assert probe.method == "GET" and probe.url.path == "/v1/customers"  # solo lectura
    assert probe.headers["authorization"] == f"Bearer {SECRET}"
    assert "La prueba pasó" in norte.get("/panel/configuracion").text

    activated = post(norte, "/panel/configuracion/sistema/activar")
    assert "ok=" in activated.headers["location"]
    assert system.mode_for(conn, tenant_id) == "real"
    # El agente de Norte ya llama a su sistema, con sus credenciales, a través del runtime.
    runtime, mode = system.runtime_for(conn, tenant_id, client_factory=world.client_factory)
    runtime.call("GET", "/orders/{order_id}", {"order_id": "77"})
    assert mode == "real" and world.upstream[-1].url.path == "/v1/orders/77"
    assert world.upstream[-1].headers["authorization"] == f"Bearer {SECRET}"

    # Cambiar el acceso obliga a probar de nuevo: vuelve al modo simulado.
    post(
        norte,
        "/panel/configuracion/sistema/acceso",
        url="https://api2.norte.example",
        auth="bearer",
    )
    assert system.load(conn, tenant_id).status == "draft"  # type: ignore[union-attr]
    assert system.mode_for(conn, tenant_id) == "simulado"
    with tenant_scope(conn, tenant_id):
        kept = conn.execute("select credentials_enc from system_connections").fetchone()
    assert kept is not None and decrypt_for(tenant_id, kept[0]) == SECRET  # se conserva


def test_en_modo_simulado_las_herramientas_no_salen_a_la_red(world: World) -> None:
    create_norte(world)
    tenant_id = world.tenant("Tienda Norte")
    system.save_openapi(world.conn, tenant_id, Path(OPENAPI_PATH).read_text(encoding="utf-8"))
    system.save_access(
        world.conn, tenant_id, base_url="https://api.norte.example", auth_kind="none"
    )
    runtime, mode = system.runtime_for(world.conn, tenant_id, client_factory=world.client_factory)
    result = runtime.call("GET", "/orders/{order_id}", {"order_id": "55"})
    assert mode == "simulado" and result.ok and result.data["id"] == "55"
    assert world.upstream == []
    # La API simulada de Acme es solo de la demo: otra empresa nunca la recibe.
    other, _ = system.runtime_for(world.conn, tenant_id, demo=lambda: world.stack.acme)
    assert type(other).__name__ == "SandboxRuntime"


def test_un_dominio_que_resuelve_a_una_direccion_interna_se_rechaza() -> None:
    system.check_public_host("https://api.norte.example", resolver=lambda host: ["93.184.216.34"])
    with pytest.raises(system.ConnectionError_):
        system.check_public_host("https://api.norte.example", resolver=lambda host: ["10.0.0.5"])
    with pytest.raises(system.ConnectionError_):
        system.check_public_host(
            "https://api.norte.example", resolver=lambda host: ["93.184.216.34", "169.254.169.254"]
        )


def test_el_formulario_con_archivos_conserva_los_bytes() -> None:
    binary = bytes(range(256)) * 4 + b"\r\n--no-es-el-limite\r\n"
    boundary = "----limite123"
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="csrf"\r\n\r\nabc\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="archivos"; '
            f'filename="C:\\datos\\chat.zip"\r\nContent-Type: application/zip\r\n\r\n'
        ).encode()
        + binary
        + f"\r\n--{boundary}--\r\n".encode()
    )
    fields, files = parse_multipart(f"multipart/form-data; boundary={boundary}", body)
    assert fields == {"csrf": "abc"}
    assert [(f.name, f.data) for f in files] == [("chat.zip", binary)]
