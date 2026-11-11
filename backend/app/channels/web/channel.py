"""Canal web (tarea 12b): el chat de la página, detrás de la misma interfaz `Channel`.

`receive()` toma el JSON que manda la página: la sesión hace las veces de firma (sin una sesión
válida no hay mensaje). `send()` deja la respuesta en `outbox`, que la ruta envía al navegador
por Server-Sent Events. Cada pedido HTTP usa su propia instancia.
"""

import hashlib
import json
import re
import time
import uuid
from collections.abc import Mapping

from app.channels.base import InboundMessage, InvalidSignature
from app.contacts import WEB_PREFIX

DEMO_ADDRESS = "web:acme-demo"  # dirección del chat de la demo en la tabla channels
MAX_CHARS = 1000
_TOKEN = re.compile(r"^[A-Za-z0-9_-]{32,64}$")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_token(body: bytes) -> str:
    """Token de sesión del pedido; InvalidSignature si falta o tiene otro formato."""
    try:
        data = json.loads(body)
    except ValueError as error:
        raise InvalidSignature from error
    token = data.get("session") if isinstance(data, dict) else None
    if not isinstance(token, str) or not _TOKEN.match(token):
        raise InvalidSignature
    return token


class WebChannel:
    kind = "web"

    def __init__(self, address: str = DEMO_ADDRESS) -> None:
        self.address = address
        self.outbox: list[str] = []

    def receive(self, url: str, body: bytes, headers: Mapping[str, str]) -> list[InboundMessage]:
        token = session_token(body)
        text = str(json.loads(body).get("text") or "").strip()[:MAX_CHARS]
        if not text:
            return []
        return [
            InboundMessage(
                phone_number_id=self.address,
                wamid=f"web.{uuid.uuid4().hex}",
                # El contacto es la sesión; se identifica por el hash del token, nunca el token.
                sender=f"{WEB_PREFIX}{token_hash(token)}",
                timestamp=int(time.time()),
                type="text",
                text=text,
            )
        ]

    def send(self, *, to: str, body: str) -> str:
        self.outbox.append(body)
        return f"web.{uuid.uuid4().hex}"
