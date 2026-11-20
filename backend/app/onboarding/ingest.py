"""Tarea 14 — ingesta de las tres fuentes del onboarding (spec, etapa 1).

* Exportaciones de chat de WhatsApp: .txt (formato Android o iOS) o un .zip con varios .txt.
* Tickets de un helpdesk: CSV (una fila por mensaje) o JSON (un objeto por ticket, con
  comentarios), con nombres de columna habituales en inglés o castellano.
* Documentos: Markdown o texto, DOCX, PDF con texto y URLs (HTML). Un PDF escaneado no tiene
  texto: se informa y el resto de la carga sigue (decisión 030).

Todo se normaliza a conversaciones con mensajes `customer`/`agent` y fecha. Al guardar, el texto
se anonimiza antes de tocar la base (los mensajes nunca quedan en claro) y los documentos se
indexan en el RAG. Cada archivo se identifica por su hash: subirlo de nuevo no duplica nada.
"""

import csv
import hashlib
import heapq
import html
import io
import json
import os
import re
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import PurePath
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import httpx
import psycopg
import pypdf

from app import net
from app.db.connection import tenant_scope
from app.llm.embedder import Embedder
from app.onboarding import sensitive
from app.onboarding.anonymize import Anonymizer
from app.rag.indexer import index_document

Role = Literal["customer", "agent"]
SourceKind = Literal["whatsapp_export", "helpdesk_export", "document", "url"]
MAX_PDF_PAGES = 500  # extraer el texto es lento: un PDF más largo se sube en partes
CRAWL_MAX_PAGES = 30  # páginas del sitio por rastreo (CRAWL_MAX_PAGES en el entorno)
SITE_MAX_BYTES = 2 * 1024 * 1024  # por página del sitio (decisión 076)
# Algunos sitios rechazan a un cliente sin nombre: el rastreo se identifica.
USER_AGENT = "Mozilla/5.0 (compatible; EnterpriseDataAIBot/1.0)"
SKIP_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".css", ".js", ".pdf", ".zip",
    ".mp4", ".mp3", ".xml", ".json",
)  # fmt: skip

ANDROID = re.compile(
    r"^(\d{1,2})/(\d{1,2})/(\d{2,4}),? (\d{1,2}):(\d{2})(?::\d{2})?\s?(?:[ap]\.?\s?m\.?)? - "
    r"([^:]+): (.*)$"
)
IOS = re.compile(
    r"^\[(\d{1,2})/(\d{1,2})/(\d{2,4}),? (\d{1,2}):(\d{2})(?::\d{2})?\] ([^:]+): (.*)$"
)
MEDIA = re.compile(r"^<(Multimedia omitido|Media omitted|archivo adjunto|attached)", re.IGNORECASE)
CUSTOMER_ROLES = {"end_user", "customer", "cliente", "requester", "user", "usuario"}
AGENT_ROLES = {"agent", "agente", "admin", "staff", "operator", "operador", "soporte"}
COLUMNS = {
    "ticket": ("ticket_id", "ticket", "id", "conversation_id", "conversacion"),
    "time": ("created_at", "fecha", "date", "timestamp"),
    "role": ("author_type", "role", "rol", "tipo_autor"),
    "author": ("author_name", "author", "autor", "nombre"),
    "text": ("body", "message", "mensaje", "text", "texto"),
}


@dataclass(frozen=True)
class Message:
    role: Role
    text: str
    sent_at: datetime | None


@dataclass(frozen=True)
class Conversation:
    external_id: str
    messages: tuple[Message, ...]
    participants: tuple[str, ...]  # nombres de clientes (para anonimizarlos)


@dataclass(frozen=True)
class Document:
    title: str
    source: str
    text: str


@dataclass(frozen=True)
class Upload:
    name: str  # nombre del archivo, o la URL
    data: bytes


@dataclass(frozen=True)
class IngestResult:
    sources: int
    conversations: int
    messages: int
    documents: int
    skipped: int
    # Un aviso por documento que no se pudo leer (PDF escaneado, con contraseña o dañado).
    unreadable: tuple[str, ...] = ()
    # Un aviso por documento que no se indexó porque parece tener datos de personas: espera la
    # confirmación de la empresa (decisión 035).
    held: tuple[str, ...] = ()
    # Documentos que reemplazaron a otro con el mismo nombre (decisión 050).
    replaced: int = 0


class UnreadableDocument(ValueError):
    """El documento no tiene texto que se pueda leer. El mensaje se le muestra a quien lo subió."""


# -- WhatsApp --------------------------------------------------------------------------------
def _when(day: str, month: str, year: str, hour: str, minute: str) -> datetime:
    full_year = int(year) + 2000 if len(year) == 2 else int(year)
    return datetime(full_year, int(month), int(day), int(hour), int(minute))


def _whatsapp_chat(text: str, external_id: str, business: set[str]) -> Conversation | None:
    entries: list[list[Any]] = []  # [autor, fecha, líneas]
    for line in text.replace("‎", "").splitlines():
        match = ANDROID.match(line) or IOS.match(line)
        if match:
            d, mo, y, h, mi, author, body = match.groups()
            entries.append([author.strip(), _when(d, mo, y, h, mi), [body]])
        elif entries and line.strip():
            entries[-1][2].append(line)  # mensaje de varias líneas
    messages = []
    participants: list[str] = []
    for author, when, lines in entries:
        body = "\n".join(lines).strip()
        if not body or MEDIA.match(body):
            continue
        role: Role = "agent" if author in business else "customer"
        if role == "customer" and author not in participants:
            participants.append(author)
        messages.append(Message(role, body, when))
    if not messages:
        return None
    return Conversation(external_id, tuple(messages), tuple(participants))


def parse_whatsapp(data: bytes, name: str, *, business: set[str]) -> list[Conversation]:
    """Un .txt exportado o un .zip con varios (un chat por cliente)."""
    files: list[tuple[str, str]] = []
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            files = [
                (n, bundle.read(n).decode("utf-8-sig"))
                for n in sorted(bundle.namelist())
                if n.lower().endswith(".txt")
            ]
    else:
        files = [(name, data.decode("utf-8-sig"))]
    conversations = []
    for filename, text in files:
        stem = PurePath(filename).stem
        found = re.search(r"\(([^)]+)\)\s*$", stem)  # "Chat con X (acme-001)" -> acme-001
        chat = _whatsapp_chat(text, found.group(1) if found else stem, business)
        if chat is not None:
            conversations.append(chat)
    return conversations


# -- helpdesk --------------------------------------------------------------------------------
def _column(header: list[str], kind: str) -> str | None:
    lowered = {h.strip().lower(): h for h in header}
    return next((lowered[c] for c in COLUMNS[kind] if c in lowered), None)


def _role(value: str) -> Role | None:
    value = value.strip().lower()
    if value in CUSTOMER_ROLES:
        return "customer"
    if value in AGENT_ROLES:
        return "agent"
    return None


def _datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def parse_helpdesk_csv(data: bytes) -> list[Conversation]:
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    header = list(reader.fieldnames or [])
    cols = {kind: _column(header, kind) for kind in COLUMNS}
    if cols["ticket"] is None or cols["text"] is None or cols["role"] is None:
        raise ValueError("El CSV necesita columnas de ticket, rol del autor y texto")
    tickets: dict[str, list[dict[str, str]]] = {}
    for row in reader:
        tickets.setdefault(row[cols["ticket"]], []).append(row)
    conversations = []
    for ticket_id, rows in tickets.items():
        messages, participants = [], []
        for row in rows:
            role = _role(row[cols["role"]])
            text = (row[cols["text"]] or "").strip()
            if role is None or not text:
                continue
            author = (row.get(cols["author"]) or "").strip() if cols["author"] else ""
            if role == "customer" and author and author not in participants:
                participants.append(author)
            when = _datetime(row.get(cols["time"])) if cols["time"] else None
            messages.append(Message(role, text, when))
        if messages:
            conversations.append(Conversation(ticket_id, tuple(messages), tuple(participants)))
    return conversations


def parse_helpdesk_json(data: bytes) -> list[Conversation]:
    payload = json.loads(data.decode("utf-8-sig"))
    tickets = payload.get("tickets", []) if isinstance(payload, dict) else payload
    conversations = []
    for ticket in tickets:
        requester = ticket.get("requester") or {}
        participants = [requester["name"]] if requester.get("name") else []
        messages = []
        for comment in ticket.get("comments") or ticket.get("messages") or []:
            role = _role(str(comment.get("author_type") or comment.get("role") or ""))
            text = str(comment.get("body") or comment.get("text") or "").strip()
            if role is None or not text:
                continue
            messages.append(Message(role, text, _datetime(comment.get("created_at"))))
        if messages:
            conversations.append(
                Conversation(str(ticket.get("id")), tuple(messages), tuple(participants))
            )
    return conversations


# -- documentos ------------------------------------------------------------------------------
def _title(text: str, fallback: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first.lstrip("# ").strip() if first.startswith("#") else fallback


def read_document(data: bytes, name: str) -> Document:
    stem = PurePath(name).stem
    suffix = PurePath(name).suffix.lower()
    if suffix == ".docx":
        with zipfile.ZipFile(io.BytesIO(data)) as bundle:
            root = ElementTree.fromstring(bundle.read("word/document.xml"))
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        paragraphs = ["".join(t.text or "" for t in p.iter(f"{ns}t")) for p in root.iter(f"{ns}p")]
        text = "\n".join(p for p in paragraphs if p.strip())
        return Document(title=paragraphs[0].strip() if paragraphs else stem, source=stem, text=text)
    if suffix == ".pdf":
        text = _pdf_text(data, PurePath(name).name)
        first = text.splitlines()[0]
        return Document(title=first if len(first) <= 120 else stem, source=stem, text=text)
    if suffix == ".csv":  # una tabla (catálogo, envíos): una línea por fila, "columna: valor"
        reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
        lines = [
            " · ".join(f"{k.strip()}: {(v or '').strip()}" for k, v in row.items() if k and v)
            for row in reader
        ]
        return Document(title=stem, source=stem, text="\n".join(line for line in lines if line))
    # Saltos de línea de Windows: sin esto el texto no se parte en párrafos ni en fragmentos.
    text = data.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    return Document(title=_title(text, stem), source=stem, text=text)


def _pdf_text(data: bytes, name: str) -> str:
    """El texto de un PDF: un párrafo por página. Si no se puede leer, `UnreadableDocument`."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        locked = reader.is_encrypted and not reader.decrypt("")
        count = 0 if locked else len(reader.pages)
        skip = locked or count > MAX_PDF_PAGES
        extracted = [] if skip else [page.extract_text() or "" for page in reader.pages]
    except Exception as error:  # noqa: BLE001 — un PDF dañado puede fallar de muchas formas
        raise UnreadableDocument(
            f"{name}: no se pudo leer el PDF. Puede estar dañado. Probá exportarlo de nuevo, o "
            "subilo como DOCX o Markdown."
        ) from error
    if locked:
        raise UnreadableDocument(
            f"{name} está protegido con contraseña. Subí una copia sin contraseña."
        )
    if count > MAX_PDF_PAGES:
        raise UnreadableDocument(
            f"{name} tiene {count} páginas y el máximo es {MAX_PDF_PAGES}. Subilo en partes."
        )
    pages = []
    for page in extracted:
        lines = [line.strip() for line in page.replace("\x00", "").splitlines()]
        pages.append("\n".join(line for line in lines if line))
    text = "\n\n".join(page for page in pages if page)
    if not any(char.isalpha() for char in text):
        raise UnreadableDocument(
            f"{name} es un PDF escaneado: son imágenes, sin texto para leer. Subí una versión con "
            "texto (exportada desde Word o Google Docs), o pasalo a DOCX o Markdown."
        )
    return text


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.links: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        self._in_title = tag == "title"
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style", "noscript") and self._skip:
            self._skip -= 1
        self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip and data.strip():
            self.parts.append(data.strip())


def fetch_url(url: str, http: httpx.Client | None = None) -> Document:
    # Cada redirección pasa por el mismo control de dirección pública (decisión 076).
    client = http or net.safe_client(
        max_bytes=SITE_MAX_BYTES, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    response = client.get(url)
    response.raise_for_status()
    parser = _TextExtractor()
    parser.feed(response.text)
    return Document(
        title=html.unescape(parser.title.strip()) or url,
        source=_slug(url),
        text="\n".join(html.unescape(p) for p in parser.parts),
    )


def _slug(url: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", url.lower().split("//", 1)[-1]).strip("_")[:60]


def _page_url(url: str) -> str:
    """Sin fragmento ni parámetros: la misma página con otro ancla no se vuelve a leer."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", "", ""))


def crawl(
    url: str,
    http: httpx.Client | None = None,
    *,
    max_pages: int | None = None,
    on_page: Callable[[str, str], None] | None = None,
    priority: Callable[[str], int] | None = None,
) -> list[Document]:
    """Las páginas del sitio de la empresa, sin salir de su dominio (decisión 050). Nunca contra
    una dirección interna; las redirecciones se siguen solo dentro del dominio. Las imágenes y
    otros archivos se saltean. Un error en una página interna no corta el rastreo. `on_page`:
    recibe la dirección y el HTML de cada página leída (la prueba gratis saca de ahí los
    productos, decisión 075). `priority`: qué páginas leer primero (menor primero; a igual
    prioridad, en el orden en que aparecen)."""
    from app.companies.connection import check_public_host

    limit = max_pages or int(os.environ.get("CRAWL_MAX_PAGES") or CRAWL_MAX_PAGES)
    start = urlsplit(url.strip())
    if start.scheme not in ("http", "https") or not start.hostname:
        raise ValueError("la dirección tiene que empezar con https:// y tener un dominio")
    if start.scheme != "https" and not net.allow_private():
        raise ValueError("la dirección tiene que ser HTTPS")
    check_public_host(url)
    host = start.hostname.lower()
    client = http or net.safe_client(max_bytes=SITE_MAX_BYTES, headers={"User-Agent": USER_AGENT})
    first = _page_url(url.strip())
    queue: list[tuple[int, int, str]] = [(0, 0, first)]
    seen = {first}
    pages: list[Document] = []
    while queue and len(pages) < limit:
        _, _, current = heapq.heappop(queue)
        try:
            response = client.get(current)
        except (httpx.HTTPError, net.UnsafeURL, net.ResponseTooLarge):
            if current == first:
                raise
            continue
        targets: list[str] = []
        if response.is_redirect:
            targets = [response.headers.get("location", "")]
        elif response.status_code == 200 and "html" in response.headers.get(
            "content-type", "text/html"
        ):
            parser = _TextExtractor()
            parser.feed(response.text)
            if on_page is not None:
                on_page(current, response.text)
            text = "\n".join(html.unescape(p) for p in parser.parts)
            if text.strip():
                title = " ".join(html.unescape(parser.title).split()) or current
                pages.append(Document(title=title, source=_slug(current), text=text))
            targets = parser.links
        for link in targets:
            found = _page_url(urljoin(current, link))
            parts = urlsplit(found)
            if (
                parts.scheme in ("http", "https")
                and (parts.hostname or "").lower() == host
                and not parts.path.lower().endswith(SKIP_EXTENSIONS)
                and found not in seen
            ):
                seen.add(found)
                rank = priority(found) if priority is not None else 0
                heapq.heappush(queue, (rank, len(seen), found))
    if not pages:
        raise ValueError("no se encontró texto para leer en esa página")
    return pages


# -- detección y guardado --------------------------------------------------------------------
def detect_kind(upload: Upload) -> SourceKind:
    name = upload.name.lower()
    if name.startswith(("http://", "https://")):
        return "url"
    if name.endswith(".zip"):
        return "whatsapp_export"
    if name.endswith(".json"):
        return "helpdesk_export"
    if name.endswith(".csv"):  # tickets si tiene sus columnas; si no, una tabla (decisión 050)
        head = upload.data[:4000].decode("utf-8-sig", errors="ignore").splitlines()[:1]
        header = next(csv.reader(head), [])
        tickets = all(_column(header, kind) for kind in ("ticket", "text", "role"))
        return "helpdesk_export" if tickets else "document"
    if name.endswith(".txt"):
        head = upload.data[:2000].decode("utf-8-sig", errors="ignore").splitlines()
        if any(ANDROID.match(line) or IOS.match(line) for line in head):
            return "whatsapp_export"
    return "document"


def _conversations(upload: Upload, kind: SourceKind, business: set[str]) -> list[Conversation]:
    if kind == "whatsapp_export":
        return parse_whatsapp(upload.data, upload.name, business=business)
    if upload.name.lower().endswith(".csv"):
        return parse_helpdesk_csv(upload.data)
    return parse_helpdesk_json(upload.data)


def _save_conversation(
    conn: psycopg.Connection, tenant_id: uuid.UUID, source_id: uuid.UUID, chat: Conversation
) -> int:
    anonymizer = Anonymizer(names=list(chat.participants))
    started = next((m.sent_at for m in chat.messages if m.sent_at), None)
    row = conn.execute(
        "insert into conversations (tenant_id, source_id, external_id, started_at)"
        " values (%s, %s, %s, %s) returning id",
        (tenant_id, source_id, chat.external_id, started),
    ).fetchone()
    assert row is not None
    for message in chat.messages:
        conn.execute(
            "insert into messages (tenant_id, conversation_id, role, text, sent_at)"
            " values (%s, %s, %s, %s, %s)",
            (tenant_id, row[0], message.role, anonymizer.anonymize(message.text), message.sent_at),
        )
    return len(chat.messages)


def ingest(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    uploads: list[Upload],
    *,
    business: set[str],
    embedder: Embedder,
    http: httpx.Client | None = None,
    hold_personal_data: bool = False,
) -> IngestResult:
    """Guarda cada archivo como fuente; los ya ingeridos (mismo hash) se saltean.

    En los documentos se reemplazan tarjetas y DNI antes de guardar. Con `hold_personal_data`
    (el panel), un documento que parece tener datos de varias personas no se indexa: queda en
    `pending_documents` hasta que la empresa lo confirma o lo descarta."""
    sources = conversations = messages = documents = skipped = replaced = 0
    unreadable: list[str] = []
    held: list[str] = []
    for upload in uploads:
        kind = detect_kind(upload)
        content = upload.name.encode() if kind == "url" else upload.data
        digest = hashlib.sha256(content).hexdigest()
        document: Document | None = None
        if kind == "url":
            # El sitio de la empresa: rastrearlo de nuevo actualiza sus páginas (decisión 050).
            try:
                pages = crawl(upload.name, http)
            except Exception as error:  # noqa: BLE001 — se informa y la carga sigue
                unreadable.append(f"{upload.name}: no se pudo leer el sitio ({error}).")
                continue
            sources += 1
            saved, again = save_site(conn, tenant_id, upload.name, pages, embedder)
            documents += saved
            replaced += again
            continue
        with tenant_scope(conn, tenant_id):
            exists = conn.execute(
                "select 1 from sources where content_hash = %s", (digest,)
            ).fetchone()
            if exists:
                skipped += 1
                continue
            if kind == "document":
                # Se lee antes de guardar la fuente: uno que no se puede leer no deja rastro,
                # se informa y la carga sigue con los demás archivos.
                try:
                    document = read_document(upload.data, upload.name)
                except UnreadableDocument as error:
                    unreadable.append(str(error))
                    continue
                document = _without_cards_and_ids(document)
                reason = sensitive.personal_data(document.text) if hold_personal_data else None
                if reason is not None:
                    conn.execute(
                        "insert into pending_documents (tenant_id, name, title, source, text,"
                        " content_hash, reason) values (%s, %s, %s, %s, %s, %s, %s)"
                        " on conflict (tenant_id, content_hash) do nothing",
                        (
                            tenant_id,
                            PurePath(upload.name).name,
                            document.title,
                            document.source,
                            document.text,
                            digest,
                            reason,
                        ),
                    )
                    held.append(f"{PurePath(upload.name).name}: {reason}")
                    continue
            row = conn.execute(
                "insert into sources (tenant_id, kind, file_path, status, content_hash)"
                " values (%s, %s, %s, 'done', %s) returning id",
                (tenant_id, kind, upload.name, digest),
            ).fetchone()
            assert row is not None
            sources += 1
            if kind in ("whatsapp_export", "helpdesk_export"):
                for chat in _conversations(upload, kind, business):
                    messages += _save_conversation(conn, tenant_id, row[0], chat)
                    conversations += 1
                continue
        assert document is not None
        # Otro archivo con el mismo nombre ya indexado: se reemplaza (decisión 050).
        replaced += remove_source(conn, tenant_id, document.source)
        index_document(
            conn,
            tenant_id,
            embedder,
            title=document.title,
            source=document.source,
            text=document.text,
        )
        documents += 1
    return IngestResult(
        sources,
        conversations,
        messages,
        documents,
        skipped,
        tuple(unreadable),
        tuple(held),
        replaced,
    )


def save_site(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    url: str,
    pages: list[Document],
    embedder: Embedder,
) -> tuple[int, int]:
    """Guarda el sitio rastreado como fuente y reindexa sus páginas (sin tarjetas ni DNI).
    Rastrearlo de nuevo reemplaza lo anterior. Devuelve (páginas indexadas, reemplazadas)."""
    digest = hashlib.sha256(url.encode()).hexdigest()
    with tenant_scope(conn, tenant_id):
        conn.execute("delete from sources where content_hash = %s", (digest,))
        conn.execute(
            "insert into sources (tenant_id, kind, file_path, status, content_hash)"
            " values (%s, 'url', %s, 'done', %s)",
            (tenant_id, url, digest),
        )
    replaced = 0
    for page in pages:
        page = _without_cards_and_ids(page)
        replaced += remove_source(conn, tenant_id, page.source)
        index_document(
            conn, tenant_id, embedder, title=page.title, source=page.source, text=page.text
        )
    return len(pages), replaced


def _without_cards_and_ids(document: Document) -> Document:
    text, _ = sensitive.redact(document.text)
    title, _ = sensitive.redact(document.title)
    return Document(title=title, source=document.source, text=text)


@dataclass(frozen=True)
class PendingDocument:
    id: uuid.UUID
    name: str
    reason: str
    created_at: datetime


def pending_documents(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[PendingDocument]:
    """Documentos que esperan la confirmación de la empresa."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select id, name, reason, created_at from pending_documents order by created_at, id"
        ).fetchall()
    return [PendingDocument(*row) for row in rows]


def confirm_pending(
    conn: psycopg.Connection, tenant_id: uuid.UUID, document_id: uuid.UUID, embedder: Embedder
) -> str | None:
    """La empresa confirma: el documento se indexa y deja de esperar. Devuelve su nombre, o
    None si no existe (o es de otra empresa)."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "delete from pending_documents where id = %s"
            " returning name, title, source, text, content_hash",
            (document_id,),
        ).fetchone()
        if row is None:
            return None
        name, title, source, text, digest = row
        conn.execute(
            "insert into sources (tenant_id, kind, file_path, status, content_hash)"
            " values (%s, 'document', %s, 'done', %s)",
            (tenant_id, name, digest),
        )
    remove_source(conn, tenant_id, source)  # reemplaza al de mismo nombre (decisión 050)
    index_document(conn, tenant_id, embedder, title=title, source=source, text=text)
    return str(name)


def discard_pending(
    conn: psycopg.Connection, tenant_id: uuid.UUID, document_id: uuid.UUID
) -> str | None:
    """La empresa lo descarta: no queda nada guardado. Devuelve su nombre, o None."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "delete from pending_documents where id = %s returning name", (document_id,)
        ).fetchone()
    return str(row[0]) if row else None


# -- documentos indexados: listar, reemplazar y borrar (decisión 050) -----------------------
@dataclass(frozen=True)
class IndexedDocument:
    id: uuid.UUID
    title: str
    source: str
    chunks: int
    created_at: datetime


def source_key(name: str) -> str:
    """Con qué `source` queda indexado un archivo o una página: subir otro con el mismo nombre
    lo reemplaza."""
    if name.lower().startswith(("http://", "https://")):
        return _slug(name)
    return PurePath(name.replace("\\", "/")).stem


def remove_source(conn: psycopg.Connection, tenant_id: uuid.UUID, source: str) -> int:
    """Saca del RAG los documentos con ese `source`. Devuelve cuántos había."""
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "delete from chunks where document_id in (select id from documents where source = %s)",
            (source,),
        )
        return conn.execute("delete from documents where source = %s", (source,)).rowcount


def indexed_documents(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[IndexedDocument]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select d.id, d.title, coalesce(d.source, ''), count(c.id), d.created_at"
            " from documents d left join chunks c on c.document_id = d.id"
            " group by d.id order by d.title, d.created_at"
        ).fetchall()
    return [IndexedDocument(*row) for row in rows]


def delete_document(conn: psycopg.Connection, tenant_id: uuid.UUID, document_id: uuid.UUID) -> bool:
    """Lo saca del RAG. El archivo deja de figurar como subido: se puede volver a subir."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute("select source from documents where id = %s", (document_id,)).fetchone()
        if row is None:
            return False
        conn.execute("delete from chunks where document_id = %s", (document_id,))
        conn.execute("delete from documents where id = %s", (document_id,))
        for source_id, path in conn.execute(
            "select id, file_path from sources where kind in ('document', 'url')"
        ).fetchall():
            if path and source_key(path) == row[0]:
                conn.execute("delete from sources where id = %s", (source_id,))
    return True
