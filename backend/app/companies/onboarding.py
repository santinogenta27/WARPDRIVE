"""Onboarding de una empresa desde el panel, sin comandos (decisión 026).

1. `add_sources`: la empresa sube su historial de chats y sus documentos. Se ingieren y se
   anonimizan en el momento; el archivo original no se guarda.
2. `start_run`: encola el job del pipeline (las 6 etapas del Hito 4). El avance de cada etapa
   queda en `onboarding_runs` y el panel lo muestra.
3. `run_onboarding_job`: lo que corre el worker. Si una etapa falla (por ejemplo, se agotó la
   cuota del modelo), el job se reintenta y retoma desde la primera etapa que falte.

El job es idempotente: cada etapa guarda su resultado y no se repite.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.companies import connection as system
from app.db.connection import tenant_scope
from app.jobs.queue import Job, enqueue
from app.llm.embedder import Embedder
from app.mailer import ConsoleMailer
from app.onboarding.ingest import IngestResult, Upload, ingest
from app.onboarding.pipeline import STAGES, PipelineDeps, run_pipeline

JOB_KIND = "onboarding_run"
MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # spec: archivo subido, 50 MB como máximo
ALLOWED_SUFFIXES = (".txt", ".zip", ".csv", ".json", ".md", ".docx", ".pdf")
STAGE_LABELS = {
    "ingesta": "Ingesta",
    "anonimizacion": "Anonimización",
    "descubrimiento": "Descubrimiento de casos",
    "evals": "Generación de pruebas",
    "herramientas": "Generación de acciones",
    "reporte": "Reporte de preparación",
}


class OnboardingError(ValueError):
    """Pedido inválido: el mensaje se le muestra a quien lo hizo."""


@dataclass(frozen=True)
class RunState:
    id: uuid.UUID
    status: str  # running, done o failed
    stages: dict[str, Any]
    report: dict[str, Any] | None
    queued: bool  # hay un job en la cola o corriendo


def business_names(conn: psycopg.Connection, tenant_id: uuid.UUID) -> set[str]:
    """Con qué nombres figura la empresa en sus chats exportados (para saber quién es el
    cliente y quién el equipo): su nombre y los que cargó."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select name, settings -> 'business_names' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    assert row is not None
    extra = row[1] if isinstance(row[1], list) else []
    return {str(row[0]), *(str(n) for n in extra if str(n).strip())}


def set_business_names(conn: psycopg.Connection, tenant_id: uuid.UUID, names: list[str]) -> None:
    clean = [n.strip()[:80] for n in names if n.strip()][:10]
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"business_names": clean}), tenant_id),
        )


def add_sources(
    conn: psycopg.Connection, tenant_id: uuid.UUID, uploads: list[Upload], embedder: Embedder
) -> IngestResult:
    for upload in uploads:
        if len(upload.data) > MAX_UPLOAD_BYTES:
            raise OnboardingError(f"{upload.name} supera los 50 MB.")
        if not upload.name.lower().endswith(ALLOWED_SUFFIXES):
            raise OnboardingError(
                f"{upload.name}: formato no soportado. Subí .txt o .zip de WhatsApp, .csv o "
                ".json de tickets, o documentos .md, .txt, .docx o .pdf."
            )
    try:
        return ingest(
            conn,
            tenant_id,
            uploads,
            business=business_names(conn, tenant_id),
            embedder=embedder,
            # Desde el panel hay dónde confirmar: un documento con datos de personas espera.
            hold_personal_data=True,
        )
    except (ValueError, KeyError, UnicodeDecodeError) as error:
        raise OnboardingError(f"No se pudo leer un archivo: {error}") from error


def sources(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[tuple[Any, ...]]:
    """(nombre, tipo, conversaciones, fecha) de cada fuente subida."""
    with tenant_scope(conn, tenant_id):
        return conn.execute(
            "select s.file_path, s.kind, (select count(*) from conversations c"
            " where c.source_id = s.id), s.created_at from sources s order by s.created_at"
        ).fetchall()


def _pending_job(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    # `jobs` es la cola del sistema (sin tenant): se filtra por el tenant del payload.
    row = conn.execute(
        "select 1 from jobs where kind = %s and payload ->> 'tenant_id' = %s"
        " and status in ('queued', 'running') limit 1",
        (JOB_KIND, str(tenant_id)),
    ).fetchone()
    return row is not None


def state(conn: psycopg.Connection, tenant_id: uuid.UUID) -> RunState | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select id, status, stages, report from onboarding_runs"
            " order by created_at desc limit 1"
        ).fetchone()
    if row is None:
        return None
    return RunState(row[0], row[1], dict(row[2]), row[3], _pending_job(conn, tenant_id))


def start_run(
    conn: psycopg.Connection, tenant_id: uuid.UUID, requested_by: uuid.UUID | None = None
) -> uuid.UUID:
    """Encola el pipeline de la empresa. Si la última corrida falló, la retoma; si terminó, abre
    una nueva (para sumar fuentes nuevas). Con una corrida ya en la cola, no encola otra."""
    with tenant_scope(conn, tenant_id):
        has_history = conn.execute("select 1 from conversations limit 1").fetchone()
    if has_history is None:
        raise OnboardingError("Primero subí un historial de chats o de tickets.")
    current = state(conn, tenant_id)
    if current is not None and current.queued:
        return current.id
    if current is None or current.status == "done":
        with tenant_scope(conn, tenant_id):
            row = conn.execute(
                "insert into onboarding_runs (tenant_id, requested_by) values (%s, %s)"
                " returning id",
                (tenant_id, requested_by),
            ).fetchone()
        assert row is not None
        run_id: uuid.UUID = row[0]
    else:
        run_id = current.id
        with tenant_scope(conn, tenant_id):
            conn.execute(
                "update onboarding_runs set status = 'running', updated_at = now() where id = %s",
                (run_id,),
            )
    enqueue(conn, JOB_KIND, {"tenant_id": str(tenant_id), "run_id": str(run_id)}, max_attempts=3)
    return run_id


DepsFactory = Callable[[psycopg.Connection, uuid.UUID], PipelineDeps]


def production_pipeline_deps(conn: psycopg.Connection, tenant_id: uuid.UUID) -> PipelineDeps:
    """Modelos y embeddings del entorno, con caché por entrada exacta: si la cuota del modelo
    gratuito corta una etapa, al retomarla las llamadas ya hechas no se repiten."""
    from app.agent.loop import Agent
    from app.companies.agent import shared_provider
    from app.config import REPO_ROOT
    from app.llm.cache import CachingProvider
    from app.llm.client import LLMClient, PostgresUsageRecorder
    from app.llm.embedding_cache import CachingEmbedder
    from app.llm.factory import embedder_from_env, embedding_backend, llm_backend
    from app.onboarding.model import LLMOnboardingModel
    from app.policy.guardian import Guardian
    from app.tools.runtime import ToolRuntime

    cache = REPO_ROOT / "evals" / ".cache" / "onboarding-panel" / str(tenant_id)
    recorder = PostgresUsageRecorder(conn)
    provider = CachingProvider(shared_provider(), cache / "modelo", profile=str(llm_backend()))
    llm = LLMClient(provider=provider, recorder=recorder)
    embedder = CachingEmbedder(
        embedder_from_env(recorder), cache / "embeddings", profile=str(embedding_backend())
    )

    def agent_factory(c: psycopg.Connection, runtime: ToolRuntime) -> Agent:
        # El replay de evals nunca envía emails reales (decisión 029).
        return Agent(c, llm, embedder, Guardian(c, runtime), mailer=ConsoleMailer())

    found = system.load(conn, tenant_id)
    return PipelineDeps(
        model=LLMOnboardingModel(llm, tenant_id),
        embedder=embedder,
        agent_factory=agent_factory,
        openapi=(found.openapi if found and found.openapi else {"paths": {}}),
        business=business_names(conn, tenant_id),
    )


def run_onboarding_job(
    conn: psycopg.Connection,
    job: Job,
    deps_factory: DepsFactory = production_pipeline_deps,
    *,
    commit: bool = True,
) -> None:
    """Handler del job: corre (o retoma) el pipeline de la empresa. Cada etapa se confirma al
    terminar, así el panel ve el avance y un reintento no repite lo hecho. `commit=False` es
    para los tests, que corren todo en una transacción que se revierte."""
    tenant_id = uuid.UUID(job.payload["tenant_id"])
    run_pipeline(conn, tenant_id, [], deps_factory(conn, tenant_id), commit=commit)


def progress(run: RunState | None) -> list[tuple[str, str, str, str]]:
    """(etapa, nombre, estado, detalle) de las 6 etapas, para el panel. Estados: `pendiente`,
    `en_curso`, `hecha` o `fallo`."""
    rows = []
    stages = run.stages if run else {}
    for stage in STAGES:
        info = stages.get(stage) or {}
        status = str(info.get("estado") or "pendiente")
        if status == "en_curso" and run is not None and not run.queued:
            status = "fallo"  # el proceso se cortó a mitad de la etapa
        rows.append((stage, STAGE_LABELS[stage], status, _detail(stage, info)))
    return rows


def _detail(stage: str, info: dict[str, Any]) -> str:
    if info.get("estado") == "fallo":
        return str(info.get("error") or "")
    result = info.get("resultado")
    if info.get("estado") != "hecha" or result is None:
        return ""
    if stage == "ingesta":
        return (
            f"{result.get('conversations', 0)} conversaciones y "
            f"{result.get('documents', 0)} documentos"
        )
    if stage == "anonimizacion":
        return f"{result.get('marcadores', 0)} datos personales reemplazados, 0 en claro"
    if stage == "descubrimiento":
        return f"{len(result)} temas"
    if stage == "evals":
        return f"{result.get('creados', 0)} casos de prueba"
    if stage == "herramientas":
        return f"{len(result)} acciones" if result else "sin OpenAPI: solo información"
    return f"resolvería solo el {round(float(result.get('resolucion_estimada') or 0) * 100)}%"
