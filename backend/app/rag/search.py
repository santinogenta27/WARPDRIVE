"""Búsqueda híbrida: vectorial (pgvector) + full-text (tsvector), fusionadas por RRF."""

import uuid
from dataclasses import dataclass

import psycopg

from app.db.connection import tenant_scope
from app.llm.embedder import Embedder
from app.rag.indexer import vector_literal

# Consulta full-text en modo OR: cada raíz de la pregunta suma, ninguna es obligatoria.
_FTS_QUERY = (
    "to_tsquery('simple', array_to_string(array("
    "  select quote_literal(lexeme)"
    "  from unnest(tsvector_to_array(to_tsvector('spanish', %s))) as lexeme), ' | '))"
)


@dataclass(frozen=True)
class SearchResult:
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    title: str
    source: str
    text: str
    score: float


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
    return sorted(scores, key=lambda item: scores[item], reverse=True)


def hybrid_search(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    embedder: Embedder,
    query: str,
    *,
    k: int = 5,
    candidates: int = 20,
) -> list[SearchResult]:
    query_vector = embedder.embed([query], tenant_id=tenant_id, input_type="query")[0]
    with tenant_scope(conn, tenant_id):
        # Un solo viaje a la base (decisión 044): las dos listas de candidatos, en orden, y los
        # fragmentos de todos. La fusión se hace acá.
        rows = conn.execute(
            "with ranked as (select"
            " array(select id from chunks where embedding is not null"
            "  order by embedding <=> %s::vector limit %s) as by_vector,"
            f" array(select id from chunks, {_FTS_QUERY} as q where tsv @@ q"
            "  order by ts_rank_cd(tsv, q) desc limit %s) as by_text)"
            " select r.by_vector, r.by_text, c.id, c.document_id, d.title, c.source, c.text"
            " from ranked r left join chunks c on c.id = any(r.by_vector || r.by_text)"
            " left join documents d on d.tenant_id = c.tenant_id and d.id = c.document_id",
            (vector_literal(query_vector), candidates, query, candidates),
        ).fetchall()
    by_vector = [str(i) for i in rows[0][0]] if rows else []
    by_text = [str(i) for i in rows[0][1]] if rows else []
    fused = reciprocal_rank_fusion([by_vector, by_text])[:k]
    by_id = {str(r[2]): r[2:] for r in rows if r[2] is not None and r[4] is not None}
    if not fused:
        return []
    return [
        SearchResult(
            chunk_id=by_id[cid][0],
            document_id=by_id[cid][1],
            title=by_id[cid][2],
            source=by_id[cid][3],
            text=by_id[cid][4],
            score=1.0 / (position + 1),
        )
        for position, cid in enumerate(fused)
        if cid in by_id
    ]
