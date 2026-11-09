"""Envío de emails transaccionales (interfaz `Mailer` de la spec).

Todavía no hay proveedor: `ConsoleMailer` escribe el email en la consola del servidor, que en
la demo es donde se lee el código de verificación (docs/progreso.md).
"""

import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger(__name__)


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
