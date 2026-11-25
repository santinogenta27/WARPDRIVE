"""La clave de Gemini propia de la prueba gratis (decisión 099).

La prueba gratis usa `TRIAL_GEMINI_API_KEY`, separada de la del resto del sistema: así el QA, las
demos o los clientes del panel no le agotan la cuota diaria (ni ella a ellos). Sin esa variable
usa la clave de siempre (`GEMINI_API_KEY`). La clave solo se lee del entorno.

Los vectores tienen que ser del mismo modelo que la búsqueda del agente: si la plataforma
indexa con Voyage, la prueba también (sus vectores no sirven para Gemini ni al revés).
"""

import os

import httpx

from app.llm.embedder import Embedder
from app.llm.factory import embedder_from_env, embedding_backend
from app.llm.gemini import GeminiEmbedder, GeminiTransport
from app.llm.types import UsageRecorder

ENV = "TRIAL_GEMINI_API_KEY"
# El armado no espera: una sola llamada, con timeout corto. Si falla, la prueba sigue con la
# búsqueda por texto y los vectores se completan después.
EMBED_TIMEOUT = httpx.Timeout(12.0, connect=5.0)


def api_key() -> str:
    return os.environ.get(ENV) or os.environ.get("GEMINI_API_KEY") or ""


def trial_embedder(recorder: UsageRecorder, *, http_client: httpx.Client | None = None) -> Embedder:
    """El embedder de la prueba gratis. Registra el uso en `usage` como cualquier otro."""
    if embedding_backend() == "voyage":
        return embedder_from_env(recorder)
    key = api_key()
    if not key:
        raise RuntimeError(f"Falta {ENV} o GEMINI_API_KEY en el entorno (embeddings)")
    transport = GeminiTransport(
        api_key=key,
        http_client=http_client or httpx.Client(timeout=EMBED_TIMEOUT),
        min_interval=0.5,
        max_retries=0,
    )
    return GeminiEmbedder(recorder, transport)
