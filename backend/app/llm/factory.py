"""Elige proveedor de modelos y de embeddings según las claves del entorno.

Modelos: Claude si hay ANTHROPIC_API_KEY; si no, Gemini si hay GEMINI_API_KEY.
Embeddings: Voyage si hay VOYAGE_API_KEY; si no, Gemini si hay GEMINI_API_KEY.
Cambiar de embedder obliga a reindexar: los vectores de uno no sirven para el otro.
"""

import os

from app.llm.client import AnthropicProvider, LLMProvider
from app.llm.embedder import Embedder, VoyageEmbedder
from app.llm.gemini import GeminiEmbedder, GeminiProvider
from app.llm.types import UsageRecorder


def llm_backend() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def embedding_backend() -> str | None:
    if os.environ.get("VOYAGE_API_KEY"):
        return "voyage"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    return None


def provider_from_env() -> LLMProvider:
    backend = llm_backend()
    if backend == "anthropic":
        return AnthropicProvider()
    if backend == "gemini":
        return GeminiProvider()
    raise RuntimeError("Falta ANTHROPIC_API_KEY o GEMINI_API_KEY en el entorno")


def embedder_from_env(recorder: UsageRecorder) -> Embedder:
    backend = embedding_backend()
    if backend == "voyage":
        return VoyageEmbedder(recorder=recorder)
    if backend == "gemini":
        return GeminiEmbedder(recorder)
    raise RuntimeError("Falta VOYAGE_API_KEY o GEMINI_API_KEY en el entorno")
