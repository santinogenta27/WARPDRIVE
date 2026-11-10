"""Arma el agente real para tests y evals.

`build_simulated_stack`: Claude, Voyage y la API de Acme simulados (sin claves).
`build_real_stack`: modelos y embeddings reales según las claves del entorno (Claude o Gemini,
Voyage o Gemini; ver app/llm/factory.py) y la API de Acme simulada como sandbox; las respuestas
del modelo se guardan en caché por entrada exacta, en una carpeta por proveedor.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic
import httpx
import httpx2
import psycopg
from fastapi.testclient import TestClient

from acme_api import create_app
from app.agent.loop import Agent, AgentResult
from app.db.connection import tenant_scope
from app.demo.acme import AcmeSeed, seed_acme
from app.llm.client import AnthropicProvider, LLMClient, PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.llm.factory import embedder_from_env, llm_backend, provider_from_env
from app.mailer import Email
from app.policy.guardian import Guardian
from app.tools.runtime import ToolRuntime
from simulated.claude import SimulatedClaude
from simulated.voyage import voyage_transport


@dataclass
class RecordingMailer:
    """Mailer de los tests y evals: guarda los emails en lugar de enviarlos."""

    sent: list[Email] = field(default_factory=list)

    def send(self, email: Email) -> None:
        self.sent.append(email)


@dataclass
class SimulatedStack:
    conn: psycopg.Connection
    seed: AcmeSeed
    agent: Agent
    acme: TestClient
    llm: LLMClient
    claude: SimulatedClaude | None = None  # None con claves reales
    mode: str = "simulated"
    results: list[AgentResult] = field(default_factory=list)
    mailer: RecordingMailer = field(default_factory=RecordingMailer)

    @property
    def claude_requests(self) -> list[dict[str, Any]]:
        return self.claude.requests if self.claude else []

    def send(self, contact_label: str, text: str) -> AgentResult:
        result = self.agent.handle_message(
            self.seed.tenant_id, self.seed.contacts[contact_label], text
        )
        self.results.append(result)
        return result

    def close_open_cases(self) -> None:
        with tenant_scope(self.conn, self.seed.tenant_id):
            self.conn.execute("update cases set status = 'resolved' where status <> 'resolved'")

    def acme_order(self, order_id: str) -> dict[str, Any]:
        data: dict[str, Any] = self.acme.get(f"/orders/{order_id}").json()
        return data

    def case_message_roles(self, case_id: uuid.UUID) -> list[str]:
        with tenant_scope(self.conn, self.seed.tenant_id):
            rows = self.conn.execute(
                "select role from case_messages where case_id = %s order by created_at", (case_id,)
            ).fetchall()
        return [r[0] for r in rows]


def build_simulated_stack(conn: psycopg.Connection, *, mode: str = "normal") -> SimulatedStack:
    recorder = PostgresUsageRecorder(conn)
    embedder = VoyageEmbedder(
        recorder=recorder, api_key="sim", http_client=httpx.Client(transport=voyage_transport())
    )
    claude = SimulatedClaude(mode=mode)
    sdk = anthropic.Anthropic(
        api_key="sim", http_client=httpx2.Client(transport=claude.transport()), max_retries=0
    )
    llm = LLMClient(provider=AnthropicProvider(client=sdk), recorder=recorder)
    acme = TestClient(create_app())
    guardian = Guardian(conn, ToolRuntime(acme))
    seed = seed_acme(conn, embedder)
    mailer = RecordingMailer()
    agent = Agent(conn, llm, embedder, guardian, mailer=mailer)
    return SimulatedStack(
        conn=conn,
        seed=seed,
        agent=agent,
        acme=acme,
        llm=llm,
        claude=claude,
        mode="simulated",
        mailer=mailer,
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


def build_real_stack(conn: psycopg.Connection, cache_dir: Path) -> SimulatedStack:
    """Modelos y embeddings reales según las claves del entorno; Acme sigue simulada. Mide un
    solo proveedor: sin respaldo automático al otro gratuito."""
    import os

    from simulated.cache import CachingProvider

    from app.llm.embedding_cache import CachingEmbedder
    from app.llm.factory import embedding_backend

    os.environ["LLM_FALLBACK"] = "off"
    backend = llm_backend()
    recorder = PostgresUsageRecorder(conn)
    # Los mismos documentos y consultas en cada corrida: sin caché se agota la cuota diaria.
    embedder = CachingEmbedder(
        embedder_from_env(recorder), cache_dir / "embeddings", profile=str(embedding_backend())
    )
    provider = CachingProvider(
        provider_from_env(), cache_dir / str(backend), profile=model_profile(backend)
    )
    llm = LLMClient(provider=provider, recorder=recorder)
    acme = TestClient(create_app())
    guardian = Guardian(conn, ToolRuntime(acme))
    seed = seed_acme(conn, embedder)
    mailer = RecordingMailer()
    agent = Agent(conn, llm, embedder, guardian, mailer=mailer)
    mode = backend if backend in ("gemini", "groq") else "real"
    return SimulatedStack(
        conn=conn, seed=seed, agent=agent, acme=acme, llm=llm, mode=mode, mailer=mailer
    )
