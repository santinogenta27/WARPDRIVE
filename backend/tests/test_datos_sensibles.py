"""Hito 7, tarea 5 — test de aceptación: datos sensibles en los documentos del onboarding
(decisión 035).

En un documento (PDF, DOCX o Markdown) los números de tarjeta y los DNI se reemplazan antes de
guardar. Los emails, teléfonos y CUIT de la empresa no se tocan: son parte de lo que el
asistente responde. Si el documento parece tener datos de varias personas, no se indexa hasta
que la empresa lo confirma en la pantalla.
"""

import importlib.util
import io
import re
import uuid
import zipfile
from pathlib import Path

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.voyage import voyage_transport
from test_empresas import (  # noqa: F401 — `world` es la fixture de test_empresas
    ADMIN,
    ADMIN_PASSWORD,
    World,
    create_norte,
    csrf,
    login,
    world,
)

from app.db.connection import tenant_scope
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.onboarding import sensitive
from app.onboarding.ingest import Upload, ingest

ONBOARDING = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "acme" / "onboarding"
CARD = "4111 1111 1111 1111"  # número de prueba de Visa: pasa el control de Luhn
CARD_2 = "5500-0000-0000-0004"
POLICY = f"""# Devoluciones

Para devolver un producto escribinos a ayuda@acmestore.example o llamá al 011 4555-0100.
Acme Store S.A., CUIT 30-71234567-8. El reintegro máximo es de $ 12.500.000 por compra.

Ejemplo de un reclamo que recibimos: "Soy Marta, DNI 28.456.789, pagué con la tarjeta
{CARD} y no me llegó el pedido 1001". Otro cliente con dni 30111222 pagó con {CARD_2}.
El mismo DNI 28.456.789 aparece dos veces.
"""
# Una planilla de clientes subida por error como documento.
CUSTOMERS = """# Clientes de noviembre

Marta Gómez, marta.gomez@example.com, 011 4555-0101, pedido 1001.
Raúl Paz, raul.paz@example.com, 11 5555-0102, pedido 1002.
Inés Luna, ines.luna@example.com, +54 9 11 4555-0103, pedido 1003.
Omar Ríos, omar.rios@example.com, 351 455-0104, pedido 1004.
"""


def embedder(conn: psycopg.Connection) -> VoyageEmbedder:
    return VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )


def tenant(conn: psycopg.Connection) -> uuid.UUID:
    row = conn.execute("insert into tenants (name) values ('Sensibles') returning id").fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    return tenant_id


def docx(text: str) -> bytes:
    paragraphs = "".join(
        f"<w:p><w:r><w:t>{line.removeprefix('# ')}</w:t></w:r></w:p>"
        for line in text.splitlines()
        if line.strip()
    )
    body = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{paragraphs}</w:body></w:document>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr("word/document.xml", body)
    return buffer.getvalue()


def pdf(text: str) -> bytes:
    spec = importlib.util.spec_from_file_location("generate_pdf", ONBOARDING / "generate_pdf.py")
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    data: bytes = generator.text_pdf([text.replace("# ", "").splitlines()])
    return data


def stored(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute("select text from chunks order by id").fetchall()
        titles = conn.execute("select title from documents").fetchall()
    return "\n".join(r[0] for r in rows + titles)


# -- reemplazo de tarjetas y DNI -------------------------------------------------------------
def test_reemplaza_tarjetas_y_dni_sin_tocar_los_datos_de_la_empresa() -> None:
    clean, found = sensitive.redact(POLICY)
    assert CARD not in clean and CARD_2 not in clean
    assert "28.456.789" not in clean and "30111222" not in clean
    assert found == {"tarjetas": 2, "dni": 2}  # el DNI repetido cuenta una vez
    assert clean.count("<DNI_1>") == 2 and "<DNI_2>" in clean
    assert "<CARD_1>" in clean and "<CARD_2>" in clean
    # Lo de la empresa queda igual: email, teléfono, CUIT, montos y números de pedido.
    for kept in ("ayuda@acmestore.example", "011 4555-0100", "30-71234567-8", "$ 12.500.000"):
        assert kept in clean, kept
    assert "pedido 1001" in clean and "Soy Marta" in clean


@pytest.mark.parametrize(
    "text",
    [
        "El tope es de $ 28.456.789 por año.",  # un monto, no un DNI
        "Facturamos 12.500.000 pesos en 2025.",
        "Código de seguimiento 4111 1111 1111 1112.",  # no pasa el control de Luhn
        "Llamanos al 0800 555 0100 o al +54 9 11 4555-0100.",
        "CUIT 30-71234567-8, IIBB 901-123456-7.",
        "Pedido 10012345 despachado.",  # 8 dígitos sin la palabra DNI
    ],
)
def test_no_reemplaza_lo_que_no_es_una_tarjeta_ni_un_dni(text: str) -> None:
    assert sensitive.redact(text) == (text, {"tarjetas": 0, "dni": 0})


def test_detecta_un_documento_con_datos_de_varias_personas() -> None:
    assert sensitive.personal_data(POLICY) is None  # un email y un teléfono: es la empresa
    reason = sensitive.personal_data(CUSTOMERS)
    assert reason is not None and "4 emails" in reason and "4 teléfonos" in reason
    # El mismo email o teléfono repetido no suma.
    repeated = "Escribí a ayuda@acme.example. " * 5 + "Tel 011 4555-0100. " * 5
    assert sensitive.personal_data(repeated) is None
    two = "ventas@acme.example, soporte@acme.example, 011 4555-0100 y 011 4555-0199"
    assert sensitive.personal_data(two) is None  # dos casillas y dos líneas: una empresa normal


@pytest.mark.parametrize("kind", ["md", "docx", "pdf"])
def test_el_documento_se_guarda_sin_tarjetas_ni_dni(conn: psycopg.Connection, kind: str) -> None:
    tenant_id = tenant(conn)
    data = {"md": POLICY.encode(), "docx": docx(POLICY), "pdf": pdf(POLICY)}[kind]
    result = ingest(
        conn,
        tenant_id,
        [Upload(f"devoluciones.{kind}", data)],
        business={"Acme Store"},
        embedder=embedder(conn),
    )
    assert result.documents == 1 and result.held == ()
    text = stored(conn, tenant_id)
    assert text and "<CARD_1>" in text and "<DNI_1>" in text
    digits = re.sub(r"\D", "", text)
    for secret in (CARD, CARD_2, "28.456.789", "30111222"):
        assert re.sub(r"\D", "", secret) not in digits, secret
    assert "ayuda@acmestore.example" in text and "30-71234567-8" in text  # lo de la empresa


def test_por_linea_de_comandos_el_documento_se_indexa_sin_frenar(
    conn: psycopg.Connection,
) -> None:
    """El pipeline por comando no tiene pantalla donde confirmar: indexa, ya sin tarjetas ni DNI."""
    tenant_id = tenant(conn)
    result = ingest(
        conn,
        tenant_id,
        [Upload("clientes.md", CUSTOMERS.encode())],
        business={"Acme Store"},
        embedder=embedder(conn),
    )
    assert result.documents == 1 and result.held == ()


# -- confirmación en la pantalla -------------------------------------------------------------
def upload(norte: TestClient, *files: tuple[str, bytes]) -> httpx.Response:
    return norte.post(
        "/panel/onboarding/fuentes",
        data={"csrf": csrf(norte, "/panel/knowledge")},
        files=[("archivos", (name, data, "application/octet-stream")) for name, data in files],
        follow_redirects=False,
    )


def counts(world: World, tenant_id: uuid.UUID) -> tuple[int, int, int]:  # noqa: F811
    with tenant_scope(world.conn, tenant_id):
        row = world.conn.execute(
            "select (select count(*) from documents), (select count(*) from sources),"
            " (select count(*) from pending_documents)"
        ).fetchone()
    assert row is not None
    return row[0], row[1], row[2]


def pending_id(norte: TestClient) -> str:
    match = re.search(
        r"/panel/onboarding/documentos/([0-9a-f-]{36})/indexar", norte.get("/panel/knowledge").text
    )
    assert match is not None
    return match.group(1)


def test_un_documento_con_datos_de_personas_espera_la_confirmacion(world: World) -> None:  # noqa: F811
    norte = create_norte(world)
    tenant_id = world.tenant("Tienda Norte")

    sent = upload(norte, ("clientes.md", CUSTOMERS.encode()), ("devoluciones.md", POLICY.encode()))
    assert sent.status_code == 303
    page = norte.get(sent.headers["location"]).text
    # La política se indexó; la planilla quedó esperando, y la pantalla dice por qué.
    assert counts(world, tenant_id) == (1, 1, 1)
    assert "clientes.md" in page and "4 emails" in page and "datos de personas" in page
    assert "marta.gomez@example.com" not in stored(world.conn, tenant_id)
    # Sigue avisando al volver a entrar, y no muestra los datos en la pantalla.
    again = norte.get("/panel/knowledge").text
    assert "Indexar igual" in again and "Descartar" in again
    assert "marta.gomez@example.com" not in again
    # Subirlo de nuevo no lo duplica.
    upload(norte, ("clientes.md", CUSTOMERS.encode()))
    assert counts(world, tenant_id) == (1, 1, 1)

    # Otra empresa no puede confirmarlo ni descartarlo: para ella no existe.
    document_id = pending_id(norte)
    admin = login(ADMIN, ADMIN_PASSWORD)
    for action in ("indexar", "descartar"):
        other = admin.post(
            f"/panel/onboarding/documentos/{document_id}/{action}",
            data={"csrf": csrf(admin, "/panel/knowledge")},
        )
        assert other.status_code == 404
    # Sin el token anti-CSRF tampoco.
    forged = norte.post(f"/panel/onboarding/documentos/{document_id}/indexar")
    assert forged.status_code == 403
    assert counts(world, tenant_id) == (1, 1, 1)

    # La empresa confirma: recién ahí se indexa.
    confirmed = norte.post(
        f"/panel/onboarding/documentos/{document_id}/indexar",
        data={"csrf": csrf(norte, "/panel/knowledge")},
        follow_redirects=False,
    )
    assert confirmed.status_code == 303 and "ok=" in confirmed.headers["location"]
    assert counts(world, tenant_id) == (2, 2, 0)
    assert "marta.gomez@example.com" in stored(world.conn, tenant_id)
    assert "Indexar igual" not in norte.get("/panel/knowledge").text
    # Ya indexado, subirlo otra vez no hace nada.
    upload(norte, ("clientes.md", CUSTOMERS.encode()))
    assert counts(world, tenant_id) == (2, 2, 0)


def test_un_documento_descartado_no_deja_nada_guardado(world: World) -> None:  # noqa: F811
    norte = create_norte(world)
    tenant_id = world.tenant("Tienda Norte")
    with_card = CUSTOMERS + f"\nMarta pagó con la tarjeta {CARD}, DNI 28.456.789.\n"
    upload(norte, ("clientes.md", with_card.encode()))
    assert counts(world, tenant_id) == (0, 0, 1)
    # Lo que espera la confirmación ya está sin tarjetas ni DNI.
    with tenant_scope(world.conn, tenant_id):
        held = world.conn.execute("select text from pending_documents").fetchone()
    assert held is not None and "<CARD_1>" in held[0] and "4111" not in held[0]
    assert "28.456.789" not in held[0]

    discarded = norte.post(
        f"/panel/onboarding/documentos/{pending_id(norte)}/descartar",
        data={"csrf": csrf(norte, "/panel/knowledge")},
        follow_redirects=False,
    )
    assert discarded.status_code == 303 and "ok=" in discarded.headers["location"]
    assert counts(world, tenant_id) == (0, 0, 0)
    assert stored(world.conn, tenant_id) == ""
    # Descartado, se puede volver a subir (por ejemplo, ya corregido).
    upload(norte, ("clientes.md", with_card.encode()))
    assert counts(world, tenant_id) == (0, 0, 1)
