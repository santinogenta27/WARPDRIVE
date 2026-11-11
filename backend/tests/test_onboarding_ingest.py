"""Tarea 14 — test de aceptación: 3 formatos de ejemplo producen el mismo esquema normalizado.

La ingesta acepta exportaciones de WhatsApp (.txt o .zip), tickets de un helpdesk (CSV o JSON) y
documentos (Markdown, DOCX, URL). Las conversaciones se normalizan a mensajes con rol y fecha y
se guardan anonimizadas; los documentos van al RAG. Subir dos veces el mismo archivo no duplica.
"""

import csv
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path

import httpx
import psycopg
from simulated.voyage import voyage_transport

from app.db.connection import tenant_scope
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.onboarding.ingest import (
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
    assert document.text == "Cambios\nTenés 30 días." and document.source == "cambios"

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
