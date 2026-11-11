"""Caché de embeddings por texto exacto, para evals y corridas largas del onboarding.

Los embeddings gratuitos tienen cuota diaria (Gemini: 1000 pedidos por día). Cada reintento de
una medición volvía a indexar los mismos documentos y a vectorizar las mismas consultas: con la
caché solo se piden los textos nuevos, en un solo lote. El uso lo registra el embedder real, así
que lo que sale de la caché no suma costo.
"""

import hashlib
import json
import uuid
from pathlib import Path

from app.llm.embedder import Embedder, InputType


class CachingEmbedder:
    def __init__(self, inner: Embedder, directory: Path, *, profile: str = "") -> None:
        self._inner = inner
        self._dir = directory
        self._profile = profile
        self._dir.mkdir(parents=True, exist_ok=True)
        self.dimensions = inner.dimensions

    def _path(self, text: str, input_type: str) -> Path:
        key = hashlib.sha256(f"{self._profile}|{input_type}|{text}".encode()).hexdigest()
        return self._dir / f"{key}.json"

    def embed(
        self,
        texts: list[str],
        *,
        tenant_id: uuid.UUID,
        input_type: InputType = "document",
        case_id: uuid.UUID | None = None,
    ) -> list[list[float]]:
        vectors: dict[int, list[float]] = {}
        missing: list[int] = []
        for i, text in enumerate(texts):
            path = self._path(text, input_type)
            if path.exists():
                vectors[i] = json.loads(path.read_text(encoding="utf-8"))
            else:
                missing.append(i)
        if missing:
            fresh = self._inner.embed(
                [texts[i] for i in missing],
                tenant_id=tenant_id,
                input_type=input_type,
                case_id=case_id,
            )
            for i, vector in zip(missing, fresh, strict=True):
                vectors[i] = vector
                self._path(texts[i], input_type).write_text(json.dumps(vector), encoding="utf-8")
        return [vectors[i] for i in range(len(texts))]
