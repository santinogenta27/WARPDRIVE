"""Indexado de documentos: fragmentos con embedding (pgvector) y tsvector (full-text)."""

import re
import uuid

import psycopg

from app.db.connection import tenant_scope
from app.llm.embedder import Embedder


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


MAX_CHARS = 900
SMALL = 240  # una sección más corta se junta con la siguiente
HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
SENTENCE = re.compile(r"(?<=[.!?…])\s+(?=[¿¡A-ZÁÉÍÓÚÑ0-9])")


def _units(block: str, max_chars: int) -> list[str]:
    """Las piezas que no se cortan: un párrafo entero, o cada ítem de una lista o fila de una
    tabla. Solo un párrafo más largo que un fragmento se parte, por oraciones."""
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) > 1 and any(re.match(r"^(?:[-*•]|\d+[.)])\s", ln) or " · " in ln for ln in lines):
        pieces = lines
    else:
        pieces = [" ".join(lines)]
    out: list[str] = []
    for piece in pieces:
        if len(piece) <= max_chars:
            out.append(piece)
            continue
        current = ""
        for sentence in SENTENCE.split(piece):
            while len(sentence) > max_chars:  # una oración gigante: por palabras, la excepción
                cut = sentence.rfind(" ", 0, max_chars)
                cut = cut if cut > 0 else max_chars
                if current:
                    out.append(current)
                    current = ""
                out.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            if current and len(current) + len(sentence) + 1 > max_chars:
                out.append(current)
                current = sentence
            else:
                current = f"{current} {sentence}".strip()
        if current:
            out.append(current)
    return out


def sections(text: str) -> list[tuple[list[str], list[str]]]:
    """El texto por secciones: (los títulos que la contienen, sus bloques)."""
    path: list[tuple[int, str]] = []
    found: list[tuple[list[str], list[str]]] = [([], [])]
    for block in re.split(r"\n\s*\n", text.replace("\r\n", "\n")):
        lines = block.strip().splitlines()
        rest: list[str] = []
        for line in lines:
            heading = HEADING.match(line.strip())
            if heading:
                if rest:
                    found[-1][1].append("\n".join(rest))
                    rest = []
                level = len(heading.group(1))
                path = [(lvl, name) for lvl, name in path if lvl < level]
                path.append((level, heading.group(2).strip()))
                # El título del documento (nivel 1) ya va al indexar: acá, desde el nivel 2.
                found.append(([name for lvl, name in path if lvl > 1], []))
            else:
                rest.append(line)
        if rest:
            found[-1][1].append("\n".join(rest))
    return [(names, blocks) for names, blocks in found if blocks]


def chunk_text(text: str, max_chars: int = MAX_CHARS) -> list[str]:
    """Fragmentos que respetan la estructura (decisión 088):

    - cada fragmento empieza con los títulos de su sección (sin el del documento, que se
      antepone al indexar), así se entiende solo;
    - nunca corta un párrafo, un ítem de lista ni una fila de tabla, salvo que solo ya sea más
      largo que un fragmento;
    - una sección corta se junta con la siguiente si entran las dos.
    """
    chunks: list[str] = []
    buffer: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal size
        if buffer:
            chunks.append("\n".join(buffer))
        buffer.clear()
        size = 0

    for path, blocks in sections(text):
        head = f"## {' › '.join(path)}" if path else ""
        units = [u for block in blocks for u in _units(block, max_chars)]
        total = len(head) + sum(len(u) + 1 for u in units)
        # Una sección por fragmento; solo las muy cortas se juntan con la siguiente.
        if buffer and (size + total > max_chars or size > SMALL):
            flush()
        if head:
            buffer.append(head)
            size += len(head) + 1
        for unit in units:
            if size + len(unit) + 1 > max_chars and len(buffer) > (1 if head else 0):
                flush()
                if head:  # la sección sigue: el fragmento nuevo repite su título
                    buffer.append(head)
                    size += len(head) + 1
            buffer.append(unit)
            size += len(unit) + 1
    flush()
    return chunks


def index_document(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    embedder: Embedder,
    *,
    title: str,
    source: str,
    text: str,
) -> uuid.UUID:
    chunks = chunk_text(text)
    # El título se antepone a cada fragmento: mejora la búsqueda y da contexto a la respuesta.
    contents = [f"{title}\n\n{chunk}" for chunk in chunks]
    vectors = embedder.embed(contents, tenant_id=tenant_id, input_type="document")
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            # El texto extraído queda guardado: Knowledge muestra qué aprendió (decisión 088).
            "insert into documents (tenant_id, title, source, text) values (%s, %s, %s, %s)"
            " returning id",
            (tenant_id, title, source, text),
        ).fetchone()
        assert row is not None
        document_id: uuid.UUID = row[0]
        for content, vector in zip(contents, vectors, strict=True):
            conn.execute(
                "insert into chunks (tenant_id, document_id, text, embedding, source)"
                " values (%s, %s, %s, %s::vector, %s)",
                (tenant_id, document_id, content, vector_literal(vector), source),
            )
    return document_id
