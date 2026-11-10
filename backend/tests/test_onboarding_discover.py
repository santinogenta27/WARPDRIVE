"""Tarea 16 — test de aceptación: en un dataset sintético con 8 tipos conocidos, recupera al
menos 7.

Cada conversación (ya anonimizada) se resume en lotes (intención, resultado y si hubo que hacer
algo en un sistema); los resúmenes se agrupan por similitud de sus embeddings y cada grupo se
nombra. El avance se guarda por lote: si se corta, retoma sin volver a resumir.
"""

import json
import sys
import uuid
from collections import Counter
from pathlib import Path

import httpx
import psycopg
from simulated.voyage import voyage_transport

from app.db.connection import tenant_scope
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.onboarding.discover import ClusterName, Summary, cluster, discover_case_types
from app.onboarding.ingest import Upload, ingest

ONBOARDING = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "acme" / "onboarding"
sys.path.insert(0, str(ONBOARDING))


class FakeModel:
    """Resumidor determinístico: la intención es lo que pidió el cliente y lo que respondió el
    agente (sin modelo, para medir el agrupamiento)."""

    def __init__(self) -> None:
        self.summarized: list[str] = []

    def summarize(self, batch: list[tuple[str, str]]) -> dict[str, Summary]:
        self.summarized.extend(cid for cid, _ in batch)
        result = {}
        for cid, transcript in batch:
            lines = transcript.splitlines()
            customer = next(line for line in lines if line.startswith("cliente:"))
            agent = next((line for line in lines if line.startswith("agente:")), "")
            result[cid] = Summary(
                intencion=customer[8:].strip(), resultado=agent[7:].strip(), accion_en_sistema=False
            )
        return result

    def name_clusters(self, clusters: list[list[Summary]]) -> list[ClusterName]:
        names = []
        for i, members in enumerate(clusters):
            words = Counter(w for s in members for w in s.intencion.lower().split() if len(w) > 4)
            top = words.most_common(1)[0][0] if words else "consulta"
            names.append(ClusterName(nombre=f"tipo_{i + 1}_{top}", objetivo=f"Atender: {top}"))
        return names


def _embedder(conn: psycopg.Connection) -> VoyageEmbedder:
    return VoyageEmbedder(
        recorder=PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )


def test_agrupa_vectores_cercanos_y_descarta_el_ruido() -> None:
    points = [[1.0, 0.0, 0.0]] * 4 + [[0.0, 1.0, 0.0]] * 4 + [[0.0, 0.0, 1.0]]
    groups = cluster(points, min_size=3)
    assert sorted(sorted(g) for g in groups) == [[0, 1, 2, 3], [4, 5, 6, 7]]


def test_recupera_al_menos_7_de_8_tipos_conocidos(conn: psycopg.Connection) -> None:
    row = conn.execute(
        "insert into tenants (name) values ('Acme onboarding') returning id"
    ).fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    embedder = _embedder(conn)
    uploads = [
        Upload(n, (ONBOARDING / n).read_bytes())
        for n in ("whatsapp_export.zip", "tickets.csv", "tickets.json")
    ]
    ingest(conn, tenant_id, uploads, business={"Acme Store"}, embedder=embedder)

    model = FakeModel()
    found = discover_case_types(conn, tenant_id, model, embedder, batch_size=10)

    labels = json.loads((ONBOARDING / "labels.json").read_text(encoding="utf-8"))
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select c.external_id, t.name from conversations c"
            " join case_types t on t.id = c.case_type_id"
        ).fetchall()
        stored = conn.execute("select name, volume, active from case_types").fetchall()
    members: dict[str, list[str]] = {}
    for external_id, type_name in rows:
        members.setdefault(type_name, []).append(labels[external_id]["tipo"])
    recovered = {
        Counter(kinds).most_common(1)[0][0] for kinds in members.values() if len(kinds) >= 3
    }
    assert len(recovered) >= 7, recovered
    assert {s[0] for s in stored} == {t.name for t in found}
    assert all(not active for _, _, active in stored)  # el cliente los activa después
    assert sum(volume for _, volume, _ in stored) == len(rows)

    # Retoma sin volver a resumir: el avance quedó guardado por conversación.
    model.summarized.clear()
    discover_case_types(conn, tenant_id, model, embedder, batch_size=10)
    assert model.summarized == []
