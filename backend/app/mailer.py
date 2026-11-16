"""Envío de emails transaccionales (interfaz `Mailer` de la spec).

- `ResendMailer`: envía por Resend. Se activa con `RESEND_API_KEY` y `MAIL_FROM` (decisión 029).
- `ConsoleMailer`: sin esas variables, escribe el email en la consola del servidor. Sirve para
  desarrollo: el código de verificación se lee en los logs.
"""

import logging
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

logger = logging.getLogger(__name__)
TIMEOUT_SECONDS = 10.0
RESERVED_DOMAIN = re.compile(
    r"(?i)@(?:[\w-]+\.)*(?:example\.(?:com|org|net)|[\w-]+\.(?:example|test|invalid|localhost))$"
)


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
    body: str  # texto plano: lo que muestra un cliente de correo sin HTML (y el de consola)
    html: str | None = None


class Mailer(Protocol):
    def send(self, email: Email) -> None: ...


class MailError(Exception):
    """El email no se pudo enviar. El mensaje no lleva el destinatario, el contenido ni la
    clave: se puede escribir en los logs y en `error_events`."""


class ResendMailer:
    """Envía por la API HTTP de Resend (https://resend.com/docs/api-reference/emails)."""

    URL = "https://api.resend.com/emails"

    def __init__(self, api_key: str, sender: str, http: httpx.Client | None = None) -> None:
        self._api_key = api_key
        self._sender = sender
        self._http = http

    def send(self, email: Email) -> None:
        if RESERVED_DOMAIN.search(email.to):
            # Direcciones de prueba (RFC 2606), como las de la demo de Acme: no existen.
            ConsoleMailer().send(email)
            return
        client = self._http or httpx.Client(timeout=TIMEOUT_SECONDS)
        payload = {
            "from": self._sender,
            "to": [email.to],
            "subject": email.subject,
            "text": email.body,
        }
        if email.html:
            payload["html"] = email.html
        try:
            response = client.post(
                self.URL, headers={"Authorization": f"Bearer {self._api_key}"}, json=payload
            )
        except httpx.HTTPError as error:
            raise MailError(f"No se pudo conectar con Resend ({type(error).__name__}).") from None
        finally:
            if self._http is None:
                client.close()
        if response.status_code >= 300:
            raise MailError(f"Resend respondió {response.status_code} ({_error_name(response)}).")


def _error_name(response: httpx.Response) -> str:
    """El tipo de error que informa Resend (`validation_error`, `rate_limit_exceeded`…). Su
    `message` no se usa: puede repetir la dirección del destinatario."""
    try:
        name = response.json().get("name")
    except (ValueError, AttributeError):
        name = None
    return str(name)[:60] if name else "sin detalle"


def mailer_from_env(env: Mapping[str, str] | None = None) -> Mailer:
    """`ResendMailer` si están `RESEND_API_KEY` y `MAIL_FROM`; si no, el de consola."""
    source = os.environ if env is None else env
    api_key = source.get("RESEND_API_KEY", "").strip()
    sender = source.get("MAIL_FROM", "").strip()
    if api_key and sender:
        return ResendMailer(api_key, sender)
    return ConsoleMailer()


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
