"""Groq (plan gratuito) por su API compatible con OpenAI: proveedor detrás de LLMClient.

Igual que con Gemini, el agente sigue hablando en el formato de mensajes de Claude (bloques
`text`, `tool_use` y `tool_result`) y este módulo lo traduce a chat completions y de vuelta.
Groq no tiene embeddings: siguen con Gemini o Voyage (docs/decisions/020).
"""

import json
import logging
import os
import random
import re
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from app.llm.errors import QuotaExhausted
from app.llm.pricing import PRICES
from app.llm.types import LLMResponse, TokenUsage, ToolCall
from app.obs import timing

logger = logging.getLogger(__name__)
API_URL = "https://api.groq.com/openai/v1/chat/completions"
# Modelos gratuitos con tool calling disponibles en la cuenta; se cambian con GROQ_AGENT_MODEL y
# GROQ_CLASSIFIER_MODEL. Cada modelo tiene sus propios límites (1000 pedidos por día y 8000
# tokens por minuto en el plan gratuito). gpt-oss-20b fue el mejor en los evals de Acme: 10/10,
# contra 7/10 de gpt-oss-120b y 8/10 de qwen3.8-27b (decisión 020).
DEFAULT_AGENT_MODEL = "openai/gpt-oss-20b"
DEFAULT_CLASSIFIER_MODEL = "openai/gpt-oss-20b"
REASONING_MODELS = ("openai/gpt-oss",)  # aceptan reasoning_effort
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
TOOL_CHOICES = {"auto": "auto", "any": "required", "none": "none"}
STOP_REASONS = {
    "stop": "end_turn",
    "tool_calls": "tool_use",
    "length": "max_tokens",
    "content_filter": "refusal",
}


class GroqQuotaExhausted(QuotaExhausted):
    """Groq pidió esperar más de lo razonable (cuota diaria) o el 429 siguió tras reintentar."""


class GroqGenerationFailed(RuntimeError):
    """El modelo siguió generando salidas que Groq no pudo interpretar."""


MAX_BAD_GENERATIONS = 3


def _seconds(value: str | None) -> float | None:
    """'7.66s', '2m59.56s', '1h2m' o '12' (retry-after) -> segundos."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    total, found = 0.0, False
    for amount, unit in re.findall(r"([\d.]+)(ms|h|m|s)", value):
        found = True
        total += float(amount) * {"h": 3600, "m": 60, "s": 1, "ms": 0.001}[unit]
    return total if found else None


class GroqTransport:
    """POST autenticado a chat completions, respetando los límites del plan gratuito.

    * Antes de cada llamada, si al modelo le quedan menos de `min_tokens` tokens en el minuto
      (encabezados x-ratelimit-*), espera a que se renueven.
    * Ante 429, 408 o 5xx reintenta con la espera de `retry-after` o exponencial con jitter.
    * Si la espera pedida supera `max_wait` (cuota diaria), lanza GroqQuotaExhausted sin esperar.
    * Un 400 `tool_use_failed` u `output_parse_failed` (el modelo generó una salida que Groq no
      pudo interpretar) se reintenta.
    """

    def __init__(
        self,
        api_key: str | None = None,
        http_client: httpx.Client | None = None,
        *,
        min_tokens: int | None = None,
        max_retries: int = 6,
        max_wait: float | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        url: str = API_URL,
    ) -> None:
        # `url`: la misma API de chat completions en otro proveedor (OpenAI, decisión 049).
        self._url = url
        self._api_key = api_key or os.environ.get("GROQ_API_KEY", "")
        self._http = http_client or httpx.Client(timeout=120.0)
        if min_tokens is None:
            min_tokens = int(os.environ.get("GROQ_MIN_TOKENS", "4000"))
        self._min_tokens = min_tokens
        self._max_retries = max_retries
        if max_wait is None:  # en una medición larga conviene esperar más que en el chat
            max_wait = float(os.environ.get("GROQ_MAX_WAIT", "90"))
        self._max_wait = max_wait
        self._sleep = sleep
        self._clock = clock
        self._tokens: dict[str, tuple[int, float]] = {}  # modelo -> (restantes, renuevan a las)

    def post(self, body: dict[str, Any]) -> dict[str, Any]:
        model = str(body["model"])
        bad_generations = 0
        for attempt in range(self._max_retries + 1):
            last = attempt == self._max_retries
            self._respect_tokens(model)
            try:
                response = self._http.post(
                    self._url, headers={"Authorization": f"Bearer {self._api_key}"}, json=body
                )
            except httpx.TransportError:
                if last:
                    raise
                self._wait(self._backoff(attempt))
                continue
            self._remember(model, response.headers)
            if response.status_code < 400:
                data: dict[str, Any] = response.json()
                return data
            if response.status_code == 400 and _tool_use_failed(response):
                recovered = _recover_tool_call(response, body)
                if recovered is not None:
                    logger.info(
                        "Groq: %s generó el nombre de la herramienta roto; recuperado", model
                    )
                    return recovered
                bad_generations += 1
                if bad_generations >= MAX_BAD_GENERATIONS:
                    raise GroqGenerationFailed(
                        f"{model} generó {bad_generations} salidas inválidas seguidas; la última"
                        f" ({_generation_detail(response)})"
                    )
                continue  # el modelo generó mal la llamada: se pide de nuevo
            if response.status_code not in RETRYABLE_STATUS:
                raise httpx.HTTPStatusError(
                    f"Groq respondió {response.status_code}: {response.text[:500]}",
                    request=response.request,
                    response=response,
                )
            wait = _seconds(response.headers.get("retry-after"))
            if response.status_code == 429 and wait is not None and wait > self._max_wait:
                raise GroqQuotaExhausted(
                    f"Cuota de {model} agotada: Groq pide esperar {wait:.0f} s", daily=True
                )
            if last:
                if response.status_code == 429:
                    raise GroqQuotaExhausted(
                        f"{model} siguió sin cuota tras {self._max_retries} reintentos",
                        daily=False,
                    )
                response.raise_for_status()
            self._wait(min(self._max_wait, wait if wait is not None else self._backoff(attempt)))
        raise AssertionError("inalcanzable")

    def _wait(self, seconds: float) -> None:
        """Una espera por el límite o para reintentar: se mide aparte (decisión 044)."""
        with timing.step(timing.WAIT, "Groq"):
            self._sleep(seconds)

    def _respect_tokens(self, model: str) -> None:
        remaining, reset_at = self._tokens.get(model, (self._min_tokens, 0.0))
        wait = reset_at - self._clock()
        if remaining < self._min_tokens and wait > 0:
            self._wait(wait)

    def _remember(self, model: str, headers: httpx.Headers) -> None:
        remaining = headers.get("x-ratelimit-remaining-tokens")
        reset = _seconds(headers.get("x-ratelimit-reset-tokens"))
        if remaining is not None and reset is not None:
            self._tokens[model] = (int(float(remaining)), self._clock() + reset)

    def _backoff(self, attempt: int) -> float:
        return min(self._max_wait, 2.0 * 2**attempt) * random.uniform(1.0, 1.25)


GENERATION_ERRORS = {"tool_use_failed", "output_parse_failed"}


def _generation_detail(response: httpx.Response) -> str:
    """Código y salida que el modelo generó mal, para el log."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return response.text[:400]
    generated = error.get("failed_generation") or ""
    if not isinstance(generated, str):
        generated = json.dumps(generated, ensure_ascii=False)
    return f"{error.get('code')}: {error.get('message', '')} | generó: {generated[:400]}"


# Marcador interno que gpt-oss a veces deja pegado al nombre de la herramienta
# ("responder<|channel|>commentary") o en su lugar ("<|channel|>commentary").
_MARKER = re.compile(r"<\|.*$", re.S)


def _recover_tool_call(response: httpx.Response, body: dict[str, Any]) -> dict[str, Any] | None:
    """Si la salida rechazada es una llamada válida a una herramienta pedida, salvo por el
    nombre roto, la devuelve como una respuesta normal. Solo cuando no hay dudas: el nombre
    limpio es una herramienta del pedido (o, si quedó vacío, los argumentos cumplen el esquema
    de una sola) y los argumentos validan contra su esquema. Si no, None y se pide de nuevo.
    Groq no informa tokens en un 400: el uso de esta llamada queda en cero."""
    try:
        generated = (response.json().get("error") or {}).get("failed_generation")
        if isinstance(generated, str):
            generated = json.loads(generated)
        arguments = generated.get("arguments") if isinstance(generated, dict) else None
        if isinstance(arguments, str):
            arguments = json.loads(arguments)
    except ValueError:
        return None
    if not isinstance(generated, dict) or not isinstance(arguments, dict):
        return None
    schemas = {
        tool["function"]["name"]: tool["function"].get("parameters") or {}
        for tool in body.get("tools") or []
    }

    def fits(tool: str) -> bool:
        return Draft202012Validator(schemas[tool]).is_valid(arguments)

    name = _MARKER.sub("", str(generated.get("name") or "")).strip()
    if not name:
        candidates = [tool for tool in schemas if fits(tool)]
        if len(candidates) != 1:
            return None
        name = candidates[0]
    if name not in schemas or not fits(name):
        return None
    call = {
        "id": f"groq_{uuid.uuid4().hex}",
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, ensure_ascii=False)},
    }
    return {
        "model": body["model"],
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {"role": "assistant", "content": None, "tool_calls": [call]},
            }
        ],
        "usage": {},
        "recovered": True,
    }


def _tool_use_failed(response: httpx.Response) -> bool:
    """El modelo generó una salida que Groq no pudo interpretar (una llamada a herramienta mal
    armada, o razonamiento en lugar de la llamada): pedirla de nuevo suele alcanzar."""
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return False
    return (
        error.get("code") in GENERATION_ERRORS
        or "failed_generation" in error
        or "tool call" in str(error.get("message", ""))
    )


def models_from_env() -> dict[str, str]:
    """Modelo pedido por el agente -> modelo de Groq que lo reemplaza."""
    agent = os.environ.get("GROQ_AGENT_MODEL") or DEFAULT_AGENT_MODEL
    classifier = os.environ.get("GROQ_CLASSIFIER_MODEL") or DEFAULT_CLASSIFIER_MODEL
    return {
        "claude-sonnet-5-5": agent,  # agente
        "claude-opus-5-5": agent,  # juez de evals con Claude (con Groq, el runner fija otro)
        "claude-haiku-5-5": classifier,  # clasificador
    }


class GroqProvider:
    def __init__(
        self, transport: GroqTransport | None = None, models: dict[str, str] | None = None
    ) -> None:
        self._transport = transport or GroqTransport()
        self._models = models if models is not None else models_from_env()
        unpriced = sorted({m for m in self._models.values() if m not in PRICES})
        if unpriced:  # sin precio no se registra el costo: mejor fallar al arrancar
            raise ValueError(f"Modelo de Groq sin precio en pricing.py: {', '.join(unpriced)}")

    def _target(self, model: str) -> str:
        if model in self._models:
            return self._models[model]
        if model in PRICES and not model.startswith(("claude-", "gemini-", "voyage-")):
            return model  # un modelo de Groq pedido por nombre (p. ej. el juez de los evals)
        raise ValueError(f"Modelo sin equivalente en Groq: {model}")

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
        body = _request(target, messages, max_tokens, system, options)
        try:
            return _response(self._transport.post(body))
        except GroqGenerationFailed as error:
            # Sin una salida válida no hay nada que mostrar: un rechazo hace que el agente
            # derive (o que el clasificador devuelva "otro") en lugar de inventar.
            logger.warning("Groq: %s; se devuelve como rechazo", error)
            return LLMResponse(
                text="",
                model=target,
                stop_reason="refusal",
                usage=TokenUsage(input_tokens=0, output_tokens=0),
                events=("rechazo",),
            )


# -- pedido ----------------------------------------------------------------------------------
def _request(
    model: str,
    messages: list[dict[str, Any]],
    max_tokens: int,
    system: str | None,
    options: dict[str, Any],
) -> dict[str, Any]:
    chat: list[dict[str, Any]] = [{"role": "system", "content": system}] if system else []
    chat.extend(_messages(messages))
    body: dict[str, Any] = {"model": model, "messages": chat, "max_completion_tokens": max_tokens}
    effort = (options.get("output_config") or {}).get("effort")
    if effort in ("low", "medium", "high") and model.startswith(REASONING_MODELS):
        body["reasoning_effort"] = effort
    tools = options.get("tools") or []
    if tools:
        body["tools"] = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t["input_schema"],
                },
            }
            for t in tools
        ]
    choice = options.get("tool_choice") or {}
    if choice.get("type") == "tool":
        body["tool_choice"] = {"type": "function", "function": {"name": choice["name"]}}
    elif choice.get("type") in TOOL_CHOICES:
        body["tool_choice"] = TOOL_CHOICES[choice["type"]]
    return body


def _messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names: dict[str, str] = {}  # id de llamada -> nombre (Groq lo acepta en el mensaje `tool`)
    chat: list[dict[str, Any]] = []
    for message in messages:
        raw = message["content"]
        blocks = [{"type": "text", "text": raw}] if isinstance(raw, str) else raw
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text" and b.get("text")]
        if message["role"] == "assistant":
            calls = []
            for block in blocks:
                if block.get("type") == "tool_use":
                    names[block["id"]] = block["name"]
                    calls.append(
                        {
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(
                                    block.get("input") or {}, ensure_ascii=False
                                ),
                            },
                        }
                    )
            entry: dict[str, Any] = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                entry["tool_calls"] = calls
            chat.append(entry)
            continue
        # Usuario: los resultados de herramientas van primero, como mensajes `tool`.
        for block in blocks:
            if block.get("type") == "tool_result":
                chat.append(
                    {
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "name": names.get(block["tool_use_id"], ""),
                        "content": _result_text(block),
                    }
                )
        if texts:
            chat.append({"role": "user", "content": "\n".join(texts)})
    return chat


def _result_text(block: dict[str, Any]) -> str:
    raw = block.get("content", "")
    if isinstance(raw, list):
        raw = "".join(b.get("text", "") for b in raw if isinstance(b, dict))
    text = str(raw)
    if block.get("is_error") and '"error"' not in text:
        text = json.dumps({"error": text}, ensure_ascii=False)
    return text


# -- respuesta -------------------------------------------------------------------------------
def _response(data: dict[str, Any]) -> LLMResponse:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content: list[dict[str, Any]] = []
    text = message.get("content") or ""
    if text:
        content.append({"type": "text", "text": text})
    calls: list[ToolCall] = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            args = json.loads(function.get("arguments") or "{}")
        except ValueError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        call_id = call.get("id") or f"groq_{uuid.uuid4().hex}"
        content.append({"type": "tool_use", "id": call_id, "name": function["name"], "input": args})
        calls.append(ToolCall(id=call_id, name=function["name"], input=args))
    reason = choice.get("finish_reason")
    stop = STOP_REASONS.get(reason, reason) if reason else None
    if calls:
        stop = "tool_use"
    return LLMResponse(
        text=text,
        model=str(data.get("model") or ""),
        stop_reason=stop,
        usage=_usage(data.get("usage") or {}),
        content=content,
        tool_calls=calls,
        raw=data,
        events=("recuperada",) if data.get("recovered") else (),
    )


def _usage(usage: dict[str, Any]) -> TokenUsage:
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    prompt = int(usage.get("prompt_tokens") or 0)
    return TokenUsage(
        input_tokens=prompt - cached,
        output_tokens=int(usage.get("completion_tokens") or 0),
        cache_read_input_tokens=cached,
    )
