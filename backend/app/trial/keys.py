"""La clave de Gemini de la prueba gratis (decisiones 099 y 100).

La prueba gratis usa SOLO `TRIAL_GEMINI_API_KEY`, y esa clave la usa SOLO la prueba. Es el único
lugar que lee la variable (un test lo verifica).

- El armado, los embeddings de fondo y el chat del agente de prueba van con ella, por
  `LLMClient` y `Embedder`, con el uso registrado en `usage`.
- Nunca cae en las claves de la plataforma (Groq, `GEMINI_API_KEY` ni Voyage), tampoco de
  respaldo. Sin la clave, o con su cuota agotada, el proveedor y el embedder lanzan `TrialBusy`.
  El armado sigue con búsqueda por texto y el chat le dice al visitante que la demo está ocupada.
- Después de la cuota DIARIA agotada, `BUSY_SECONDS` sin volver a llamar: así no se insiste en
  cada mensaje. Un 429 por minuto no bloquea nada: se renueva solo.
"""

import os
import threading
import time
import uuid
from functools import lru_cache
from typing import Any

import httpx

from app.llm.embedder import Embedder, InputType
from app.llm.errors import QuotaExhausted
from app.llm.gemini import GeminiEmbedder, GeminiProvider, GeminiTransport
from app.llm.types import LLMResponse, UsageRecorder

ENV = "TRIAL_GEMINI_API_KEY"
BUSY_SECONDS = 300.0
# El armado no espera: una sola llamada, con timeout corto. Si falla, sigue por texto.
EMBED_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
CHAT_TIMEOUT = httpx.Timeout(60.0, connect=5.0)
_lock = threading.Lock()
_busy_until = 0.0


class TrialBusy(QuotaExhausted):
    """La prueba no tiene su clave o se le agotó la cuota."""

    def __init__(self, message: str = "La demo no tiene cuota disponible") -> None:
        super().__init__(message, daily=True)


def api_key() -> str:
    return os.environ.get(ENV) or ""


def http_client(timeout: httpx.Timeout = CHAT_TIMEOUT) -> httpx.Client:
    return httpx.Client(timeout=timeout)


def busy() -> bool:
    return time.monotonic() < _busy_until


def mark_busy() -> None:
    global _busy_until
    with _lock:
        _busy_until = time.monotonic() + BUSY_SECONDS


def reset() -> None:
    global _busy_until
    with _lock:
        _busy_until = 0.0
    _chat_transport.cache_clear()


def _check() -> str:
    key = api_key()
    if not key:
        raise TrialBusy(f"Falta {ENV}")
    if busy():
        raise TrialBusy("La cuota de la prueba está agotada")
    return key


@lru_cache(maxsize=4)
def _chat_transport(key: str) -> GeminiTransport:
    """Uno por clave y por proceso: respeta la pausa entre llamadas de un mensaje al otro. Pocos
    reintentos: el visitante está esperando la respuesta."""
    return GeminiTransport(api_key=key, http_client=http_client(), max_retries=2, max_wait=15.0)


class TrialProvider:
    """El modelo de la prueba: Gemini con la clave de la prueba. `on_busy` se llama al lanzar
    `TrialBusy` (el agente de prueba cambia el mensaje)."""

    def __init__(self, on_busy: Any = None) -> None:
        self._on_busy = on_busy or (lambda: None)

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        **options: Any,
    ) -> LLMResponse:
        try:
            provider = GeminiProvider(_chat_transport(_check()))
            return provider.create(
                model=model, messages=messages, max_tokens=max_tokens, system=system, **options
            )
        except QuotaExhausted as error:
            self._on_busy()
            if error.daily and not isinstance(error, TrialBusy):  # la por minuto se renueva sola
                mark_busy()
            raise TrialBusy(str(error)) from error


class TrialEmbedder:
    """Los embeddings de la prueba (el armado, el fondo y las búsquedas del chat)."""

    dimensions = GeminiEmbedder.dimensions

    def __init__(
        self,
        recorder: UsageRecorder,
        *,
        transport: GeminiTransport | None = None,
        on_busy: Any = None,
    ) -> None:
        self._recorder = recorder
        self._transport = transport
        self._on_busy = on_busy or (lambda: None)

    def embed(
        self,
        texts: list[str],
        *,
        tenant_id: uuid.UUID,
        input_type: InputType = "document",
        case_id: uuid.UUID | None = None,
    ) -> list[list[float]]:
        try:
            key = _check()
            transport = self._transport or _chat_transport(key)
            return GeminiEmbedder(self._recorder, transport).embed(
                texts, tenant_id=tenant_id, input_type=input_type, case_id=case_id
            )
        except QuotaExhausted as error:
            self._on_busy()
            if error.daily and not isinstance(error, TrialBusy):  # la por minuto se renueva sola
                mark_busy()
            raise TrialBusy(str(error)) from error


def trial_embedder(recorder: UsageRecorder, *, client: httpx.Client | None = None) -> Embedder:
    """El embedder del armado: una sola llamada con timeout corto, sin esperar reintentos."""
    key = api_key()
    transport = None
    if key:
        transport = GeminiTransport(
            api_key=key,
            http_client=client or http_client(EMBED_TIMEOUT),
            min_interval=0.5,
            max_retries=0,
        )
    return TrialEmbedder(recorder, transport=transport)
