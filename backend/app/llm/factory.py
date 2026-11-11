"""Elige proveedor de modelos y de embeddings según el entorno.

Modelos: Claude si hay ANTHROPIC_API_KEY. Si no, `LLM_PROVIDER` (gemini o groq) elige entre
los gratuitos; sin esa variable, Gemini si hay GEMINI_API_KEY y si no Groq si hay GROQ_API_KEY.
Si están las claves de los dos gratuitos, el otro queda de respaldo cuando el elegido se queda sin
cuota (FallbackProvider), salvo con LLM_FALLBACK=off (los evals miden un solo modelo).
Embeddings: Voyage si hay VOYAGE_API_KEY; si no, Gemini si hay GEMINI_API_KEY (Groq no tiene
embeddings). Cambiar de embedder obliga a reindexar: los vectores de uno no sirven para el otro.
"""

import os

from app.llm.client import AnthropicProvider, LLMProvider
from app.llm.embedder import Embedder, VoyageEmbedder
from app.llm.fallback import FallbackProvider
from app.llm.gemini import GeminiEmbedder, GeminiProvider
from app.llm.groq import GroqProvider
from app.llm.types import UsageRecorder

FREE_KEYS = {"gemini": "GEMINI_API_KEY", "groq": "GROQ_API_KEY"}


def llm_backend() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    chosen = (os.environ.get("LLM_PROVIDER") or "").strip().lower()
    if chosen in FREE_KEYS and os.environ.get(FREE_KEYS[chosen]):
        return chosen
    for backend in ("gemini", "groq"):
        if os.environ.get(FREE_KEYS[backend]):
            return backend
    return None


def embedding_backend() -> str | None:
    if os.environ.get("VOYAGE_API_KEY"):
        return "voyage"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def _free_provider(backend: str) -> LLMProvider:
    return GeminiProvider() if backend == "gemini" else GroqProvider()


def provider_from_env() -> LLMProvider:
    backend = llm_backend()
    if backend == "anthropic":
        return AnthropicProvider()
    if backend in FREE_KEYS:
        other = "groq" if backend == "gemini" else "gemini"
        primary = _free_provider(backend)
        fallback = os.environ.get("LLM_FALLBACK", "on").strip().lower() != "off"
        if fallback and os.environ.get(FREE_KEYS[other]):
            return FallbackProvider(primary, _free_provider(other))
        return primary
    raise RuntimeError("Falta ANTHROPIC_API_KEY, GEMINI_API_KEY o GROQ_API_KEY en el entorno")


def embedder_from_env(recorder: UsageRecorder) -> Embedder:
    backend = embedding_backend()
    if backend == "voyage":
        return VoyageEmbedder(recorder=recorder)
    if backend == "gemini":
        return GeminiEmbedder(recorder)
    raise RuntimeError("Falta VOYAGE_API_KEY o GEMINI_API_KEY en el entorno (embeddings)")
