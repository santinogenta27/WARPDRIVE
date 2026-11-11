"""Gemini (nivel gratuito) por la API REST con httpx: proveedor detrás de LLMClient y Embedder.

El agente sigue hablando en el formato de mensajes de Claude (bloques `text`, `tool_use` y
`tool_result`); este módulo lo traduce a `generateContent` de Gemini y de vuelta, así que el
resto del agente no cambia. Los modelos del agente se mapean a modelos Flash del nivel gratuito
(docs/decisions/012).
"""

import json
import os
import random
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx

from app.llm.embedder import InputType
from app.llm.errors import QuotaExhausted
from app.llm.pricing import PRICES, cost_usd
from app.llm.types import LLMResponse, TokenUsage, ToolCall, UsageRecorder

API_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Modelos gratuitos por defecto; se cambian con GEMINI_AGENT_MODEL y GEMINI_CLASSIFIER_MODEL
# (cada modelo tiene su propia cuota diaria en el nivel gratuito).
DEFAULT_AGENT_MODEL = "gemini-3.6-flash"
DEFAULT_CLASSIFIER_MODEL = "gemini-3.5-flash-lite"
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
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
# Id propio para llamadas que Gemini devuelve sin id: no se le reenvía.
LOCAL_ID_PREFIX = "gemini_local_"


class GeminiQuotaExhausted(QuotaExhausted):
    """Se agotó la cuota del nivel gratuito. `daily`: la diaria, que se renueva a medianoche
    del Pacífico; si no, la por minuto siguió agotada después de todos los reintentos."""


class GeminiTransport:
    """POST autenticado a la API de Gemini, pensado para los límites bajos del nivel gratuito.

    * Pausa mínima entre llamadas (`GEMINI_MIN_INTERVAL`, 6 s por defecto: unas 10 por minuto).
    * Ante 429, 408 o 5xx reintenta con la espera que pide la API (RetryInfo) o con espera
      exponencial con jitter, hasta `max_retries` veces.
    * Si la cuota agotada es la diaria no reintenta: lanza GeminiQuotaExhausted(daily=True).
    """

    def __init__(
        self,
        api_key: str | None = None,
        http_client: httpx.Client | None = None,
        *,
        min_interval: float | None = None,
        max_retries: int = 6,
        max_wait: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self._http = http_client or httpx.Client(timeout=60.0)
        if min_interval is None:
            min_interval = float(os.environ.get("GEMINI_MIN_INTERVAL", "6"))
        self._min_interval = min_interval
        self._max_retries = max_retries
        self._max_wait = max_wait
        self._sleep = sleep
        self._clock = clock
        self._last_call: float | None = None

    def post(self, model: str, method: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{API_URL}/{model}:{method}"
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            self._pause()
            try:
                response = self._http.post(
                    url, headers={"x-goog-api-key": self._api_key}, json=body
                )
            except httpx.TransportError:  # timeout o corte de red
                if last:
                    raise
                self._sleep(self._backoff(attempt))
                continue
            if response.status_code < 400:
                data: dict[str, Any] = response.json()
                return data
            if response.status_code not in RETRYABLE_STATUS:
                response.raise_for_status()
            error = _error(response)
            if response.status_code == 429 and _is_daily(error):
                raise GeminiQuotaExhausted(
                    f"Cuota diaria de {model} agotada: {error.get('message', '')}", daily=True
                )
            if last:
                if response.status_code == 429:
                    raise GeminiQuotaExhausted(
                        f"{model} siguió sin cuota tras {self._max_retries} reintentos", daily=False
                    )
                response.raise_for_status()
            self._sleep(min(self._max_wait, _retry_delay(error) or self._backoff(attempt)))
        raise AssertionError("inalcanzable")

    def _pause(self) -> None:
        if self._last_call is not None:
            wait = self._last_call + self._min_interval - self._clock()
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()

    def _backoff(self, attempt: int) -> float:
        return min(self._max_wait, 2.0 * 2**attempt) * random.uniform(1.0, 1.25)


def _error(response: httpx.Response) -> dict[str, Any]:
    try:
        error = response.json().get("error")
    except ValueError:
        return {}
    return error if isinstance(error, dict) else {}


def _is_daily(error: dict[str, Any]) -> bool:
    if error.get("code") == "quota_exceeded":  # formato de error de la API de Interactions
        return True
    for detail in error.get("details") or []:
        for violation in detail.get("violations") or []:
            if "PerDay" in str(violation.get("quotaId", "")):
                return True
    return False


def _retry_delay(error: dict[str, Any]) -> float | None:
    for detail in error.get("details") or []:
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            try:
                return float(str(detail.get("retryDelay", "")).rstrip("s"))
            except ValueError:
                return None
    return None


def models_from_env() -> dict[str, str]:
    """Modelo pedido por el agente -> modelo de Gemini que lo reemplaza."""
    agent = os.environ.get("GEMINI_AGENT_MODEL") or DEFAULT_AGENT_MODEL
    classifier = os.environ.get("GEMINI_CLASSIFIER_MODEL") or DEFAULT_CLASSIFIER_MODEL
    return {
        "claude-sonnet-5-5": agent,  # agente
        "claude-opus-5-5": agent,  # juez de evals con Claude (con Gemini, el runner fija otro)
        "claude-haiku-5-5": classifier,  # clasificador
    }


class GeminiProvider:
    def __init__(
        self, transport: GeminiTransport | None = None, models: dict[str, str] | None = None
    ) -> None:
        self._transport = transport or GeminiTransport()
        self._models = models if models is not None else models_from_env()
        unpriced = sorted({m for m in self._models.values() if m not in PRICES})
        if unpriced:  # sin precio no se registra el costo: mejor fallar al arrancar
            raise ValueError(f"Modelo de Gemini sin precio en pricing.py: {', '.join(unpriced)}")

    def _target(self, model: str) -> str:
        if model in self._models:
            return self._models[model]
        if model.startswith("gemini-"):
            return model
        raise ValueError(f"Modelo sin equivalente en Gemini: {model}")

    def create(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        system: str | None = None,
        **options: Any,
    ) -> LLMResponse:
        target = self._target(model)
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
