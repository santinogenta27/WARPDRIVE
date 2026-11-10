"""Caché de embeddings por texto exacto (evals y corridas largas con cuota diaria gratuita).

Gemini gratis permite 1000 pedidos de embeddings por día: cada reintento de una medición volvía
a indexar los documentos y a vectorizar las mismas consultas. Con la caché, solo se piden los
textos nuevos; el tipo (documento o consulta) y el perfil del modelo separan las entradas.
"""

import uuid
from pathlib import Path
from typing import Any

from app.llm.embedding_cache import CachingEmbedder

TENANT = uuid.uuid4()


class Counting:
    dimensions = 3

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def embed(self, texts: list[str], **kwargs: Any) -> list[list[float]]:
        self.batches.append(list(texts))
        return [[float(len(t)), 1.0, kwargs.get("input_type") == "query"] for t in texts]


def test_solo_pide_los_textos_nuevos_y_conserva_el_orden(tmp_path: Path) -> None:
    inner = Counting()
    cached = CachingEmbedder(inner, tmp_path, profile="gemini-embedding-2")
    first = cached.embed(["envíos", "talles"], tenant_id=TENANT, input_type="document")
    again = CachingEmbedder(inner, tmp_path, profile="gemini-embedding-2").embed(
        ["talles", "cuotas", "envíos"], tenant_id=TENANT, input_type="document"
    )
    assert inner.batches == [["envíos", "talles"], ["cuotas"]]
    assert again[0] == first[1] and again[2] == first[0]
    assert cached.dimensions == 3


def test_separa_consultas_documentos_y_perfiles(tmp_path: Path) -> None:
    inner = Counting()
    CachingEmbedder(inner, tmp_path, profile="a").embed(["hola"], tenant_id=TENANT)
    CachingEmbedder(inner, tmp_path, profile="a").embed(
        ["hola"], tenant_id=TENANT, input_type="query"
    )
    CachingEmbedder(inner, tmp_path, profile="b").embed(["hola"], tenant_id=TENANT)
    assert len(inner.batches) == 3
