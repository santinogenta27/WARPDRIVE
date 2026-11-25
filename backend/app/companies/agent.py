"""El agente en vivo de cada empresa: los modelos del entorno y el runtime de herramientas que
le corresponde (su sistema real si activó la conexión; si no, el modo simulado).

Es el mismo agente para todas las empresas: lo único que cambia es el tenant (sus tipos de caso
activos, sus herramientas aprobadas y sus documentos, que el agente lee de la base) y a qué
sistema llegan las herramientas, siempre a través del guardián.
"""

import uuid
from collections.abc import Callable
from functools import lru_cache
from typing import TYPE_CHECKING

import psycopg

from app.companies.connection import runtime_for
from app.llm.client import LLMProvider

if TYPE_CHECKING:
    from app.agent.loop import Agent
    from app.policy.guardian import Guardian


@lru_cache(maxsize=1)
def shared_provider() -> LLMProvider:
    """Un proveedor por proceso: así el respaldo entre gratuitos recuerda que el principal se
    quedó sin cuota en lugar de volver a probarlo en cada mensaje."""
    from app.llm.factory import provider_from_env

    return provider_from_env()


def build_guardian(conn: psycopg.Connection, tenant_id: uuid.UUID) -> "Guardian":
    from app.demo import northwind
    from app.demo.acme import shared_acme
    from app.policy.guardian import Guardian
    from app.trial import service as trial

    runtime, _ = runtime_for(
        conn,
        tenant_id,
        demo=shared_acme,
        # La API simulada de una prueba gratis solo se arma si la empresa apunta ahí (075).
        demos={northwind.BASE_URL: northwind.shared_northwind, **trial.sandboxes(conn, tenant_id)},
    )
    return Guardian(conn, runtime)


def _trial_agent(conn: psycopg.Connection, tenant_id: uuid.UUID) -> "Agent | None":
    from app.trial import models

    return models.agent_for(conn, tenant_id)


def build_agent(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    trial_agent: Callable[[psycopg.Connection, uuid.UUID], "Agent | None"] | None = None,
) -> "Agent":
    """`trial_agent`: arma el agente de una prueba gratis, con su propia clave (decisión 100). Por
    defecto, el de `app.trial`, que para cualquier empresa que no es una prueba en curso devuelve
    None: su agente queda exactamente como antes."""
    from app.agent.loop import Agent
    from app.llm.client import LLMClient, PostgresUsageRecorder
    from app.llm.factory import embedder_from_env
    from app.llm.tenant_llm import provider_for

    trial = (trial_agent or _trial_agent)(conn, tenant_id)
    if trial is not None:
        return trial
    recorder = PostgresUsageRecorder(conn)
    # Con la key propia de la empresa, su proveedor; si no, el de la plataforma (decisión 049).
    llm = LLMClient(provider=provider_for(conn, tenant_id, shared_provider), recorder=recorder)
    return Agent(conn, llm, embedder_from_env(recorder), build_guardian(conn, tenant_id))
