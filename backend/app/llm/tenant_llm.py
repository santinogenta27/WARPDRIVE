"""La API key de modelos de cada empresa (decisión 049).

Una empresa puede cargar su propia key de Anthropic, OpenAI, Groq o Gemini. Se guarda cifrada
con la clave de esa empresa en `tenants.settings.llm`. Con su key, el agente de esa empresa usa
ese proveedor y el gasto lo paga ella; sin key, usa el proveedor de la plataforma, con los
límites de `app.llm.spending`. Todo sigue pasando por `LLMClient`, que registra tokens y costo.
"""

import json
import os
import uuid
from collections.abc import Callable
from functools import lru_cache

import anthropic
import psycopg

from app.crypto import decrypt_for, encrypt_for
from app.db.connection import tenant_scope
from app.llm.client import AnthropicProvider, LLMProvider
from app.llm.gemini import GeminiProvider, GeminiTransport
from app.llm.groq import GroqProvider, GroqTransport

PROVIDERS = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI",
    "groq": "Groq",
    "gemini": "Gemini",
}
OPENAI_URL = "https://api.openai.com/v1/chat/completions"


def _openai_models() -> dict[str, str]:
    model = os.environ.get("OPENAI_AGENT_MODEL") or "gpt-5-mini"
    return {"claude-sonnet-5-5": model, "claude-opus-5-5": model, "claude-haiku-5-5": model}


def make_provider(provider: str, key: str) -> LLMProvider:
    if provider == "anthropic":
        return AnthropicProvider(anthropic.Anthropic(api_key=key, timeout=30.0, max_retries=3))
    if provider == "openai":
        # La misma API de chat completions que Groq, en otra dirección.
        return GroqProvider(GroqTransport(api_key=key, url=OPENAI_URL), models=_openai_models())
    if provider == "groq":
        return GroqProvider(GroqTransport(api_key=key))
    if provider == "gemini":
        return GeminiProvider(GeminiTransport(api_key=key))
    raise ValueError(f"Proveedor desconocido: {provider}")


@lru_cache(maxsize=64)
def _cached(provider: str, key: str) -> LLMProvider:
    """Un proveedor por key: el transporte recuerda los límites de cada una entre mensajes."""
    return make_provider(provider, key)


def _stored(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, str] | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings -> 'llm' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    found = row[0] if row else None
    return found if isinstance(found, dict) and found.get("key_enc") else None


def own_provider(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str | None:
    """El proveedor de la key propia de la empresa, o None si usa la de la plataforma."""
    found = _stored(conn, tenant_id)
    return found["provider"] if found else None


def save_key(conn: psycopg.Connection, tenant_id: uuid.UUID, provider: str, key: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(f"Proveedor desconocido: {provider}")
    key = key.strip()
    if not key or len(key) > 500 or any(c.isspace() for c in key):
        raise ValueError("La clave no es válida.")
    value = {"provider": provider, "key_enc": encrypt_for(tenant_id, key)}
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set settings = jsonb_set(settings, '{llm}', %s::jsonb) where id = %s",
            (json.dumps(value), tenant_id),
        )


def clear_key(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    with tenant_scope(conn, tenant_id):
        conn.execute("update tenants set settings = settings - 'llm' where id = %s", (tenant_id,))


def provider_for(
    conn: psycopg.Connection, tenant_id: uuid.UUID, shared: Callable[[], LLMProvider]
) -> LLMProvider:
    """El proveedor con la key de la empresa; sin key, el de la plataforma."""
    found = _stored(conn, tenant_id)
    if found is None:
        return shared()
    return _cached(found["provider"], decrypt_for(tenant_id, found["key_enc"]))
