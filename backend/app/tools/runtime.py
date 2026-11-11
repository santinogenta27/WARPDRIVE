"""Ejecución de herramientas contra la API del cliente según su manifiesto.

Solo la llama el guardián de políticas (app/policy/guardian.py). Tiempos de la spec:
consultas (GET) 10 s con 2 reintentos; acciones 15 s, sin reintento salvo con clave de
idempotencia.
"""

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import httpx2

from app.obs import timing

_PATH_FIELD = re.compile(r"{(\w+)}")
# Se acepta httpx o httpx2 (misma API): el TestClient de Starlette usa httpx2.
HttpClient = httpx.Client | httpx2.Client
_TRANSPORT_ERRORS = (httpx.TransportError, httpx2.TransportError)


@dataclass(frozen=True)
class UpstreamResult:
    status_code: int
    data: Any

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300


def idempotency_key(expression: str | None, values: dict[str, Any]) -> str | None:
    """`"case_id + order_id"` -> `"<case_id>:<order_id>"`."""
    if not expression:
        return None
    names = [part.strip() for part in expression.split("+")]
    return ":".join(str(values[name]) for name in names)


class ToolRuntime:
    def __init__(self, http: HttpClient) -> None:
        self._http = http

    def call(
        self,
        method: str,
        path_template: str,
        params: dict[str, Any],
        *,
        key: str | None = None,
    ) -> UpstreamResult:
        path_fields = _PATH_FIELD.findall(path_template)
        path = path_template.format(**{f: quote(str(params[f]), safe="") for f in path_fields})
        rest = {k: v for k, v in params.items() if k not in path_fields}
        headers = {"Idempotency-Key": key} if key else {}
        is_query = method == "GET"
        attempts = 3 if is_query or key else 1
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                with timing.step(timing.TOOLS, "herramienta") as span:
                    span.update({"http.method": method, "http.route": path_template})
                    response = self._http.request(
                        method,
                        path,
                        params=rest if is_query and rest else None,
                        json=rest if not is_query and rest else None,
                        headers=headers,
                        timeout=10.0 if is_query else 15.0,
                    )
            except _TRANSPORT_ERRORS as exc:
                last_error = exc
                continue
            if response.status_code >= 500 and attempts > 1:
                last_error = RuntimeError(f"upstream {response.status_code}")
                continue
            data = response.json() if response.content else None
            return UpstreamResult(response.status_code, data)
        raise RuntimeError(f"La API del cliente no respondió: {last_error!r}")
