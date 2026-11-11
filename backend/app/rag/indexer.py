"""Indexado de documentos: fragmentos con embedding (pgvector) y tsvector (full-text)."""

import uuid

import psycopg

from app.db.connection import tenant_scope
from app.llm.embedder import Embedder


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{v:.6f}" for v in values) + "]"


def chunk_text(text: str, max_chars: int = 700) -> list[str]:
    """Agrupa párrafos hasta `max_chars`. Los títulos Markdown (#) no forman fragmentos."""
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    paragraphs = [p for p in paragraphs if not p.startswith("#")]
    chunks: list[str] = []
    current = ""
    for paragraph in paragraphs:
        while len(paragraph) > max_chars:  # párrafo gigante: corte duro por palabras
            cut = paragraph.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            if current:
                chunks.append(current)
                current = ""
            chunks.append(paragraph[:cut].strip())
            paragraph = paragraph[cut:].strip()
        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) <= max_chars:
            current = candidate
        else:
            chunks.append(current)
            current = paragraph
    if current:
        chunks.append(current)
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
            "insert into documents (tenant_id, title, source) values (%s, %s, %s) returning id",
            (tenant_id, title, source),
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
