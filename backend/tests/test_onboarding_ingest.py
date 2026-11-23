"""Tarea 14 — test de aceptación: 3 formatos de ejemplo producen el mismo esquema normalizado.

La ingesta acepta exportaciones de WhatsApp (.txt o .zip), tickets de un helpdesk (CSV o JSON) y
documentos (Markdown, DOCX, PDF, URL). Las conversaciones se normalizan a mensajes con rol y fecha y
se guardan anonimizadas; los documentos van al RAG. Subir dos veces el mismo archivo no duplica.
"""

import csv
import importlib.util
import io
import json
import uuid
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
import psycopg
import pypdf
import pytest
from simulated.voyage import voyage_transport

from app.db.connection import tenant_scope
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.onboarding.ingest import (
    UnreadableDocument,
    Upload,
    detect_kind,
    fetch_url,
    ingest,
    parse_helpdesk_csv,
    parse_helpdesk_json,
    parse_whatsapp,
    read_document,
)

ONBOARDING = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "acme" / "onboarding"
TURNS = [
    ("customer", "Ana Ruiz", "hola, donde esta mi pedido 1203?", "2026-08-03T10:15"),
    ("agent", "Acme Store", "Hola Ana! Llega el 12/8.", "2026-08-03T10:17"),
    ("customer", "Ana Ruiz", "gracias", "2026-08-03T10:19"),
]


def whatsapp_txt() -> bytes:
    lines = []
    for _, author, text, when in TURNS:
        moment = datetime.fromisoformat(when)
        lines.append(f"{moment.day}/{moment.month}/{moment:%y}, {moment:%H:%M} - {author}: {text}")
    return ("\n".join(lines) + "\n").encode()


def tickets_csv() -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["ticket_id", "created_at", "author_type", "author_name", "body"])
    for role, author, text, when in TURNS:
        writer.writerow(["T-1", when, "end_user" if role == "customer" else "agent", author, text])
    return buffer.getvalue().encode()


def tickets_json() -> bytes:
    ticket = {
        "id": "T-1",
        "requester": {"name": "Ana Ruiz", "email": "ana@example.com"},
        "comments": [
            {"author_type": "end_user" if r == "customer" else "agent", "body": t, "created_at": w}
            for r, _, t, w in TURNS
        ],
    }
    return json.dumps([ticket]).encode()


def test_los_tres_formatos_producen_el_mismo_esquema_normalizado() -> None:
    by_whatsapp = parse_whatsapp(whatsapp_txt(), "Chat con Ana.txt", business={"Acme Store"})
    by_csv = parse_helpdesk_csv(tickets_csv())
    by_json = parse_helpdesk_json(tickets_json())
    normalized = [conversations[0].messages for conversations in (by_whatsapp, by_csv, by_json)]
    assert normalized[0] == normalized[1] == normalized[2]
    roles = [(m.role, m.text, m.sent_at) for m in normalized[0]]
    assert roles[0] == (
        "customer",
        "hola, donde esta mi pedido 1203?",
        datetime(2026, 8, 3, 10, 15),
    )
    assert [m.role for m in normalized[0]] == ["customer", "agent", "customer"]
    for conversations in (by_whatsapp, by_csv, by_json):
        assert "Ana Ruiz" in conversations[0].participants


def test_lee_whatsapp_de_ios_con_mensajes_de_varias_lineas_y_un_zip() -> None:
    ios = (
        "[3/8/26, 10:15:02] Ana Ruiz: hola\n"
        "segunda línea del mismo mensaje\n"
        "[3/8/26, 10:16:40] Acme Store: Hola Ana!\n"
        "[3/8/26, 10:17:00] Acme Store: <Multimedia omitido>\n"
    ).encode()
    conversation = parse_whatsapp(ios, "chat.txt", business={"Acme Store"})[0]
    assert conversation.messages[0].text == "hola\nsegunda línea del mismo mensaje"
    assert len(conversation.messages) == 2  # los avisos de multimedia no son mensajes
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as z:
        z.writestr("a.txt", whatsapp_txt())
        z.writestr("b.txt", ios)
    assert len(parse_whatsapp(bundle.getvalue(), "export.zip", business={"Acme Store"})) == 2


def test_lee_documentos_markdown_docx_y_url() -> None:
    markdown = read_document("# Envíos\n\nHacemos envíos a todo el país.".encode(), "envios.md")
    assert markdown.title == "Envíos" and "todo el país" in markdown.text
    docx = io.BytesIO()
    body = (
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>Cambios</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Tenés 30 días.</w:t></w:r></w:p></w:body></w:document>"
    )
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("word/document.xml", body)
    document = read_document(docx.getvalue(), "cambios.docx")
    assert document.text == "Cambios\n\nTenés 30 días." and document.source == "cambios"

    html = "<html><head><title>FAQ</title><script>x()</script></head><body><h1>FAQ</h1>"
    html += "<p>Abrimos de 10 a 20.</p></body></html>"
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=html)))
    page = fetch_url("https://acme.example/faq", client)
    assert page.title == "FAQ" and "Abrimos de 10 a 20." in page.text and "x()" not in page.text


def test_detecta_el_tipo_de_cada_archivo() -> None:
    assert detect_kind(Upload("export.zip", b"PK")) == "whatsapp_export"
    assert detect_kind(Upload("chat.txt", whatsapp_txt())) == "whatsapp_export"
    assert detect_kind(Upload("tickets.csv", tickets_csv())) == "helpdesk_export"
    assert detect_kind(Upload("tickets.json", tickets_json())) == "helpdesk_export"
    assert detect_kind(Upload("politica.md", b"# x")) == "document"
    assert detect_kind(Upload("Garantia.PDF", b"%PDF-1.4")) == "document"
    assert detect_kind(Upload("https://acme.example/faq", b"")) == "url"


def test_guarda_conversaciones_anonimizadas_e_indexa_documentos_sin_duplicar(
    conn: psycopg.Connection,
) -> None:
    row = conn.execute("insert into tenants (name) values ('Onboarding') returning id").fetchone()
    assert row is not None
    tenant_id = row[0]
    embedder = VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )
    uploads = [
        Upload("whatsapp_export.zip", (ONBOARDING / "whatsapp_export.zip").read_bytes()),
        Upload("tickets.csv", (ONBOARDING / "tickets.csv").read_bytes()),
        Upload("tickets.json", (ONBOARDING / "tickets.json").read_bytes()),
        Upload("envios.md", "# Envíos\n\nHacemos envíos a todo el país.".encode()),
    ]
    first = ingest(conn, tenant_id, uploads, business={"Acme Store"}, embedder=embedder)
    assert first.conversations == 100 and first.documents == 1
    again = ingest(conn, tenant_id, uploads, business={"Acme Store"}, embedder=embedder)
    assert again.conversations == 0 and again.skipped == 4

    seeded = json.loads((ONBOARDING / "seeded_pii.json").read_text(encoding="utf-8"))
    with tenant_scope(conn, tenant_id):
        texts = [r[0] for r in conn.execute("select text from messages").fetchall()]
        kinds = sorted(r[0] for r in conn.execute("select kind from sources").fetchall())
    assert len(texts) > 300
    assert kinds == ["document", "helpdesk_export", "helpdesk_export", "whatsapp_export"]
    stored = "\n".join(texts)
    assert [value for value in seeded if value in stored] == []  # nada en claro en la base


# -- PDF (decisión 030) ----------------------------------------------------------------------
PDF = (ONBOARDING / "garantia.pdf").read_bytes()
SCANNED_PDF = (ONBOARDING / "garantia_escaneada.pdf").read_bytes()


def _embedder(conn: psycopg.Connection) -> VoyageEmbedder:
    return VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )


def _tenant(conn: psycopg.Connection) -> uuid.UUID:
    row = conn.execute("insert into tenants (name) values ('Con PDF') returning id").fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    return tenant_id


def test_los_pdf_de_acme_salen_del_generador() -> None:
    """Los PDF de prueba se generan con `generate_pdf.py`: no son archivos de un cliente."""
    spec = importlib.util.spec_from_file_location("generate_pdf", ONBOARDING / "generate_pdf.py")
    assert spec is not None and spec.loader is not None
    generator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(generator)
    assert generator.text_pdf() == PDF and generator.scanned_pdf() == SCANNED_PDF


def test_lee_un_pdf_con_texto() -> None:
    document = read_document(PDF, "garantia.pdf")
    assert document.title == "Garantía de productos" and document.source == "garantia"
    assert "6 meses de garantía por fallas de fabricación" in document.text  # con tildes
    assert "ayuda@acmestore.example" in document.text
    # El título queda como título y cada párrafo, aparte (decisión 088).
    blocks = document.text.split("\n\n")
    assert blocks[0] == "# Garantía de productos" and len(blocks) >= 3
    assert any("3 meses de garantía" in block for block in blocks[2:])


def test_un_pdf_escaneado_avisa_que_no_tiene_texto() -> None:
    with pytest.raises(UnreadableDocument) as error:
        read_document(SCANNED_PDF, "garantia_escaneada.pdf")
    assert "garantia_escaneada.pdf" in str(error.value) and "escaneado" in str(error.value)


def test_un_pdf_roto_o_con_clave_avisa_en_vez_de_fallar() -> None:
    for broken in (b"%PDF-1.4 esto no es un PDF", b"", b"PK\x03\x04 un zip con otro nombre"):
        with pytest.raises(UnreadableDocument) as error:
            read_document(broken, "roto.pdf")
        assert "roto.pdf" in str(error.value)

    writer = pypdf.PdfWriter(clone_from=pypdf.PdfReader(io.BytesIO(PDF)))
    writer.encrypt("clave-del-pdf")
    locked = io.BytesIO()
    writer.write(locked)
    with pytest.raises(UnreadableDocument) as error:
        read_document(locked.getvalue(), "con_clave.pdf")
    assert "contraseña" in str(error.value)


def test_un_pdf_con_demasiadas_paginas_avisa(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.onboarding.ingest.MAX_PDF_PAGES", 1)
    with pytest.raises(UnreadableDocument) as error:
        read_document(PDF, "garantia.pdf")
    assert "2 páginas" in str(error.value)


def test_el_pdf_se_indexa_como_un_docx_y_el_escaneado_no_frena_la_carga(
    conn: psycopg.Connection,
) -> None:
    tenant_id = _tenant(conn)
    uploads = [
        Upload("garantia.pdf", PDF),
        Upload("garantia_escaneada.pdf", SCANNED_PDF),
        Upload("envios.md", "# Envíos\n\nHacemos envíos a todo el país.".encode()),
    ]
    result = ingest(conn, tenant_id, uploads, business={"Acme Store"}, embedder=_embedder(conn))
    # El escaneado se informa; los otros dos archivos se guardan igual.
    assert result.documents == 2 and result.sources == 2
    assert len(result.unreadable) == 1 and "garantia_escaneada.pdf" in result.unreadable[0]
    with tenant_scope(conn, tenant_id):
        sources = conn.execute("select file_path, kind from sources order by file_path").fetchall()
        chunks = conn.execute(
            "select c.text from chunks c join documents d on d.id = c.document_id"
            " where d.source = 'garantia' and c.embedding is not null order by c.text"
        ).fetchall()
    assert sources == [("envios.md", "document"), ("garantia.pdf", "document")]
    # Las dos páginas quedan indexadas para la búsqueda, con el título del documento.
    assert chunks and all(text.startswith("Garantía de productos\n\n") for (text,) in chunks)
    indexed = "\n".join(text for (text,) in chunks)
    assert "6 meses de garantía" in indexed and "3 meses de garantía" in indexed

    # Subir de nuevo no duplica; el escaneado se vuelve a informar.
    again = ingest(conn, tenant_id, uploads, business={"Acme Store"}, embedder=_embedder(conn))
    assert again.skipped == 2 and again.documents == 0 and len(again.unreadable) == 1


def test_un_documento_con_saltos_de_linea_de_windows_se_parte_en_fragmentos(
    conn: psycopg.Connection,
) -> None:
    """Un Markdown guardado en Windows (CRLF) quedaba sin fragmentos: no se podía buscar."""
    text = "# Envíos\r\n\r\nHacemos envíos a todo el país.\r\n\r\nTardan de 3 a 5 días.\r\n"
    document = read_document(text.encode(), "envios.md")
    assert "\r" not in document.text and document.title == "Envíos"
    tenant_id = _tenant(conn)
    ingest(
        conn,
        tenant_id,
        [Upload("envios.md", text.encode())],
        business={"Acme Store"},
        embedder=_embedder(conn),
    )
    with tenant_scope(conn, tenant_id):
        chunks = conn.execute("select text from chunks").fetchall()
    assert len(chunks) == 1 and "Tardan de 3 a 5 días." in chunks[0][0]
