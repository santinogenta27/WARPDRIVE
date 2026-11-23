"""Arma el agente real para tests y evals.

`build_simulated_stack`: Claude, Voyage y la API de Acme simulados (sin claves).
`build_real_stack`: modelos y embeddings reales según las claves del entorno (Claude o Gemini,
Voyage o Gemini; ver app/llm/factory.py) y la API de Acme simulada como sandbox; las respuestas
del modelo se guardan en caché por entrada exacta, en una carpeta por proveedor.
"""

import re
import uuid
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import psycopg
from fastapi.testclient import TestClient

from acme_api import create_app
from app.agent.loop import Agent, AgentResult
from app.contacts import hash_address
from app.db.connection import tenant_scope
from app.demo.acme import AcmeSeed, seed_acme
from app.demo.northwind import NorthwindSeed
from app.llm.client import AnthropicProvider, LLMClient, LLMProvider, PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.llm.errors import QuotaExhausted
from app.llm.factory import embedder_from_env, llm_backend, provider_from_env
from app.llm.fallback import FallbackProvider
from app.mailer import Email, MailError
from app.policy.guardian import Guardian
from app.tools.runtime import ToolRuntime
from simulated.claude import SimulatedClaude
from simulated.voyage import voyage_transport


@dataclass
class RecordingMailer:
    """Mailer de los tests y evals: guarda los emails en lugar de enviarlos."""

    sent: list[Email] = field(default_factory=list)
    failing: bool = False  # simula al proveedor de email caído

    def send(self, email: Email) -> None:
        if self.failing:
            raise MailError("El proveedor de email respondió 503.")
        self.sent.append(email)


@dataclass
class SimulatedStack:
    conn: psycopg.Connection
    seed: AcmeSeed | NorthwindSeed
    agent: Agent
    acme: TestClient  # la API simulada de la empresa (Acme o Northwind)
    llm: LLMClient
    claude: SimulatedClaude | None = None  # None con claves reales
    mode: str = "simulated"
    results: list[AgentResult] = field(default_factory=list)
    mailer: RecordingMailer = field(default_factory=RecordingMailer)
    exhausted: Any = None  # ExhaustedProvider si el principal está sin cuota
    embedder: Any = None  # el mismo que usa el agente (para armar otro agente o ingerir)

    @property
    def exhausted_calls(self) -> int:
        return int(self.exhausted.calls) if self.exhausted is not None else 0

    @property
    def claude_requests(self) -> list[dict[str, Any]]:
        return self.claude.requests if self.claude else []

    def send(self, contact_label: str, text: str) -> AgentResult:
        result = self.agent.handle_message(
            self.seed.tenant_id, self.seed.contacts[contact_label], text
        )
        self.results.append(result)
        return result

    def add_contact(self, label: str) -> uuid.UUID:
        """Cliente nuevo sin verificar (como una sesión nueva del chat web)."""
        with tenant_scope(self.conn, self.seed.tenant_id):
            row = self.conn.execute(
                "insert into contacts (tenant_id, phone_hash) values (%s, %s) returning id",
                (self.seed.tenant_id, hash_address(f"web:eval-{uuid.uuid4().hex}")),
            ).fetchone()
        assert row is not None
        self.seed.contacts[label] = row[0]
        contact_id: uuid.UUID = row[0]
        return contact_id

    def last_code(self) -> str:
        """El último código de verificación enviado por email."""
        match = re.search(r"\b\d{6}\b", self.mailer.sent[-1].body)
        assert match is not None, "no se envió ningún código"
        return match.group(0)

    def close_open_cases(self) -> None:
        with tenant_scope(self.conn, self.seed.tenant_id):
            self.conn.execute("update cases set status = 'resolved' where status <> 'resolved'")

    def start_case(self) -> None:
        """Antes de cada caso de evals (decisión 040): la API simulada vuelve a sus pedidos
        iniciales, si lo permite (la de Northwind: un cupón de un caso no bloquea la cortesía del
        siguiente; la de Acme no cambia), y una aprobación que quedó pendiente no bloquea el
        mismo pedido en el caso siguiente."""
        reset = getattr(getattr(self.acme, "app", None), "state", None)
        if reset is not None and hasattr(reset, "reset"):
            reset.reset()
        with tenant_scope(self.conn, self.seed.tenant_id):
            self.conn.execute("update approvals set status = 'rejected' where status = 'pending'")
            # Cada caso con el cliente sin memoria (decisión 078), salvo la que siembra el caso:
            # lo que se recordó de un caso anterior no cambia el siguiente.
            self.conn.execute("delete from customer_memories")

    def acme_order(self, order_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self.acme.get(f"/orders/{order_id}").json()
        return data

    def case_message_roles(self, case_id: uuid.UUID) -> list[str]:
        with tenant_scope(self.conn, self.seed.tenant_id):
            rows = self.conn.execute(
                "select role from case_messages where case_id = %s order by created_at", (case_id,)
            ).fetchall()
        return [r[0] for r in rows]


@dataclass
class ExhaustedProvider:
    """Proveedor principal sin cuota diaria (como Groq agotado en Render)."""

    calls: int = 0

    def create(self, **kwargs: Any) -> Any:
        self.calls += 1
        raise QuotaExhausted("sin cuota (simulado)", daily=True)


def _company(
    conn: psycopg.Connection, company: str, embedder: Any, today: date | None = None
) -> tuple[AcmeSeed | NorthwindSeed, TestClient]:
    """La empresa de demo y su API simulada: `acme` (en español) o `northwind` (en inglés).
    Las fechas de Northwind son relativas a `today`: los tests y evals usan el día de sus
    fixtures (`DATES_AS_OF`), así no dependen del día en que se corren."""
    if company == "northwind":
        from app.demo.northwind import api_module, sandbox_client, seed_northwind

        fixtures_day: date = api_module().DATES_AS_OF
        return seed_northwind(conn, embedder), sandbox_client(today=today or fixtures_day)
    return seed_acme(conn, embedder), TestClient(create_app())


def build_simulated_stack(
    conn: psycopg.Connection,
    *,
    mode: str = "normal",
    primary_out_of_quota: bool = False,
    company: str = "acme",
    today: date | None = None,
) -> SimulatedStack:
    """`primary_out_of_quota`: el modelo simulado responde como respaldo de un principal sin
    cuota, con el mismo FallbackProvider que usa la demo (Groq agotado → Gemini). `company`:
    `acme` o `northwind` (decisión 038). `today`: el día de las fechas de Northwind (por
    defecto, el de sus fixtures)."""
    recorder = PostgresUsageRecorder(conn)
    embedder = VoyageEmbedder(
        recorder=recorder, api_key="sim", http_client=httpx.Client(transport=voyage_transport())
    )
    claude = SimulatedClaude(mode=mode)
    sdk = anthropic.Anthropic(
        api_key="sim", http_client=httpx2.Client(transport=claude.transport()), max_retries=0
    )
    simulated: LLMProvider = AnthropicProvider(client=sdk)
    exhausted = ExhaustedProvider()
    provider = FallbackProvider(exhausted, simulated) if primary_out_of_quota else simulated
    llm = LLMClient(provider=provider, recorder=recorder)
    seed, sandbox = _company(conn, company, embedder, today)
    guardian = Guardian(conn, ToolRuntime(sandbox))
    mailer = RecordingMailer()
    agent = Agent(conn, llm, embedder, guardian, mailer=mailer)
    return SimulatedStack(
        conn=conn,
        seed=seed,
        agent=agent,
        acme=sandbox,
        llm=llm,
        claude=claude,
        mode="simulated",
        mailer=mailer,
        exhausted=exhausted,
        embedder=embedder,
    )


def model_profile(backend: str | None) -> str:
    """Modelos reales del proveedor según el entorno (clave de la caché de los evals)."""
    if backend == "groq":
        from app.llm.groq import models_from_env as groq_models

        return "groq:" + ",".join(sorted(set(groq_models().values())))
    if backend == "gemini":
        from app.llm.gemini import models_from_env as gemini_models

        return "gemini:" + ",".join(sorted(set(gemini_models().values())))
    return str(backend)


def build_real_stack(
    conn: psycopg.Connection,
    cache_dir: Path,
    *,
    cache_models: bool = True,
    company: str = "acme",
) -> SimulatedStack:
    """Modelos y embeddings reales según las claves del entorno; Acme sigue simulada. Mide un
    solo proveedor: sin respaldo automático al otro gratuito. `cache_models=False`: cada pedido
    va al modelo (los embeddings siguen en caché)."""
    import os

    from app.llm.embedding_cache import CachingEmbedder
    from app.llm.factory import embedding_backend
    from simulated.cache import CachingProvider

    os.environ["LLM_FALLBACK"] = "off"
    backend = llm_backend()
    recorder = PostgresUsageRecorder(conn)
    # Los mismos documentos y consultas en cada corrida: sin caché se agota la cuota diaria.
    embedder = CachingEmbedder(
        embedder_from_env(recorder), cache_dir / "embeddings", profile=str(embedding_backend())
    )
    provider = (
        CachingProvider(
            provider_from_env(), cache_dir / str(backend), profile=model_profile(backend)
        )
        if cache_models
        else provider_from_env()
    )
    llm = LLMClient(provider=provider, recorder=recorder)
    seed, sandbox = _company(conn, company, embedder)
    guardian = Guardian(conn, ToolRuntime(sandbox))
    mailer = RecordingMailer()
    agent = Agent(conn, llm, embedder, guardian, mailer=mailer)
    mode = backend if backend in ("gemini", "groq") else "real"
    return SimulatedStack(
        conn=conn,
        seed=seed,
        agent=agent,
        acme=sandbox,
        llm=llm,
        mode=mode,
        mailer=mailer,
        embedder=embedder,
    )
