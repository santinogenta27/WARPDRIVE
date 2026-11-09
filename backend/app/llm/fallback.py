"""Respaldo entre proveedores: si el principal se queda sin cuota, sigue con el secundario.

Pensado para los niveles gratuitos (Gemini y Groq), que tienen cuota diaria: así la demo
pública no se corta. Solo cambia ante QuotaExhausted; cualquier otro error se propaga. Después
de un agotamiento, el principal se deja descansar (una hora si fue la cuota diaria, un minuto
si fue la por minuto) antes de volver a probarlo.
"""

import logging
import time
from collections.abc import Callable
from typing import Any

from app.llm.client import LLMProvider
from app.llm.errors import QuotaExhausted
from app.llm.types import LLMResponse

logger = logging.getLogger(__name__)
DAILY_REST = 3600.0
MINUTE_REST = 60.0


class FallbackProvider:
    def __init__(
        self,
        primary: LLMProvider,
        secondary: LLMProvider,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.primary = primary
        self.secondary = secondary
        self._clock = clock
        self._rest_until = 0.0

    def create(self, **kwargs: Any) -> LLMResponse:
        if self._clock() >= self._rest_until:
            try:
                return self.primary.create(**kwargs)
            except QuotaExhausted as exhausted:
                rest = DAILY_REST if exhausted.daily else MINUTE_REST
                self._rest_until = self._clock() + rest
                logger.warning(
                    "Proveedor principal sin cuota (%s): sigo con el respaldo por %.0f s",
                    exhausted,
                    rest,
                )
        return self.secondary.create(**kwargs)
