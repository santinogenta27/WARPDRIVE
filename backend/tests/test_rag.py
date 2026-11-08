"""Tarea 6 — test de aceptación: RAG híbrido con Recall@5 >= 90% en preguntas de Acme.

Embeddings: el cliente real de Voyage sobre un transporte simulado (embeddings léxicos
deterministas). Con VOYAGE_API_KEY el mismo código usa embeddings reales.
"""

import json
import uuid
from pathlib import Path

import httpx
import psycopg
from simulated.voyage import voyage_transport

from app.demo.acme import ACME_DIR, index_acme_documents
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.rag.indexer import chunk_text
from app.rag.search import hybrid_search, reciprocal_rank_fusion

QUESTIONS = json.loads(Path(ACME_DIR / "rag_questions.json").read_text(encoding="utf-8"))


def _tenant(conn: psycopg.Connection, name: str) -> uuid.UUID:
    row = conn.execute("insert into tenants (name) values (%s) returning id", (name,)).fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    return tenant_id


def _embedder(conn: psycopg.Connection) -> VoyageEmbedder:
    return VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="test",
        http_client=httpx.Client(transport=voyage_transport()),
    )


def test_recall_at_5_en_preguntas_de_acme(conn: psycopg.Connection) -> None:
    tenant = _tenant(conn, "Acme Store")
    embedder = _embedder(conn)
    assert index_acme_documents(conn, tenant, embedder) >= 10

    hits = 0
    misses: list[str] = []
    for item in QUESTIONS:
        results = hybrid_search(conn, tenant, embedder, item["question"], k=5)
        if item["expected"] in {r.source for r in results}:
            hits += 1
        else:
            misses.append(item["question"])
    recall = hits / len(QUESTIONS)
    assert recall >= 0.9, f"Recall@5 = {recall:.2f}; fallaron: {misses}"


def test_la_busqueda_no_cruza_tenants(conn: psycopg.Connection) -> None:
    acme = _tenant(conn, "Acme Store")
    other = _tenant(conn, "Otra empresa")
    embedder = _embedder(conn)
    index_acme_documents(conn, acme, embedder)
    assert hybrid_search(conn, other, embedder, "¿Tienen cuotas sin interés?", k=5) == []


def test_resultados_traen_fuente_y_texto(conn: psycopg.Connection) -> None:
    tenant = _tenant(conn, "Acme Store")
    embedder = _embedder(conn)
    index_acme_documents(conn, tenant, embedder)
    top = hybrid_search(conn, tenant, embedder, "¿Abren los domingos?", k=5)[0]
    assert top.source == "horarios_y_sucursales"
    assert "domingos" in top.text
    assert top.title == "Horarios y sucursales"


def test_fusion_de_rankings() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "a", "d"]], k=60)
    assert fused[:2] == ["a", "c"]
    assert set(fused) == {"a", "b", "c", "d"}


def test_chunking_respeta_parrafos_y_tamano() -> None:
    text = "# Título\n\n" + "\n\n".join(f"Párrafo {i}. " + "palabra " * 40 for i in range(6))
    chunks = chunk_text(text, max_chars=700)
    assert len(chunks) > 1
    assert all(len(c) <= 700 for c in chunks)
    assert all(not c.startswith("# ") for c in chunks)  # el título va aparte
