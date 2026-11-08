"""Tarea 3 — test de aceptación: cada llamada a un modelo guarda tokens y costo en `usage`.

Se usan el SDK real de Anthropic y el cliente real de Voyage sobre un transporte HTTP simulado:
se prueba el mapeo de la respuesta de la API sin red ni claves.
"""

import json
import uuid
from decimal import Decimal

import anthropic
import httpx
import httpx2  # el SDK de Anthropic 2.x usa httpx2 (misma API que httpx)
import psycopg
import pytest

from app.db.connection import tenant_scope
from app.llm.client import AnthropicProvider, LLMClient, PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.llm.pricing import cost_usd
from app.llm.types import TokenUsage


def _tenant(conn: psycopg.Connection) -> uuid.UUID:
    row = conn.execute("insert into tenants (name) values ('Acme') returning id").fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    return tenant_id


def _anthropic_transport(calls: list[dict[str, object]]) -> httpx2.MockTransport:
    def handler(request: httpx2.Request) -> httpx2.Response:
        body = json.loads(request.content)
        calls.append(body)
        return httpx2.Response(
            200,
            json={
                "id": f"msg_{len(calls)}",
                "type": "message",
                "role": "assistant",
                "model": body["model"],
                "content": [{"type": "text", "text": "¿Me pasás el número de pedido?"}],
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {
                    "input_tokens": 1200,
                    "output_tokens": 80,
                    "cache_creation_input_tokens": 300,
                    "cache_read_input_tokens": 2000,
                },
            },
        )

    return httpx2.MockTransport(handler)


def _llm_client(conn: psycopg.Connection, calls: list[dict[str, object]]) -> LLMClient:
    sdk = anthropic.Anthropic(
        api_key="test", http_client=httpx2.Client(transport=_anthropic_transport(calls))
    )
    return LLMClient(provider=AnthropicProvider(client=sdk), recorder=PostgresUsageRecorder(conn))


def test_costo_por_modelo() -> None:
    million = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost_usd("claude-sonnet-5-5", million) == Decimal("12")
    assert cost_usd("claude-haiku-5-5", million) == Decimal("0.6")
    assert cost_usd("claude-opus-5-5", million) == Decimal("24")
    cached = TokenUsage(input_tokens=0, output_tokens=0, cache_read_input_tokens=1_000_000)
    assert cost_usd("claude-sonnet-5-5", cached) == Decimal("0.2")


def test_cada_llamada_guarda_tokens_y_costo(conn: psycopg.Connection) -> None:
    tenant = _tenant(conn)
    calls: list[dict[str, object]] = []
    client = _llm_client(conn, calls)

    for _ in range(2):
        response = client.complete(
            tenant_id=tenant,
            model="claude-sonnet-5-5",
            system="Sos el asistente de soporte de Acme.",
            messages=[{"role": "user", "content": "¿Dónde está mi pedido?"}],
            max_tokens=1024,
        )
        assert response.text == "¿Me pasás el número de pedido?"

    assert len(calls) == 2
    expected = cost_usd(
        "claude-sonnet-5-5",
        TokenUsage(
            input_tokens=1200,
            output_tokens=80,
            cache_creation_input_tokens=300,
            cache_read_input_tokens=2000,
        ),
    )
    with tenant_scope(conn, tenant):
        rows = conn.execute(
            "select kind, model, input_tokens, output_tokens, cache_creation_input_tokens, "
            "cache_read_input_tokens, cost_usd from usage"
        ).fetchall()
    assert rows == [("llm", "claude-sonnet-5-5", 1200, 80, 300, 2000, expected)] * 2


def test_modelo_sin_precio_no_llama_a_la_api(conn: psycopg.Connection) -> None:
    tenant = _tenant(conn)
    calls: list[dict[str, object]] = []
    client = _llm_client(conn, calls)
    with pytest.raises(ValueError, match="sin precio"):
        client.complete(
            tenant_id=tenant,
            model="modelo-inventado",
            messages=[{"role": "user", "content": "hola"}],
            max_tokens=10,
        )
    assert calls == []


def test_embedder_guarda_tokens_y_costo(conn: psycopg.Connection) -> None:
    tenant = _tenant(conn)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url == "https://api.voyageai.com/v1/embeddings"
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": [
                    {"object": "embedding", "embedding": [0.1] * 1024, "index": i}
                    for i in range(len(body["input"]))
                ],
                "model": body["model"],
                "usage": {"total_tokens": 42},
            },
        )

    embedder = VoyageEmbedder(
        api_key="test",
        recorder=PostgresUsageRecorder(conn),
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    vectors = embedder.embed(["horarios de sucursales", "cuotas sin interés"], tenant_id=tenant)

    assert len(vectors) == 2 and len(vectors[0]) == 1024
    with tenant_scope(conn, tenant):
        row = conn.execute("select kind, model, input_tokens, cost_usd from usage").fetchone()
    assert row == (
        "embedding",
        "voyage-multilingual-2",
        42,
        cost_usd("voyage-multilingual-2", TokenUsage(input_tokens=42, output_tokens=0)),
    )
