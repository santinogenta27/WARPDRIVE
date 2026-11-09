"""Gemini (nivel gratuito) por la API REST con httpx: proveedor detrás de LLMClient y Embedder.

El agente sigue hablando en el formato de mensajes de Claude (bloques `text`, `tool_use` y
`tool_result`); este módulo lo traduce a `generateContent` de Gemini y de vuelta, así que el
resto del agente no cambia. Los modelos del agente se mapean a modelos Flash del nivel gratuito
(docs/decisions/012).
"""

import json
import os
import uuid
from typing import Any

import httpx

from app.llm.embedder import InputType
from app.llm.pricing import cost_usd
from app.llm.types import LLMResponse, TokenUsage, ToolCall, UsageRecorder

API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Modelo pedido por el agente -> modelo gratuito de Gemini que lo reemplaza.
MODEL_MAP = {
    "claude-sonnet-5-5": "gemini-3.6-flash",  # agente
    "claude-opus-5-5": "gemini-3.6-flash",  # juez de evals
    "claude-haiku-5-5": "gemini-3.5-flash-lite",  # clasificador
}
EMBEDDING_MODEL = "gemini-embedding-2"
EMBEDDING_BATCH = 100  # pedidos por llamada a batchEmbedContents
# gemini-embedding-2 no acepta task_type: la tarea va como prefijo del texto.
EMBEDDING_FORMATS = {
    "document": "title: none | text: {}",
    "query": "task: search result | query: {}",
}
EFFORT_TO_THINKING = {"low": "LOW", "medium": "MEDIUM", "high": "HIGH"}
TOOL_CHOICE_MODES = {"auto": "AUTO", "any": "ANY", "tool": "ANY", "none": "NONE"}
REFUSAL_REASONS = {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
# Id propio para llamadas que Gemini devuelve sin id: no se le reenvía.
LOCAL_ID_PREFIX = "gemini_local_"


class GeminiTransport:
    """POST autenticado a la API de Gemini."""

    def __init__(self, api_key: str | None = None, http_client: httpx.Client | None = None) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._http = http_client or httpx.Client(timeout=60.0)

    def post(self, model: str, method: str, body: dict[str, Any]) -> dict[str, Any]:
        response = self._http.post(
            f"{API_URL}/{model}:{method}", headers={"x-goog-api-key": self._api_key}, json=body
        )
        response.raise_for_status()
        data: dict[str, Any] = response.json()
        return data


def gemini_model(model: str) -> str:
    if model in MODEL_MAP:
        return MODEL_MAP[model]
    if model.startswith("gemini-"):
        return model
    raise ValueError(f"Modelo sin equivalente en Gemini: {model}")


class GeminiProvider:
    def __init__(self, transport: GeminiTransport | None = None) -> None:
        self._transport = transport or GeminiTransport()

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        **options: Any,
    ) -> LLMResponse:
        target = gemini_model(model)
        body = _request(messages, max_tokens, system, options)
        data = self._transport.post(target, "generateContent", body)
        return _response(target, data)


class GeminiEmbedder:
    """Embeddings con Gemini Embedding 2. Con dimensión no por defecto la API ya los normaliza."""

    dimensions = 1024

    def __init__(
        self,
        recorder: UsageRecorder,
        transport: GeminiTransport | None = None,
        model: str = EMBEDDING_MODEL,
    ) -> None:
        self._recorder = recorder
        self._transport = transport or GeminiTransport()
        self._model = model

    def embed(
        self,
        texts: list[str],
        *,
        tenant_id: uuid.UUID,
        input_type: InputType = "document",
        case_id: uuid.UUID | None = None,
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        tokens = 0
        template = EMBEDDING_FORMATS[input_type]
        for start in range(0, len(texts), EMBEDDING_BATCH):
            requests = [
                {
                    "model": f"models/{self._model}",
                    "content": {"parts": [{"text": template.format(text)}]},
                    "outputDimensionality": self.dimensions,
                }
                for text in texts[start : start + EMBEDDING_BATCH]
            ]
            data = self._transport.post(self._model, "batchEmbedContents", {"requests": requests})
            vectors.extend(list(e["values"]) for e in data["embeddings"])
            tokens += int((data.get("usageMetadata") or {}).get("promptTokenCount", 0))
        usage = TokenUsage(input_tokens=tokens, output_tokens=0)
        self._recorder.record(
            tenant_id=tenant_id,
            case_id=case_id,
            kind="embedding",
            model=self._model,
            usage=usage,
            cost_usd=cost_usd(self._model, usage),
        )
        return vectors


# -- pedido ----------------------------------------------------------------------------------
def _request(
    messages: list[dict[str, Any]], max_tokens: int, system: str | None, options: dict[str, Any]
) -> dict[str, Any]:
    config: dict[str, Any] = {"maxOutputTokens": max_tokens}
    effort = (options.get("output_config") or {}).get("effort")
    if effort in EFFORT_TO_THINKING:
        config["thinkingConfig"] = {"thinkingLevel": EFFORT_TO_THINKING[effort]}
    body: dict[str, Any] = {"contents": _contents(messages), "generationConfig": config}
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    tools = options.get("tools") or []
    if tools:
        body["tools"] = [
            {
                "functionDeclarations": [
                    {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parametersJsonSchema": t["input_schema"],
                    }
                    for t in tools
                ]
            }
        ]
    choice = options.get("tool_choice") or {}
    if choice.get("type") in TOOL_CHOICE_MODES:
        calling: dict[str, Any] = {"mode": TOOL_CHOICE_MODES[choice["type"]]}
        if choice["type"] == "tool":
            calling["allowedFunctionNames"] = [choice["name"]]
        body["toolConfig"] = {"functionCallingConfig": calling}
    return body


def _contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names: dict[str, str] = {}  # id de llamada -> nombre de función (functionResponse lo pide)
    contents: list[dict[str, Any]] = []
    for message in messages:
        raw = message["content"]
        blocks = [{"type": "text", "text": raw}] if isinstance(raw, str) else raw
        parts: list[dict[str, Any]] = []
        for block in blocks:
            part = _part(block, names)
            if part is None:
                continue
            if block.get("thought_signature"):
                part["thoughtSignature"] = block["thought_signature"]
            parts.append(part)
        if parts:
            role = "model" if message["role"] == "assistant" else "user"
            contents.append({"role": role, "parts": parts})
    return contents


def _part(block: dict[str, Any], names: dict[str, str]) -> dict[str, Any] | None:
    kind = block.get("type")
    if kind == "text":
        if not block.get("text") and not block.get("thought_signature"):
            return None
        return {"text": block.get("text", "")}
    if kind == "tool_use":
        names[block["id"]] = block["name"]
        call: dict[str, Any] = {"name": block["name"], "args": block.get("input") or {}}
        if not block["id"].startswith(LOCAL_ID_PREFIX):
            call = {"id": block["id"], **call}
        return {"functionCall": call}
    if kind == "tool_result":
        call_id = block["tool_use_id"]
        result: dict[str, Any] = {"name": names.get(call_id, ""), "response": _result(block)}
        if not call_id.startswith(LOCAL_ID_PREFIX):
            result = {"id": call_id, **result}
        return {"functionResponse": result}
    return None  # otros bloques (p. ej. thinking de Claude) no tienen equivalente


def _result(block: dict[str, Any]) -> dict[str, Any]:
    raw = block.get("content", "")
    if isinstance(raw, list):
        raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict))
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        value = raw
    result = value if isinstance(value, dict) else {"result": value}
    if block.get("is_error") and "error" not in result:
        result = {"error": result}
    return result


# -- respuesta -------------------------------------------------------------------------------
def _response(model: str, data: dict[str, Any]) -> LLMResponse:
    candidate = (data.get("candidates") or [{}])[0]
    content: list[dict[str, Any]] = []
    calls: list[ToolCall] = []
    texts: list[str] = []
    for part in (candidate.get("content") or {}).get("parts") or []:
        if part.get("thought"):  # resúmenes de pensamiento: no forman parte de la respuesta
            continue
        if "functionCall" in part:
            fc = part["functionCall"]
            call_id = fc.get("id") or f"{LOCAL_ID_PREFIX}{uuid.uuid4().hex}"
            args = dict(fc.get("args") or {})
            block: dict[str, Any] = {
                "type": "tool_use",
                "id": call_id,
                "name": fc["name"],
                "input": args,
            }
            calls.append(ToolCall(id=call_id, name=fc["name"], input=args))
        elif "text" in part:
            block = {"type": "text", "text": part["text"]}
            texts.append(part["text"])
        else:
            continue
        if part.get("thoughtSignature"):
            block["thought_signature"] = part["thoughtSignature"]
        content.append(block)
    return LLMResponse(
        text="".join(texts),
        model=model,
        stop_reason=_stop_reason(data, candidate, bool(calls)),
        usage=_usage(data.get("usageMetadata") or {}),
        content=content,
        tool_calls=calls,
        raw=data,
    )


def _stop_reason(data: dict[str, Any], candidate: dict[str, Any], has_calls: bool) -> str | None:
    if (data.get("promptFeedback") or {}).get("blockReason"):
        return "refusal"
    reason = candidate.get("finishReason")
    if reason == "STOP":
        return "tool_use" if has_calls else "end_turn"
    if reason == "MAX_TOKENS":
        return "max_tokens"
    if reason in REFUSAL_REASONS:
        return "refusal"
    return str(reason).lower() if reason else None


def _usage(meta: dict[str, Any]) -> TokenUsage:
    cached = int(meta.get("cachedContentTokenCount", 0))
    prompt = int(meta.get("promptTokenCount", 0)) + int(meta.get("toolUsePromptTokenCount", 0))
    output = int(meta.get("candidatesTokenCount", 0)) + int(meta.get("thoughtsTokenCount", 0))
    return TokenUsage(
        input_tokens=prompt - cached, output_tokens=output, cache_read_input_tokens=cached
    )
