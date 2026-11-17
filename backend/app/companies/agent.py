"""El agente en vivo de cada empresa: los modelos del entorno y el runtime de herramientas que
le corresponde (su sistema real si activó la conexión; si no, el modo simulado).

Es el mismo agente para todas las empresas: lo único que cambia es el tenant (sus tipos de caso
activos, sus herramientas aprobadas y sus documentos, que el agente lee de la base) y a qué
sistema llegan las herramientas, siempre a través del guardián.
"""

import uuid
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

    runtime, _ = runtime_for(
        conn,
        tenant_id,
        demo=shared_acme,
        demos={northwind.BASE_URL: northwind.shared_northwind},
    )
    return Guardian(conn, runtime)


def build_agent(conn: psycopg.Connection, tenant_id: uuid.UUID) -> "Agent":
    from app.agent.loop import Agent
    from app.llm.client import LLMClient, PostgresUsageRecorder
    from app.llm.factory import embedder_from_env
    from app.llm.tenant_llm import provider_for

    recorder = PostgresUsageRecorder(conn)
    # Con la key propia de la empresa, su proveedor; si no, el de la plataforma (decisión 049).
    llm = LLMClient(provider=provider_for(conn, tenant_id, shared_provider), recorder=recorder)
    return Agent(conn, llm, embedder_from_env(recorder), build_guardian(conn, tenant_id))
