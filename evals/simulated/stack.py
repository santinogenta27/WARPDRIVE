"""Arma el agente real con Claude, Voyage y la API de Acme simulados (sin claves)."""

import uuid
from dataclasses import dataclass, field
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
from app.policy.guardian import Guardian
from app.tools.runtime import ToolRuntime
from simulated.claude import SimulatedClaude
from simulated.voyage import voyage_transport


@dataclass
class SimulatedStack:
    conn: psycopg.Connection
    seed: AcmeSeed
    agent: Agent
    acme: TestClient
    claude: SimulatedClaude
    results: list[AgentResult] = field(default_factory=list)

    @property
    def claude_requests(self) -> list[dict[str, Any]]:
        return self.claude.requests

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


def build_simulated_stack(
    conn: psycopg.Connection, *, invent_when_unknown: bool = False
) -> SimulatedStack:
    recorder = PostgresUsageRecorder(conn)
    embedder = VoyageEmbedder(
        recorder=recorder, api_key="sim", http_client=httpx.Client(transport=voyage_transport())
    )
    claude = SimulatedClaude(invent_when_unknown=invent_when_unknown)
    sdk = anthropic.Anthropic(
        api_key="sim", http_client=httpx2.Client(transport=claude.transport()), max_retries=0
    )
    llm = LLMClient(provider=AnthropicProvider(client=sdk), recorder=recorder)
    acme = TestClient(create_app())
    guardian = Guardian(conn, ToolRuntime(acme))
    seed = seed_acme(conn, embedder)
    agent = Agent(conn, llm, embedder, guardian)
    return SimulatedStack(conn=conn, seed=seed, agent=agent, acme=acme, claude=claude)
