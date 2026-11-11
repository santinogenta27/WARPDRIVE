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
import html
import io
import json
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import PurePath
from typing import Any, Literal
from xml.etree import ElementTree

import httpx
import psycopg
import pypdf

from app.db.connection import tenant_scope
from app.llm.embedder import Embedder
from app.onboarding.anonymize import Anonymizer
from app.rag.indexer import index_document

Role = Literal["customer", "agent"]
SourceKind = Literal["whatsapp_export", "helpdesk_export", "document", "url"]
MAX_PDF_PAGES = 500  # extraer el texto es lento: un PDF más largo se sube en partes

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
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in ("script", "style", "noscript"):
            self._skip += 1
        self._in_title = tag == "title"

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
    client = http or httpx.Client(timeout=20.0, follow_redirects=True)
    response = client.get(url)
    response.raise_for_status()
    parser = _TextExtractor()
    parser.feed(response.text)
    slug = re.sub(r"[^a-z0-9]+", "_", url.lower().split("//", 1)[-1]).strip("_")[:60]
    return Document(
        title=html.unescape(parser.title.strip()) or url,
        source=slug,
        text="\n".join(html.unescape(p) for p in parser.parts),
    )


# -- detección y guardado --------------------------------------------------------------------
def detect_kind(upload: Upload) -> SourceKind:
    name = upload.name.lower()
    if name.startswith(("http://", "https://")):
        return "url"
    if name.endswith(".zip"):
        return "whatsapp_export"
    if name.endswith((".csv", ".json")):
        return "helpdesk_export"
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
) -> IngestResult:
    """Guarda cada archivo como fuente; los ya ingeridos (mismo hash) se saltean."""
    sources = conversations = messages = documents = skipped = 0
    unreadable: list[str] = []
    for upload in uploads:
        kind = detect_kind(upload)
        content = upload.name.encode() if kind == "url" else upload.data
        digest = hashlib.sha256(content).hexdigest()
        document: Document | None = None
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
        if document is None:
            document = fetch_url(upload.name, http)
        index_document(
            conn,
            tenant_id,
            embedder,
            title=document.title,
            source=document.source,
            text=document.text,
        )
        documents += 1
    return IngestResult(sources, conversations, messages, documents, skipped, tuple(unreadable))
