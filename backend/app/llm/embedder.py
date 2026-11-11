"""Embedder: interfaz de embeddings. Implementación con Voyage AI (decisión cerrada de la spec)."""

import os
import uuid
from typing import Literal, Protocol

import httpx

from app.llm.pricing import cost_usd
from app.llm.types import TokenUsage, UsageRecorder

InputType = Literal["document", "query"]


class Embedder(Protocol):
    dimensions: int

    def embed(
        self,
        texts: list[str],
        *,
        tenant_id: uuid.UUID,
        input_type: InputType = "document",
        case_id: uuid.UUID | None = None,
    ) -> list[list[float]]: ...


class VoyageEmbedder:
    url = "https://api.voyageai.com/v1/embeddings"
    dimensions = 1024

    def __init__(
        self,
        recorder: UsageRecorder,
        api_key: str | None = None,
        model: str = "voyage-multilingual-2",
        http_client: httpx.Client | None = None,
    ) -> None:
        self._recorder = recorder
        self._api_key = api_key or os.environ.get("VOYAGE_API_KEY", "")
        self._model = model
        self._http = http_client or httpx.Client(timeout=30.0)

    def embed(
        self,
        texts: list[str],
        *,
        tenant_id: uuid.UUID,
        input_type: InputType = "document",
        case_id: uuid.UUID | None = None,
    ) -> list[list[float]]:
        response = self._http.post(
            self.url,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={"input": texts, "model": self._model, "input_type": input_type},
        )
        response.raise_for_status()
        body = response.json()
        usage = TokenUsage(input_tokens=int(body["usage"]["total_tokens"]), output_tokens=0)
        self._recorder.record(
            tenant_id=tenant_id,
            case_id=case_id,
            kind="embedding",
            model=self._model,
            usage=usage,
            cost_usd=cost_usd(self._model, usage),
        )
        ordered = sorted(body["data"], key=lambda item: item["index"])
        return [list(item["embedding"]) for item in ordered]
