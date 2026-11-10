"""Pipeline de autoconfiguración (Hito 4): seis etapas, del archivo subido al reporte.

Cada etapa es idempotente y guarda su resultado en `onboarding_runs.stages`: si una falla (por
ejemplo, se agota la cuota del modelo gratuito), la próxima corrida retoma desde la primera que
falte, sin repetir lo hecho. Con `commit=True` (CLI), cada etapa se confirma al terminar.

Uso: python -m app.onboarding.pipeline   (con PYTHONPATH=backend; ver docs/onboarding.md)
"""

import argparse
import json
import re
import sys
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import psycopg
from psycopg.types.json import Jsonb

from app.db.connection import tenant_scope
from app.llm.embedder import Embedder
from app.onboarding.discover import ClusterName, Summary, discover_case_types
from app.onboarding.evalgen import generate_evals
from app.onboarding.ingest import Upload, ingest
from app.onboarding.report import AgentFactory, readiness_report, render_markdown
from app.onboarding.toolgen import generate_tools

STAGES = ("ingesta", "anonimizacion", "descubrimiento", "evals", "herramientas", "reporte")
# Rastros de datos personales que no pueden quedar en los mensajes guardados (control de la
# etapa de anonimización).
PII = re.compile(
    r"[\w.+-]+@[\w-]+\.[\w.]+|\+?54[ -]?9?[ -]?\d{2,4}[ -]?\d{3,4}[ -]?\d{4}"
    r"|\b\d{1,2}\.\d{3}\.\d{3}\b|\b(?:20|23|24|27|30|33|34)-\d{8}-\d\b"
)


class OnboardingModel(Protocol):
    def summarize(self, batch: list[tuple[str, str]]) -> dict[str, Summary]: ...

    def name_clusters(self, clusters: list[list[Summary]]) -> list[ClusterName]: ...

    def make_evals(
        self, case_type: str, goal: str, conversations: list[tuple[str, str]]
    ) -> list[dict[str, Any]]: ...

    def pick_operations(
        self, case_types: list[tuple[str, str]], operations: list[tuple[str, str]]
    ) -> dict[str, list[str]]: ...


@dataclass
class PipelineDeps:
    model: OnboardingModel
    embedder: Embedder
    agent_factory: AgentFactory
    openapi: dict[str, Any]
    business: set[str]
    batch_size: int = 10
    evals_per_type: int = 3


def _run(conn: psycopg.Connection, tenant_id: uuid.UUID) -> tuple[uuid.UUID, dict[str, Any]]:
    """La corrida en curso (o la última terminada) del tenant, o una nueva."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select id, stages from onboarding_runs order by created_at desc limit 1"
        ).fetchone()
        if row is None:
            row = conn.execute(
                "insert into onboarding_runs (tenant_id) values (%s) returning id, stages",
                (tenant_id,),
            ).fetchone()
    assert row is not None
    return row[0], dict(row[1])


def _save(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    run_id: uuid.UUID,
    stages: dict[str, Any],
    *,
    status: str = "running",
    report: dict[str, Any] | None = None,
) -> None:
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update onboarding_runs set stages = %s, status = %s, report = coalesce(%s, report),"
            " updated_at = now() where id = %s",
            (Jsonb(stages), status, Jsonb(report) if report is not None else None, run_id),
        )


def check_anonymized(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, int]:
    with tenant_scope(conn, tenant_id):
        texts = [r[0] for r in conn.execute("select text from messages").fetchall()]
    leaks = sum(len(PII.findall(text)) for text in texts)
    if leaks:
        raise RuntimeError(f"Quedaron {leaks} datos personales sin anonimizar")
    markers = sum(len(re.findall(r"<[A-Z]+_\d+>", text)) for text in texts)
    return {"mensajes": len(texts), "marcadores": markers, "datos_en_claro": 0}


def run_pipeline(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    uploads: list[Upload],
    deps: PipelineDeps,
    *,
    commit: bool = False,
    log: Callable[[str], None] = lambda message: None,
) -> tuple[uuid.UUID, dict[str, Any]]:
    run_id, stages = _run(conn, tenant_id)
    steps: dict[str, Callable[[], Any]] = {
        "ingesta": lambda: (
            ingest(
                conn, tenant_id, uploads, business=deps.business, embedder=deps.embedder
            ).__dict__
        ),
        "anonimizacion": lambda: check_anonymized(conn, tenant_id),
        "descubrimiento": lambda: [
            {"nombre": t.name, "volumen": t.volume, "objetivo": t.goal}
            for t in discover_case_types(
                conn,
                tenant_id,
                deps.model,
                deps.embedder,
                batch_size=deps.batch_size,
                # Con commit, cada lote resumido queda guardado aunque la etapa se corte.
                checkpoint=conn.commit if commit else lambda: None,
            )
        ],
        "evals": lambda: {
            "creados": generate_evals(conn, tenant_id, deps.model, per_type=deps.evals_per_type)
        },
        "herramientas": lambda: [
            {"nombre": m["name"], "riesgo": m["risk"]}
            for m in generate_tools(conn, tenant_id, deps.model, deps.openapi)
        ],
        "reporte": lambda: readiness_report(conn, tenant_id, deps.openapi, deps.agent_factory),
    }
    report: dict[str, Any] | None = stages.get("reporte", {}).get("resultado")
    for stage in STAGES:
        if stages.get(stage, {}).get("estado") == "hecha":
            log(f"{stage}: ya estaba hecha")
            continue
        log(f"{stage}: empieza")
        try:
            result = steps[stage]()
        except Exception as error:
            stages[stage] = {"estado": "fallo", "error": str(error)[:500]}
            if commit:
                conn.rollback()
            _save(conn, tenant_id, run_id, stages, status="failed")
            if commit:
                conn.commit()
            raise
        stages[stage] = {
            "estado": "hecha",
            "terminada": datetime.now(UTC).isoformat(timespec="seconds"),
            "resultado": result,
        }
        if stage == "reporte":
            report = result
        _save(
            conn,
            tenant_id,
            run_id,
            stages,
            status="done" if stage == "reporte" else "running",
            report=report,
        )
        if commit:
            conn.commit()
        log(f"{stage}: hecha")
    assert report is not None
    return run_id, report


def main(argv: list[str] | None = None) -> int:
    import os

    from app.config import REPO_ROOT, load_dotenv
    from app.db.connection import connect
    from app.db.migrate import migrate
    from app.llm.cache import CachingProvider
    from app.llm.client import LLMClient, PostgresUsageRecorder
    from app.llm.embedding_cache import CachingEmbedder
    from app.llm.factory import (
        embedder_from_env,
        embedding_backend,
        llm_backend,
        provider_from_env,
    )
    from app.onboarding.model import LLMOnboardingModel
    from app.policy.guardian import Guardian

    acme = REPO_ROOT / "evals" / "fixtures" / "acme"
    parser = argparse.ArgumentParser(description="Onboarding de una empresa (Hito 4)")
    parser.add_argument("--tenant", default="Acme Store (onboarding)")
    parser.add_argument("--history", type=Path, default=acme / "onboarding")
    parser.add_argument("--documents", type=Path, default=acme / "documents")
    parser.add_argument("--openapi", type=Path, default=REPO_ROOT / "mocks/acme-api/openapi.json")
    parser.add_argument("--report", type=Path, default=REPO_ROOT / "evals" / "last_onboarding.md")
    parser.add_argument("--evals-per-type", type=int, default=3)
    args = parser.parse_args(argv)

    load_dotenv()
    migrate()
    conn = connect()
    row = conn.execute("select id from tenants where name = %s", (args.tenant,)).fetchone()
    if row is None:
        row = conn.execute(
            "insert into tenants (name, settings) values (%s, %s) returning id",
            (args.tenant, Jsonb({"tone": "rioplatense"})),
        ).fetchone()
        conn.commit()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    recorder = PostgresUsageRecorder(conn)
    # Caché por entrada exacta: si la cuota del modelo gratuito corta una etapa, al retomarla
    # las llamadas ya hechas no se vuelven a pagar.
    provider = CachingProvider(
        provider_from_env(),
        REPO_ROOT / "evals" / ".cache" / "onboarding",
        profile=f"{llm_backend()}:{os.environ.get('GROQ_AGENT_MODEL', '')}:"
        f"{os.environ.get('GROQ_CLASSIFIER_MODEL', '')}",
    )
    llm = LLMClient(provider=provider, recorder=recorder)
    # Lo mismo con los embeddings, que también tienen cuota diaria gratuita.
    embedder = CachingEmbedder(
        embedder_from_env(recorder),
        REPO_ROOT / "evals" / ".cache" / "onboarding-embeddings",
        profile=str(embedding_backend()),
    )
    from app.agent.loop import Agent

    def agent_factory(c: psycopg.Connection, runtime: Any) -> Agent:
        return Agent(c, llm, embedder, Guardian(c, runtime))

    uploads = [
        Upload(path.name, path.read_bytes())
        for path in sorted(args.history.iterdir())
        if path.suffix in (".zip", ".csv") or path.name == "tickets.json"
    ] + [Upload(p.name, p.read_bytes()) for p in sorted(args.documents.glob("*.md"))]
    deps = PipelineDeps(
        model=LLMOnboardingModel(llm, tenant_id),
        embedder=embedder,
        agent_factory=agent_factory,
        openapi=json.loads(args.openapi.read_text(encoding="utf-8")),
        business={"Acme Store"},
        evals_per_type=args.evals_per_type,
    )
    try:
        run_id, report = run_pipeline(
            conn,
            tenant_id,
            uploads,
            deps,
            commit=True,
            log=lambda message: print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True),
        )
    finally:
        conn.close()
    markdown = render_markdown(report)
    args.report.write_text(markdown, encoding="utf-8")
    print(markdown)
    print(f"Corrida {run_id}. Reporte en {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
