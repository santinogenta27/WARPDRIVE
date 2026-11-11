"""Cuánto tarda cada paso de una respuesta (decisión 025).

Un `Timer` por turno del agente reparte el tiempo en cuatro pasos:

- `base`: consultas a Postgres (cada una es un viaje de ida y vuelta a la base);
- `modelo`: llamadas al modelo (clasificador y agente);
- `busqueda`: el RAG híbrido (embedding de la pregunta y sus consultas);
- `herramientas`: llamadas a la API de la empresa.

El tiempo es exclusivo: se le cuenta al paso más interno que esté abierto, así la suma de los
pasos nunca supera el total. Lo que no cae en ninguno queda en `otros`. Sin un turno abierto,
`step` no hace nada: se puede instrumentar cualquier código sin costo.
"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

BASE, MODEL, SEARCH, TOOLS = "base", "modelo", "busqueda", "herramientas"
STEPS = (BASE, MODEL, SEARCH, TOOLS)


class Timer:
    def __init__(self, clock: Callable[[], float] = time.perf_counter) -> None:
        self._clock = clock
        self._started = clock()
        self._finished: float | None = None
        self._stack: list[list[Any]] = []  # [paso, desde cuándo corre]
        self.seconds: dict[str, float] = dict.fromkeys(STEPS, 0.0)
        self.calls: dict[str, int] = dict.fromkeys(STEPS, 0)

    @property
    def current(self) -> str | None:
        return self._stack[-1][0] if self._stack else None

    def enter(self, kind: str) -> None:
        now = self._clock()
        if self._stack:  # el paso de afuera deja de correr mientras dura el de adentro
            outer = self._stack[-1]
            self.seconds[outer[0]] += now - outer[1]
        self._stack.append([kind, now])
        self.calls[kind] = self.calls.get(kind, 0) + 1
        self.seconds.setdefault(kind, 0.0)

    def leave(self) -> None:
        now = self._clock()
        kind, since = self._stack.pop()
        self.seconds[kind] += now - since
        if self._stack:
            self._stack[-1][1] = now

    def count(self, kind: str) -> None:
        """Suma una llamada sin abrir un paso (su tiempo ya corre en el paso actual)."""
        self.calls[kind] = self.calls.get(kind, 0) + 1

    def finish(self) -> None:
        self._finished = self._clock()

    def summary(self) -> dict[str, Any]:
        """Milisegundos por paso y cantidad de llamadas, listo para la auditoría."""
        total = (self._finished or self._clock()) - self._started
        known = sum(self.seconds.values())
        data: dict[str, Any] = {"total_ms": round(total * 1000)}
        for kind in STEPS:
            data[f"{kind}_ms"] = round(self.seconds[kind] * 1000)
        data["otros_ms"] = max(round((total - known) * 1000), 0)
        data["llamadas"] = {kind: self.calls[kind] for kind in STEPS}
        return data


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
def step(kind: str) -> Iterator[None]:
    timer = _current.get()
    if timer is None:
        yield
        return
    timer.enter(kind)
    try:
        yield
    finally:
        timer.leave()


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
