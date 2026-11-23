"""Hito 9, tarea 3 — test de aceptación: carga de conocimiento desde el panel (decisión 050).

- Un CSV que no es un export de tickets (un catálogo, una tabla de envíos) se indexa como
  documento; un export de tickets sigue siendo historial.
- Las páginas del sitio de la empresa se rastrean sin salir de su dominio, con un máximo de
  páginas, y nunca contra una dirección interna.
- Subir de nuevo un archivo con el mismo nombre y otro contenido lo reindexa (reemplaza al
  anterior); borrarlo lo saca del RAG, y después se puede volver a subir.
- El panel lista los documentos indexados y los borra.
"""

import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import build_simulated_stack
from simulated.voyage import voyage_transport

from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.main import app
from app.onboarding import ingest as ingestion
from app.onboarding.ingest import Upload, detect_kind, ingest
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

CATALOG = b"sku,producto,precio\nCP-1,Campera Polar,45000\nRE-2,Remera Lisa,12000\n"
NORTE, NORTE_PASSWORD = "ana@tiendanorte.example", "clave-de-tienda-norte"


def _embedder(conn: psycopg.Connection) -> VoyageEmbedder:
    return VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )


def _tenant(conn: psycopg.Connection) -> uuid.UUID:
    row = conn.execute("insert into tenants (name) values ('Documentos') returning id").fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    return tenant_id


def _chunks(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute("select text from chunks order by text").fetchall()
    return "\n".join(r[0] for r in rows)


def test_un_csv_que_no_es_de_tickets_se_indexa_como_documento(conn: psycopg.Connection) -> None:
    assert detect_kind(Upload("catalogo.csv", CATALOG)) == "document"
    document = ingestion.read_document(CATALOG, "catalogo.csv")
    assert "producto: Campera Polar" in document.text and "precio: 45000" in document.text
    tenant = _tenant(conn)
    result = ingest(
        conn, tenant, [Upload("catalogo.csv", CATALOG)], business=set(), embedder=_embedder(conn)
    )
    assert result.documents == 1
    assert "Campera Polar" in _chunks(conn, tenant)


def test_reemplazar_un_archivo_lo_reindexa_y_borrarlo_lo_saca_del_rag(
    conn: psycopg.Connection,
) -> None:
    tenant, embedder = _tenant(conn), _embedder(conn)
    first = Upload("garantia.md", "# Garantía\n\nLa garantía dura 6 meses.".encode())
    second = Upload("garantia.md", "# Garantía\n\nLa garantía dura 12 meses.".encode())
    ingest(conn, tenant, [first], business=set(), embedder=embedder)
    result = ingest(conn, tenant, [second], business=set(), embedder=embedder)
    assert result.replaced == 1
    listed = ingestion.indexed_documents(conn, tenant)
    assert [(d.title, d.source) for d in listed] == [("Garantía", "garantia")]
    text = _chunks(conn, tenant)
    assert "12 meses" in text and "6 meses" not in text

    assert ingestion.delete_document(conn, tenant, listed[0].id)
    assert _chunks(conn, tenant) == "" and ingestion.indexed_documents(conn, tenant) == []
    again = ingest(conn, tenant, [second], business=set(), embedder=embedder)
    assert again.documents == 1 and again.skipped == 0  # se puede volver a subir


def _site() -> httpx.Client:
    pages = {
        "/": '<title>Tienda</title><a href="/envios">Envíos</a> <a href="/cambios#x">Cambios</a>'
        ' <a href="https://otro.example/x">Afuera</a> <a href="/logo.png">Logo</a>'
        " <p>Bienvenidos a la tienda.</p>",
        "/envios": "<title>Envíos</title><p>Enviamos en 48 horas.</p><a href='/'>Inicio</a>",
        "/cambios": "<title>Cambios</title><p>Tenés 30 días para cambiar.</p>",
    }
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert request.url.host == "tienda.example", request.url
        body = pages.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    client.seen = seen  # type: ignore[attr-defined]
    return client


def test_rastrea_las_paginas_del_sitio_sin_salir_del_dominio(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ALLOW_PRIVATE_SYSTEM_URLS", "1")  # el dominio de prueba no resuelve
    pages = ingestion.crawl("https://tienda.example/", _site(), max_pages=10)
    assert sorted(p.title for p in pages) == ["Cambios", "Envíos", "Tienda"]
    assert len(ingestion.crawl("https://tienda.example/", _site(), max_pages=2)) == 2

    tenant = _tenant(conn)
    result = ingest(
        conn,
        tenant,
        [Upload("https://tienda.example/", b"")],
        business=set(),
        embedder=_embedder(conn),
        http=_site(),
    )
    assert result.documents == 3 and "48 horas" in _chunks(conn, tenant)
    again = ingest(
        conn,
        tenant,
        [Upload("https://tienda.example/", b"")],
        business=set(),
        embedder=_embedder(conn),
        http=_site(),
    )
    assert again.documents == 3 and again.replaced == 3  # volver a rastrear actualiza


def test_una_direccion_interna_no_se_rastrea(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ALLOW_PRIVATE_SYSTEM_URLS", raising=False)
    result = ingest(
        conn,
        _tenant(conn),
        [Upload("http://127.0.0.1/admin", b"")],
        business=set(),
        embedder=_embedder(conn),
    )
    assert result.documents == 0 and result.unreadable
    assert "127.0.0.1" in result.unreadable[0]


# -- el panel ---------------------------------------------------------------------------------
@pytest.fixture
def norte(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[uuid.UUID]:
    monkeypatch.setenv("PANEL_EMAIL", "operador@plataforma.example")
    monkeypatch.setenv("PANEL_PASSWORD", "clave-del-operador")
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Tienda Norte", NORTE, NORTE_PASSWORD)
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


def test_el_panel_lista_y_borra_documentos_indexados(
    conn: psycopg.Connection, norte: uuid.UUID
) -> None:
    client = TestClient(app)
    client.post("/panel/login", data={"email": NORTE, "password": NORTE_PASSWORD})

    def csrf() -> str:
        found = re.search(r'name="csrf" value="([^"]+)"', client.get("/panel/knowledge").text)
        assert found is not None
        return found.group(1)

    response = client.post(
        "/panel/onboarding/fuentes",
        data={"csrf": csrf()},
        files=[("archivos", ("catalogo.csv", CATALOG, "text/csv"))],
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    page = client.get("/panel/knowledge").text
    assert "Documentos indexados" in page and "catalogo" in page
    document = ingestion.indexed_documents(conn, norte)[0]
    response = client.post(
        f"/panel/onboarding/indexados/{document.id}/borrar",
        data={"csrf": csrf()},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert ingestion.indexed_documents(conn, norte) == []
    assert "Sitio web" in client.get("/panel/knowledge").text
