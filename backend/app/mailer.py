"""Envío de emails transaccionales (interfaz `Mailer` de la spec).

Todavía no hay proveedor: `ConsoleMailer` escribe el email en la consola del servidor, que en
la demo es donde se lee el código de verificación (docs/progreso.md).
"""

import logging
import sys
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)


class _CurrentStderr(logging.StreamHandler):
    """Escribe en el stderr vigente (el que junta Render en los logs). StreamHandler hace flush
    después de cada línea: el código aparece en el momento."""

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, value: Any) -> None:
        pass


# Handler propio: no depende de cómo configure uvicorn el logging (no le pone handler al logger
# raíz). Sin propagar, la línea no sale dos veces si alguien configura el raíz.
_handler = _CurrentStderr()
_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)
logger.propagate = False


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    body: str


class Mailer(Protocol):
    def send(self, email: Email) -> None: ...


class ConsoleMailer:
    """Solo para desarrollo y la demo: no envía nada, lo muestra en la consola."""

    def send(self, email: Email) -> None:
        logger.warning(
            "[Mailer de consola, no se envió] Para: %s | %s | %s",
            email.to,
            email.subject,
            email.body,
        )
        _handler.flush()
