"""Hito 4 — test de hito: Acme pasa del archivo subido al reporte sin intervención manual.

Las seis etapas (ingesta, anonimización, descubrimiento, evals, herramientas y reporte) corren
seguidas desde los archivos de Acme (export de WhatsApp, tickets CSV y JSON, documentos y el
OpenAPI de su API). Con un modelo determinístico y el agente simulado, sin claves; la corrida
real con Groq está en docs/onboarding.md. Retomar no repite etapas hechas.
"""

import json
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import psycopg
from simulated.claude import SimulatedClaude
from simulated.voyage import voyage_transport

from acme_api import OPENAPI_PATH
from app.agent.loop import Agent
from app.db.connection import tenant_scope
from app.llm.client import AnthropicProvider, LLMClient, PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.onboarding.discover import ClusterName, Summary
from app.onboarding.ingest import Upload
from app.onboarding.pipeline import STAGES, PipelineDeps, run_pipeline
from app.onboarding.report import render_markdown
from app.policy.guardian import Guardian
from app.tools.runtime import ToolRuntime

ACME = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "acme"


class FakeOnboardingModel:
    def __init__(self) -> None:
        self.calls = 0

    def summarize(self, batch: list[tuple[str, str]]) -> dict[str, Summary]:
        self.calls += 1
        result = {}
        for cid, text in batch:
            lines = text.splitlines()
            customer = next(line for line in lines if line.startswith("cliente:"))[8:].strip()
            agent = next((line for line in lines if line.startswith("agente:")), "agente:")[7:]
            result[cid] = Summary(
                customer, agent.strip(), "actualicé" in agent or "Cancelé" in agent
            )
        return result

    def name_clusters(self, clusters: list[list[Summary]]) -> list[ClusterName]:
        self.calls += 1
        out = []
        for i, members in enumerate(clusters):
            words = Counter(w for s in members for w in s.intencion.lower().split() if len(w) > 5)
            top = words.most_common(1)[0][0] if words else "consulta"
            out.append(ClusterName(f"tipo_{i + 1}_{top}", f"Resolver consultas sobre {top}"))
        return out

    def make_evals(
        self, case_type: str, goal: str, conversations: list[tuple[str, str]]
    ) -> list[dict[str, Any]]:
        self.calls += 1
        return [
            {
                "conversacion": cid,
                "mensaje": text.splitlines()[0].split(": ", 1)[1],
                "accion": "responder",
                "debe_escalar": False,
                "palabras_clave": [],
            }
            for cid, text in conversations
        ]

    def pick_operations(
        self, case_types: list[tuple[str, str]], operations: list[tuple[str, str]]
    ) -> dict[str, list[str]]:
        self.calls += 1
        lookup = [op for op, _ in operations if op == "consultarPedido"]
        return {name: lookup for name, _ in case_types}


def _deps(conn: psycopg.Connection, model: FakeOnboardingModel) -> PipelineDeps:
    recorder = PostgresUsageRecorder(conn)
    embedder = VoyageEmbedder(
        recorder=recorder, api_key="sim", http_client=httpx.Client(transport=voyage_transport())
    )
    claude = SimulatedClaude(mode="normal")
    sdk = anthropic.Anthropic(
        api_key="sim", http_client=httpx2.Client(transport=claude.transport()), max_retries=0
    )
    llm = LLMClient(provider=AnthropicProvider(client=sdk), recorder=recorder)

    def agent_factory(c: psycopg.Connection, runtime: ToolRuntime) -> Agent:
        return Agent(c, llm, embedder, Guardian(c, runtime))

    return PipelineDeps(
        model=model,
        embedder=embedder,
        agent_factory=agent_factory,
        openapi=json.loads(Path(OPENAPI_PATH).read_text(encoding="utf-8")),
        business={"Acme Store"},
    )


def test_acme_pasa_del_archivo_subido_al_reporte_sin_intervencion(conn: psycopg.Connection) -> None:
    row = conn.execute(
        "insert into tenants (name) values ('Acme onboarding') returning id"
    ).fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    uploads = [
        Upload(n, (ACME / "onboarding" / n).read_bytes())
        for n in ("whatsapp_export.zip", "tickets.csv", "tickets.json")
    ]
    uploads += [Upload(p.name, p.read_bytes()) for p in sorted((ACME / "documents").glob("*.md"))]
    model = FakeOnboardingModel()

    run_id, report = run_pipeline(conn, tenant_id, uploads, _deps(conn, model))

    with tenant_scope(conn, tenant_id):
        stages = conn.execute(
            "select stages, status, report from onboarding_runs where id = %s", (run_id,)
        ).fetchone()
        active = conn.execute("select count(*) from case_types where active").fetchone()
        approved = conn.execute("select count(*) from tools where approved").fetchone()
    assert stages is not None and stages[1] == "done" and stages[2] == report
    assert [s for s in STAGES if stages[0][s]["estado"] == "hecha"] == list(STAGES)
    assert stages[0]["anonimizacion"]["resultado"]["datos_en_claro"] == 0
    assert report["conversaciones"] == 100
    assert len(report["tipos"]) >= 7
    assert sum(t["volumen"] for t in report["tipos"]) + report["sin_tipo"] == 100
    for kind in report["tipos"]:
        assert kind["evals"] >= 1 and 0 <= kind["porcentaje"] <= 1
    assert report["adversariales"]["total"] == 4
    assert 0 <= report["resolucion_estimada"] <= 1
    assert "| Tipo de caso |" in render_markdown(report)
    # El sandbox no deja nada activo: el cliente decide qué tipos y herramientas activar.
    assert active == (0,) and approved == (0,)

    # Retomar no repite etapas: ni el modelo ni la ingesta vuelven a correr.
    model.calls = 0
    _, again = run_pipeline(conn, tenant_id, uploads, _deps(conn, model))
    assert model.calls == 0 and again == report
