"""Carga de la empresa de demo Acme desde evals/fixtures/acme/ (datos ficticios)."""

import uuid
from pathlib import Path

import psycopg

from app.config import REPO_ROOT
from app.llm.embedder import Embedder
from app.rag.indexer import index_document

ACME_DIR = REPO_ROOT / "evals" / "fixtures" / "acme"


def _title(text: str, fallback: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first.lstrip("# ").strip() if first.startswith("#") else fallback


def index_acme_documents(conn: psycopg.Connection, tenant_id: uuid.UUID, embedder: Embedder) -> int:
    """Indexa los documentos de políticas de Acme. Devuelve cuántos indexó."""
    paths: list[Path] = sorted((ACME_DIR / "documents").glob("*.md"))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        index_document(
            conn, tenant_id, embedder, title=_title(text, path.stem), source=path.stem, text=text
        )
    return len(paths)
