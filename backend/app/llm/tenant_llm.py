"""La API key de modelos de cada empresa (decisiones 049 y 102).

Una empresa puede cargar su propia key de Anthropic, OpenAI, Groq o Gemini y elegir el modelo de una
lista corta por proveedor (`CATALOG`). Se guarda cifrada con la clave de esa empresa en
`tenants.settings.llm`, junto con el proveedor, el modelo y una pista para mostrarla (`sk-…a1b2`).
La key nunca se escribe en logs, respuestas, HTML ni auditoría.

Con su key, el agente de esa empresa usa ese proveedor y el modelo elegido (los modelos livianos de
`LIGHT` clasifican y revisan); el uso se registra como `byok/<proveedor>/<modelo>`, con el precio de
lista del proveedor, porque lo paga ella. Sin key, usa el proveedor de la plataforma, con los
límites de `app.llm.spending`. Todo sigue pasando por `LLMClient`, que registra tokens y costo.

Antes de guardar una key se prueba con una llamada mínima (`check_key`): una key que no pasa no se
guarda. Los errores del proveedor se traducen a palabras simples (`explain`).
"""

import hashlib
import json
import logging
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

import anthropic
import httpx
import psycopg

from app.crypto import decrypt_for, encrypt_for
from app.db.connection import tenant_scope
from app.llm.client import AnthropicProvider, LLMClient, LLMProvider, PostgresUsageRecorder
from app.llm.errors import QuotaExhausted
from app.llm.gemini import GeminiProvider, GeminiTransport
from app.llm.groq import API_URL as GROQ_URL
from app.llm.groq import GroqProvider, GroqTransport
from app.llm.types import LLMResponse

logger = logging.getLogger(__name__)

PROVIDERS = {
    "anthropic": "Anthropic (Claude)",
    "openai": "OpenAI",
    "groq": "Groq",
    "gemini": "Gemini",
}
# A quién le llega la factura: "Billed to your Anthropic account".
ACCOUNTS = {
    "anthropic": "Anthropic",
    "openai": "OpenAI",
    "groq": "Groq",
    "gemini": "Google Gemini",
}
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
BYOK = "byok"
TESTED_FOR = 600.0  # segundos: una key probada bien no se vuelve a probar al guardarla
# Los modelos que pide el agente: el de las conversaciones y el liviano (clasificar, revisar).
AGENT_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5")
LIGHT_MODELS = ("claude-haiku-5-5",)


@dataclass(frozen=True)
class Model:
    id: str
    label: str
    note: str  # para qué sirve, en una línea


# Una lista corta y recomendada por proveedor (verificada el 25/11/2026, decisión 102). El primero
# es el recomendado.
CATALOG: dict[str, list[Model]] = {
    "anthropic": [
        Model(
            "claude-sonnet-5-5",
            "Claude Sonnet 5.5",
            "Recommended. The best answers for customer conversations.",
        ),
        Model(
            "claude-haiku-5-5",
            "Claude Haiku 5.5",
            "Fastest and about 20 times cheaper. Good for simple questions.",
        ),
    ],
    "openai": [
        Model("gpt-5-mini", "GPT-5 mini", "Recommended. Good answers at a low cost."),
        Model("gpt-5.4-mini", "GPT-5.4 mini", "Newer and stronger, about 3 times the cost."),
    ],
    "gemini": [
        Model(
            "gemini-3.8-flash",
            "Gemini 3.8 Flash",
            "Recommended. Google's most capable Flash model.",
        ),
        Model(
            "gemini-3.5-flash-lite",
            "Gemini 3.5 Flash-Lite",
            "Fastest and cheapest. Good for simple questions.",
        ),
    ],
    "groq": [
        Model(
            "openai/gpt-oss-120b",
            "GPT-OSS 120B",
            "Recommended. An open model with very fast replies.",
        ),
        Model("openai/gpt-oss-20b", "GPT-OSS 20B", "Smaller and cheaper."),
    ],
}
# El modelo liviano de cada proveedor: clasifica los mensajes y revisa las respuestas.
LIGHT = {
    "anthropic": "claude-haiku-5-5",
    "openai": "gpt-5-nano",
    "gemini": "gemini-3.5-flash-lite",
    "groq": "openai/gpt-oss-20b",
}
LIGHT_LABELS = {
    "claude-haiku-5-5": "Claude Haiku 5.5",
    "gpt-5-nano": "GPT-5 nano",
    "gemini-3.5-flash-lite": "Gemini 3.5 Flash-Lite",
    "openai/gpt-oss-20b": "GPT-OSS 20B",
}


def default_model(provider: str) -> str:
    return CATALOG[provider][0].id


def model_label(provider: str, model: str) -> str:
    for item in CATALOG.get(provider, []):
        if item.id == model:
            return item.label
    return LIGHT_LABELS.get(model, model)


def display_name(model: str) -> str:
    """El nombre para mostrar de un modelo de cualquier proveedor (el id si no está en la lista)."""
    for models in CATALOG.values():
        for item in models:
            if item.id == model:
                return item.label
    return LIGHT_LABELS.get(model, model)


def billed_model(provider: str, model: str) -> str:
    """Cómo se registra el uso con key propia: con el precio de lista del proveedor."""
    return f"{BYOK}/{provider}/{model}"


def from_billed(name: str) -> tuple[str, str] | None:
    """(proveedor, modelo) de un uso con key propia, o None si fue con la de la plataforma."""
    parts = name.split("/", 2)
    if len(parts) == 3 and parts[0] == BYOK and parts[1] in PROVIDERS:
        return parts[1], parts[2]
    return None


def mask(key: str) -> str:
    """La pista que se muestra: "sk-…a1b2" (nunca la key)."""
    key = key.strip()
    if len(key) < 12:
        return "…" + key[-2:]
    return f"{key[:3]}…{key[-4:]}"


# -- el proveedor de la empresa -----------------------------------------------------------------
class TenantProvider:
    """El proveedor con la key de la empresa: responde con el modelo elegido y registra el uso como
    `byok/<proveedor>/<modelo>`, para que el costo salga con el precio de lista del proveedor."""

    def __init__(self, inner: Any, provider: str, model: str) -> None:
        self.inner = inner
        self.provider = provider
        self.model = model

    def target(self, requested: str) -> str:
        if requested in LIGHT_MODELS:
            return LIGHT[self.provider]
        return self.model

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        **options: Any,
    ) -> LLMResponse:
        target = self.target(model)
        response: LLMResponse = self.inner.create(
            model=target, messages=messages, max_tokens=max_tokens, system=system, **options
        )
        # El nombre de nuestra lista, que es el que tiene precio (Anthropic devuelve el mismo id).
        return replace(response, model=billed_model(self.provider, target))


def _inner(provider: str, key: str, models: dict[str, str], quick: bool = False) -> Any:
    """El cliente de cada proveedor. `quick`: para probar la key, sin reintentos ni esperas."""
    if provider == "anthropic":
        client = anthropic.Anthropic(
            api_key=key, timeout=20.0 if quick else 30.0, max_retries=2 if quick else 3
        )
        return AnthropicProvider(client)
    if provider in ("openai", "groq"):
        url = OPENAI_URL if provider == "openai" else GROQ_URL
        if quick:
            fast = GroqTransport(
                api_key=key, url=url, http_client=httpx.Client(timeout=20.0), max_retries=2,
                min_tokens=0, max_wait=5.0,
            )  # fmt: skip
            return GroqProvider(fast, models=models)
        return GroqProvider(GroqTransport(api_key=key, url=url), models=models)
    if provider == "gemini":
        if quick:
            quick_transport = GeminiTransport(
                api_key=key, http_client=httpx.Client(timeout=30.0), max_retries=2,
                min_interval=0.0, max_wait=5.0,
            )  # fmt: skip
            return GeminiProvider(quick_transport, models=models)
        return GeminiProvider(GeminiTransport(api_key=key), models=models)
    raise ValueError(f"Proveedor desconocido: {provider}")


def _models(provider: str, model: str) -> dict[str, str]:
    light = LIGHT[provider]
    return {**dict.fromkeys(AGENT_MODELS, model), **dict.fromkeys(LIGHT_MODELS, light),
            model: model, light: light}  # fmt: skip


def make_provider(
    provider: str, key: str, model: str | None = None, *, quick: bool = False
) -> TenantProvider:
    if provider not in PROVIDERS:
        raise ValueError(f"Proveedor desconocido: {provider}")
    chosen = model or default_model(provider)
    return TenantProvider(_inner(provider, key, _models(provider, chosen), quick), provider, chosen)


@lru_cache(maxsize=64)
def _cached(provider: str, key: str, model: str) -> TenantProvider:
    """Un proveedor por key y modelo: el transporte recuerda los límites entre mensajes."""
    return make_provider(provider, key, model)


# -- lo guardado ---------------------------------------------------------------------------------
@dataclass(frozen=True)
class OwnKey:
    provider: str
    model: str
    hint: str


def _stored(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, str] | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings -> 'llm' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    found = row[0] if row else None
    return found if isinstance(found, dict) and found.get("key_enc") else None


def own_key(conn: psycopg.Connection, tenant_id: uuid.UUID) -> OwnKey | None:
    found = _stored(conn, tenant_id)
    if found is None:
        return None
    provider = found["provider"]
    known = {m.id for m in CATALOG.get(provider, [])}
    model = found.get("model") if found.get("model") in known else default_model(provider)
    return OwnKey(provider, str(model), str(found.get("hint") or "…"))


def own_provider(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str | None:
    """El proveedor de la key propia de la empresa, o None si usa la de la plataforma."""
    found = _stored(conn, tenant_id)
    return found["provider"] if found else None


def _valid(provider: str, key: str, model: str | None) -> tuple[str, str]:
    if provider not in PROVIDERS:
        raise ValueError(f"Proveedor desconocido: {provider}")
    key = key.strip()
    if not key or len(key) > 500 or any(c.isspace() for c in key):
        raise ValueError("The key isn't valid. Paste it again, without spaces.")
    chosen = model or default_model(provider)
    if chosen not in {m.id for m in CATALOG[provider]}:
        raise ValueError("Choose a model from the list.")
    return key, chosen


def save_key(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    provider: str,
    key: str,
    model: str | None = None,
) -> None:
    """Guarda la key (cifrada), el proveedor, el modelo y la pista. No la prueba: eso lo hace
    `check_key` antes (el panel nunca guarda una key que no pasó)."""
    key, chosen = _valid(provider, key, model)
    value = {
        "provider": provider,
        "model": chosen,
        "hint": mask(key),
        "key_enc": encrypt_for(tenant_id, key),
    }
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
    """El proveedor con la key de la empresa y su modelo; sin key, el de la plataforma."""
    found = _stored(conn, tenant_id)
    if found is None:
        return shared()
    own = own_key(conn, tenant_id)
    assert own is not None
    return _cached(own.provider, decrypt_for(tenant_id, found["key_enc"]), own.model)


# -- probar la key -------------------------------------------------------------------------------
@dataclass(frozen=True)
class KeyCheck:
    ok: bool
    # connected | invalid_key | no_credit | no_model | rate_limited | busy | unreachable | error
    code: str
    message: str


_tested: dict[str, float] = {}
_lock = threading.Lock()


def _fingerprint(tenant_id: uuid.UUID, provider: str, model: str, key: str) -> str:
    return hashlib.sha256(f"{tenant_id}|{provider}|{model}|{key}".encode()).hexdigest()


def reset_tested() -> None:
    with _lock:
        _tested.clear()


def recently_tested(tenant_id: uuid.UUID, provider: str, model: str, key: str) -> bool:
    with _lock:
        until = _tested.get(_fingerprint(tenant_id, provider, model, key), 0.0)
    return time.monotonic() < until


def _status(error: BaseException) -> tuple[int | None, str]:
    """El estado HTTP y el texto del error (en minúsculas, solo para clasificarlo)."""
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code, error.response.text.lower()
    if isinstance(error, anthropic.APIStatusError):
        return error.status_code, str(error.message).lower()
    return None, str(error).lower()


def explain(error: BaseException, provider: str, model: str) -> KeyCheck:
    """El error del proveedor en palabras simples. Nunca su texto crudo (puede traer la key)."""
    account = ACCOUNTS.get(provider, provider)
    label = model_label(provider, model)
    if isinstance(error, QuotaExhausted):
        if getattr(error, "daily", False):
            return KeyCheck(False, "no_credit", _NO_CREDIT.format(account=account))
        return KeyCheck(False, "rate_limited", _RATE.format(account=account))
    if isinstance(error, (httpx.TransportError, anthropic.APIConnectionError, TimeoutError)):
        return KeyCheck(False, "unreachable", _UNREACHABLE.format(account=account))
    status, text = _status(error)
    if any(w in text for w in ("credit balance", "insufficient_quota", "billing", "exceeded your")):
        return KeyCheck(False, "no_credit", _NO_CREDIT.format(account=account))
    if status == 402:
        return KeyCheck(False, "no_credit", _NO_CREDIT.format(account=account))
    if status == 404 or ("model" in text and any(
        w in text for w in ("not found", "does not exist", "not have access", "not supported")
    )):  # fmt: skip
        return KeyCheck(False, "no_model", _NO_MODEL.format(label=label, account=account))
    if status in (401, 403) or any(
        w in text for w in ("api key not valid", "invalid api key", "incorrect api key",
                            "invalid x-api-key", "api_key_invalid", "authentication")
    ):  # fmt: skip
        return KeyCheck(False, "invalid_key", _INVALID.format(account=account))
    if status == 429:
        return KeyCheck(False, "rate_limited", _RATE.format(account=account))
    if status is not None and status >= 500:
        return KeyCheck(False, "busy", _BUSY.format(account=account))
    shown = f" (HTTP {status})" if status else ""
    return KeyCheck(False, "error", _OTHER.format(account=account, status=shown))


_INVALID = "This key isn't valid. Check that you copied the whole key from your {account} account."
_NO_CREDIT = (
    "The key works, but the account has no credit or quota left. Add credit in your {account} "
    "billing settings and try again."
)
_NO_MODEL = (
    "The key works, but it doesn't have access to {label}. Choose another model or enable it in "
    "your {account} account."
)
_RATE = "{account} is limiting requests right now. Wait a minute and test the key again."
_BUSY = "{account} is busy right now and couldn't answer. Test the key again in a minute."
_UNREACHABLE = "We couldn't reach {account}. Check your connection and try again in a minute."
_OTHER = "{account} rejected the test call{status}. Check the key and try again."


def check_key(
    conn: psycopg.Connection, tenant_id: uuid.UUID, provider: str, key: str, model: str | None
) -> KeyCheck:
    """Una llamada mínima con la key (por `LLMClient`, con el uso en `usage`). Devuelve
    "Connected" o el error en palabras simples. No guarda nada."""
    try:
        key, chosen = _valid(provider, key, model)
    except ValueError as error:
        return KeyCheck(False, "invalid_key", str(error))
    llm = LLMClient(
        provider=make_provider(provider, key, chosen, quick=True),
        recorder=PostgresUsageRecorder(conn),
    )
    try:
        with conn.transaction():
            llm.complete(
                tenant_id=tenant_id,
                model=AGENT_MODELS[0],
                messages=[{"role": "user", "content": "Reply with the single word OK."}],
                max_tokens=16,
                role="key_test",
            )
    except Exception as error:  # noqa: BLE001 — cualquier falla se explica, sin su texto
        found = explain(error, provider, chosen)
        logger.info("Prueba de key de %s para %s: %s", provider, tenant_id, found.code)
        return found
    with _lock:
        _tested[_fingerprint(tenant_id, provider, chosen, key)] = time.monotonic() + TESTED_FOR
    label = model_label(provider, chosen)
    return KeyCheck(True, "connected", f"Connected to {ACCOUNTS[provider]} with {label}.")


def set_model(conn: psycopg.Connection, tenant_id: uuid.UUID, model: str) -> KeyCheck:
    """Cambia el modelo de la key guardada, después de probarlo con esa key."""
    found = _stored(conn, tenant_id)
    if found is None:
        return KeyCheck(False, "error", "There's no key saved. Add one first.")
    key = decrypt_for(tenant_id, found["key_enc"])
    provider = found["provider"]
    result = (
        KeyCheck(True, "connected", "Connected.")
        if recently_tested(tenant_id, provider, model, key)
        else check_key(conn, tenant_id, provider, key, model)
    )
    if result.ok:
        save_key(conn, tenant_id, provider, key, model)
    return result
