"""Cuánto tarda cada paso de una respuesta (decisión 025).

Un `Timer` por turno del agente reparte el tiempo en cuatro pasos:

- `base`: consultas a Postgres (cada una es un viaje de ida y vuelta a la base);
- `modelo`: llamadas al modelo (clasificador y agente);
- `busqueda`: el RAG híbrido (embedding de la pregunta y sus consultas);
- `herramientas`: llamadas a la API de la empresa.

Para la demo en inglés (decisión 044) se suman tres pasos:

- `espera`: la espera por el límite de tokens por minuto del modelo (Groq gratis), dentro de una
  llamada al modelo pero contada aparte: no es tiempo de generación;
- `verificaciones`: los controles de la respuesta en código (fundamento, conducta, formato);
- `email`: el envío del código de verificación.

El tiempo es exclusivo: se le cuenta al paso más interno que esté abierto, así la suma de los
pasos nunca supera el total. Lo que no cae en ninguno queda en `otros`. Sin un turno abierto,
`step` no hace nada: se puede instrumentar cualquier código sin costo.

`summary()` trae además `pasos`: cada llamada al modelo (con su papel y su espera), la búsqueda,
cada pedido a la API de la empresa, las verificaciones y el email, en orden. `log_turn` lo
escribe en una línea del log del servidor, sin datos del cliente.
"""

import logging
import sys
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

BASE, MODEL, SEARCH, TOOLS = "base", "modelo", "busqueda", "herramientas"
WAIT, CHECKS, MAIL = "espera", "verificaciones", "email"
STEPS = (BASE, MODEL, WAIT, SEARCH, TOOLS, CHECKS, MAIL)
CHECKS_NAME = "controles de la respuesta"


class Timer:
    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._started = clock()
        self._finished: float | None = None
        self._wall_ns = time.time_ns()  # para ubicar cada paso en el reloj real (trazas)
        self._stack: list[list[Any]] = []  # [paso, desde cuándo corre, registro del paso]
        # Un registro por llamada al modelo, búsqueda o herramienta: de acá salen los spans de
        # la traza del caso (app/obs/tracing.py). Las consultas a la base no se registran una
        # por una: son decenas por turno.
        self.records: list[dict[str, Any]] = []
        self.seconds: dict[str, float] = dict.fromkeys(STEPS, 0.0)
        self.calls: dict[str, int] = dict.fromkeys(STEPS, 0)

    @property
    def current(self) -> str | None:
        return self._stack[-1][0] if self._stack else None

    def _ns(self, moment: float) -> int:
        return self._wall_ns + int((moment - self._started) * 1e9)

    @property
    def started_ns(self) -> int:
        return self._wall_ns

    @property
    def finished_ns(self) -> int:
        return self._ns(self._finished if self._finished is not None else self._clock())

    def enter(self, kind: str, name: str = "") -> dict[str, Any]:
        """Abre un paso. Devuelve sus atributos, para completarlos mientras corre (modelo,
        tokens, estado): salen en el span de la traza."""
        now = self._clock()
        if self._stack:  # el paso de afuera deja de correr mientras dura el de adentro
            outer = self._stack[-1]
            self.seconds[outer[0]] += now - outer[1]
        record: dict[str, Any] = {"kind": kind, "name": name or kind, "start_ns": self._ns(now)}
        record["attrs"] = {}
        self._stack.append([kind, now, record])
        self.calls[kind] = self.calls.get(kind, 0) + 1
        self.seconds.setdefault(kind, 0.0)
        attrs: dict[str, Any] = record["attrs"]
        return attrs

    def leave(self) -> None:
        now = self._clock()
        kind, since, record = self._stack.pop()
        self.seconds[kind] += now - since
        if kind != BASE:
            record["end_ns"] = self._ns(now)
            self.records.append(record)
        if self._stack:
            self._stack[-1][1] = now

    def count(self, kind: str) -> None:
        """Suma una llamada sin abrir un paso (su tiempo ya corre en el paso actual)."""
        self.calls[kind] = self.calls.get(kind, 0) + 1

    def finish(self) -> None:
        self._finished = self._clock()

    def summary(self, roles: Mapping[str, str] | None = None) -> dict[str, Any]:
        """Milisegundos por paso y cantidad de llamadas, listo para la auditoría. `roles`:
        modelo pedido -> papel ("clasificador", "agente"), para nombrar cada llamada."""
        total = (self._finished or self._clock()) - self._started
        known = sum(self.seconds.values())
        data: dict[str, Any] = {"total_ms": round(total * 1000)}
        for kind in STEPS:
            data[f"{kind}_ms"] = round(self.seconds[kind] * 1000)
        data["otros_ms"] = max(round((total - known) * 1000), 0)
        data["llamadas"] = {kind: self.calls[kind] for kind in STEPS}
        data["pasos"] = self._steps(roles or {})
        return data

    def _steps(self, roles: Mapping[str, str]) -> list[dict[str, Any]]:
        """Cada paso en el orden en que empezó, con su duración de punta a punta (incluye lo
        anidado: la base de una herramienta, la espera de una llamada al modelo)."""
        ordered = sorted(self.records, key=lambda r: r["start_ns"])
        waits = [r for r in ordered if r["kind"] == WAIT]
        found: list[dict[str, Any]] = []
        for record in ordered:
            kind, attrs = record["kind"], record["attrs"]
            if kind == WAIT:  # va dentro de su llamada al modelo
                continue
            ms = round((record["end_ns"] - record["start_ns"]) / 1e6)
            name = str(record["name"])
            if kind == MODEL:
                requested = str(attrs.get("llm.requested_model") or "")
                name = str(attrs.get("llm.role") or roles.get(requested, requested or name))
            elif kind == TOOLS and attrs.get("http.route"):
                name = f"{attrs.get('http.method', '')} {attrs['http.route']}".strip()
            elif kind == SEARCH:
                name = "rag"
            if kind == CHECKS and found and found[-1]["paso"] == CHECKS:
                found[-1]["ms"] += ms  # los controles de una misma respuesta, juntos
                continue
            step: dict[str, Any] = {"paso": kind, "nombre": name, "ms": ms}
            if kind == MODEL:
                inside = [
                    w
                    for w in waits
                    if record["start_ns"] <= w["start_ns"] and w["end_ns"] <= record["end_ns"]
                ]
                step["espera_ms"] = round(sum(w["end_ns"] - w["start_ns"] for w in inside) / 1e6)
            found.append(step)
        return found


_current: ContextVar[Timer | None] = ContextVar("turn_timer", default=None)


def active() -> Timer | None:
    return _current.get()


@contextmanager
def turn(clock: Callable[[], float] = time.perf_counter) -> Iterator[Timer]:
    """Abre el timer de un turno. Un turno dentro de otro usa el de afuera."""
    outer = _current.get()
    if outer is not None:
        yield outer
        return
    timer = Timer(clock)
    token = _current.set(timer)
    try:
        yield timer
    finally:
        timer.finish()
        _current.reset(token)


@contextmanager
def step(kind: str, name: str = "") -> Iterator[dict[str, Any]]:
    """Un paso del turno. Entrega un diccionario de atributos para el span de la traza (sin
    datos personales: nombres de modelos y herramientas, cantidades y estados)."""
    timer = _current.get()
    if timer is None:
        yield {}
        return
    attrs = timer.enter(kind, name)
    try:
        yield attrs
    finally:
        timer.leave()


def _seconds(ms: Any) -> str:
    return f"{int(ms or 0) / 1000:.1f} s"


def describe(summary: Mapping[str, Any]) -> str:
    """El turno en una línea: total, cada paso y cada llamada. Sin datos del cliente (papeles,
    rutas de la API con sus variables sin completar, cantidades)."""
    steps = list(summary.get("pasos") or [])
    calls = summary.get("llamadas") or {}

    def detail(kind: str) -> str:
        parts = []
        for step in (s for s in steps if s["paso"] == kind):
            text = f"{step['nombre']} {_seconds(step['ms'])}"
            if step.get("espera_ms"):
                text += f" (espera de Groq {_seconds(step['espera_ms'])})"
            parts.append(text)
        return f": {', '.join(parts)}" if parts else ""

    pieces = [
        f"{_seconds(summary.get('total_ms'))}",
        f"base {_seconds(summary.get('base_ms'))} ({calls.get(BASE, 0)} consultas)",
        f"modelo {_seconds(summary.get('modelo_ms'))}{detail(MODEL)}",
    ]
    if summary.get("espera_ms"):
        pieces.append(f"espera de Groq {_seconds(summary.get('espera_ms'))}")
    pieces += [
        f"búsqueda {_seconds(summary.get('busqueda_ms'))}",
        f"herramientas {_seconds(summary.get('herramientas_ms'))}{detail(TOOLS)}",
        f"verificaciones {_seconds(summary.get('verificaciones_ms'))}",
    ]
    if calls.get(MAIL):
        pieces.append(f"email {_seconds(summary.get('email_ms'))}")
    pieces.append(f"otros {_seconds(summary.get('otros_ms'))}")
    return " · ".join(pieces)


class _CurrentStderr(logging.StreamHandler):
    """El stderr vigente (el que junta Render en los logs), como el del mailer de consola."""

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, value: Any) -> None:
        pass


logger = logging.getLogger(__name__)
_handler = _CurrentStderr()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


def log_turn(case_id: Any, summary: Mapping[str, Any]) -> None:
    """Una línea por turno en el log del servidor (Render): en qué se fue el tiempo."""
    logger.info("[Latencia] caso=%s %s", case_id, describe(summary))


@contextmanager
def db_step() -> Iterator[None]:
    """Una consulta a la base. Dentro de la búsqueda cuenta como búsqueda (y suma una consulta
    a `base` en `llamadas`, que es la cantidad de viajes a Postgres)."""
    timer = _current.get()
    if timer is None:
        yield
        return
    if timer.current == SEARCH:
        timer.count(BASE)
        yield
        return
    timer.enter(BASE)
    try:
        yield
    finally:
        timer.leave()
