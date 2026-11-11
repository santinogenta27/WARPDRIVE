"""Tarea 16 — descubrimiento de casos (spec, etapa 3).

1. Un modelo resume cada conversación anonimizada en lotes (intención, resultado y si el agente
   humano tuvo que hacer algo en un sistema). El resumen se guarda en la conversación: si se
   corta (cuota del modelo gratuito), la próxima corrida sigue con las que faltan.
2. Los resúmenes se convierten en embeddings y se agrupan: aglomerativo de enlace promedio sobre
   la similitud coseno, con el corte de mejor silueta; los grupos de menos de `min_size`
   conversaciones son ruido. Cumple el rol de HDBSCAN de la spec sin sumar dependencias
   (decisión 021); para cientos o miles de conversaciones alcanza.
3. El modelo nombra cada grupo y se guardan como tipos de caso inactivos, con volumen y ejemplos:
   el cliente elige cuáles activar después del reporte.
"""

import json
import math
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import psycopg
import yaml
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope
from app.llm.embedder import Embedder

MAX_CLUSTERS = 15


@dataclass(frozen=True)
class Summary:
    intencion: str
    resultado: str
    accion_en_sistema: bool


@dataclass(frozen=True)
class ClusterName:
    nombre: str
    objetivo: str


@dataclass(frozen=True)
class DiscoveredType:
    id: uuid.UUID
    name: str
    goal: str
    volume: int
    system_action_share: float  # parte de las conversaciones que pidió actuar en un sistema


class Summarizer(Protocol):
    def summarize(self, batch: list[tuple[str, str]]) -> dict[str, Summary]: ...

    def name_clusters(self, clusters: list[list[Summary]]) -> list[ClusterName]: ...


# -- resúmenes -------------------------------------------------------------------------------
def transcript(conn: psycopg.Connection, conversation_id: uuid.UUID) -> str:
    rows = conn.execute(
        "select role, text from messages where conversation_id = %s order by sent_at, id",
        (conversation_id,),
    ).fetchall()
    return "\n".join(f"{'cliente' if r == 'customer' else 'agente'}: {t}" for r, t in rows)


def summarize_pending(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    model: Summarizer,
    *,
    batch_size: int = 10,
    checkpoint: Callable[[], None] = lambda: None,
) -> int:
    """Resume las conversaciones que todavía no tienen resumen. Devuelve cuántas resumió.
    `checkpoint` se llama después de cada lote (el pipeline confirma ahí la transacción)."""
    with tenant_scope(conn, tenant_id):
        pending = conn.execute(
            "select id from conversations where summary is null order by started_at, id"
        ).fetchall()
    done = 0
    for start in range(0, len(pending), batch_size):
        ids = [row[0] for row in pending[start : start + batch_size]]
        with tenant_scope(conn, tenant_id):
            batch = [(str(cid), transcript(conn, cid)) for cid in ids]
        summaries = model.summarize(batch)
        with tenant_scope(conn, tenant_id):
            for cid in ids:
                summary = summaries.get(str(cid))
                if summary is None:
                    continue  # el modelo la salteó: queda pendiente para la próxima
                conn.execute(
                    "update conversations set summary = %s where id = %s",
                    (Jsonb(summary.__dict__), cid),
                )
                done += 1
        checkpoint()
    return done


# -- agrupamiento ----------------------------------------------------------------------------
def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _silhouette(similarity: list[list[float]], labels: list[int]) -> float:
    n = len(labels)
    groups: dict[int, list[int]] = {}
    for i, label in enumerate(labels):
        groups.setdefault(label, []).append(i)
    if len(groups) < 2:
        return -1.0
    total = 0.0
    for i in range(n):
        own = groups[labels[i]]
        if len(own) == 1:
            continue  # silueta 0 por convención
        a = sum(1 - similarity[i][j] for j in own if j != i) / (len(own) - 1)
        b = min(
            sum(1 - similarity[i][j] for j in members) / len(members)
            for label, members in groups.items()
            if label != labels[i]
        )
        total += (b - a) / max(a, b) if max(a, b) > 0 else 0.0
    return total / n


def cluster(vectors: list[list[float]], *, min_size: int = 3) -> list[list[int]]:
    """Índices agrupados. Aglomerativo de enlace promedio con coseno; se elige el número de
    grupos con mejor silueta y se descartan los grupos chicos (ruido)."""
    n = len(vectors)
    if n < 2:
        return [[0]] if n == 1 and min_size <= 1 else []
    unit = [_normalize(v) for v in vectors]
    similarity = [[sum(a * b for a, b in zip(u, v, strict=True)) for v in unit] for u in unit]
    clusters: dict[int, list[int]] = {i: [i] for i in range(n)}
    sums: dict[tuple[int, int], float] = {
        (i, j): similarity[i][j] for i in range(n) for j in range(i + 1, n)
    }
    partitions: dict[int, list[list[int]]] = {}
    while len(clusters) > 1:
        if len(clusters) <= MAX_CLUSTERS:
            partitions[len(clusters)] = [list(m) for m in clusters.values()]
        best, best_value = (-1, -1), -math.inf
        for (i, j), total in sums.items():
            value = total / (len(clusters[i]) * len(clusters[j]))
            if value > best_value:
                best, best_value = (i, j), value
        i, j = best
        for k in clusters:
            if k in (i, j):
                continue
            key_ik, key_jk = (min(i, k), max(i, k)), (min(j, k), max(j, k))
            sums[key_ik] = sums[key_ik] + sums.pop(key_jk)
        sums.pop((i, j))
        clusters[i].extend(clusters.pop(j))
    best_labels, best_score = None, -math.inf
    for groups in partitions.values():
        if len(groups) < 2:
            continue
        labels = [0] * n
        for label, members in enumerate(groups):
            for member in members:
                labels[member] = label
        score = _silhouette(similarity, labels)
        if score > best_score:
            best_labels, best_score = groups, score
    chosen = best_labels or [list(range(n))]
    return [sorted(g) for g in chosen if len(g) >= min_size]


# -- tipos de caso ---------------------------------------------------------------------------
def _slug(name: str) -> str:
    plain = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return plain or "tipo_de_caso"


def instructions_yaml(name: str, goal: str, tools: list[str] | None = None) -> str:
    return yaml.safe_dump(
        {
            "nombre": name,
            "objetivo": goal,
            "datos_necesarios": [],
            "herramientas": tools or [],
            "condiciones": [
                "Usá solo los documentos y los resultados de herramientas.",
                "Si falta un dato que el cliente puede dar, pedíselo.",
                "Si hay que actuar en un sistema y no hay herramienta, derivá.",
            ],
            "cierre": "Confirmá lo que resolviste y preguntá si necesita algo más.",
        },
        allow_unicode=True,
        sort_keys=False,
    )


def discover_case_types(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    model: Summarizer,
    embedder: Embedder,
    *,
    batch_size: int = 10,
    min_size: int = 3,
    checkpoint: Callable[[], None] = lambda: None,
) -> list[DiscoveredType]:
    summarize_pending(conn, tenant_id, model, batch_size=batch_size, checkpoint=checkpoint)
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select id, external_id, summary from conversations where summary is not null"
            " order by started_at, id"
        ).fetchall()
    if not rows:
        return []
    summaries = [Summary(**row[2]) for row in rows]
    texts = [f"{s.intencion}. {s.resultado}" for s in summaries]
    vectors = embedder.embed(texts, tenant_id=tenant_id, input_type="document")
    groups = cluster(vectors, min_size=min_size)
    names = model.name_clusters([[summaries[i] for i in g] for g in groups])
    found: list[DiscoveredType] = []
    used: set[str] = set()
    with tenant_scope(conn, tenant_id):
        conn.execute("update conversations set case_type_id = null")
        for members, name in zip(groups, names, strict=True):
            slug = _slug(name.nombre)
            while slug in used:
                slug += "_2"
            used.add(slug)
            examples = [{"conversacion": rows[i][1], **summaries[i].__dict__} for i in members[:3]]
            share = sum(summaries[i].accion_en_sistema for i in members) / len(members)
            row = conn.execute(
                "insert into case_types (tenant_id, name, volume, examples, active, instructions)"
                " values (%s, %s, %s, %s, false, %s)"
                " on conflict (tenant_id, name) do update set volume = excluded.volume,"
                " examples = excluded.examples returning id",
                (
                    tenant_id,
                    slug,
                    len(members),
                    Jsonb(examples),
                    instructions_yaml(slug, name.objetivo),
                ),
            ).fetchone()
            assert row is not None
            conn.execute(
                "update conversations set case_type_id = %s where id = any(%s)",
                (row[0], [rows[i][0] for i in members]),
            )
            found.append(DiscoveredType(row[0], slug, name.objetivo, len(members), share))
    return found


def parse_json_list(text: str) -> list[dict[str, object]]:
    """JSON de una respuesta de modelo, aunque venga con texto alrededor."""
    match = re.search(r"\[.*\]", text, re.S)
    data = json.loads(match.group(0) if match else text)
    return data if isinstance(data, list) else []
