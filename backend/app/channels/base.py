"""Lo común a los canales de mensajería: el canal es solo transporte (spec v1.1).

Cada canal implementa `Channel`: `receive()` valida la firma del webhook y devuelve los mensajes
entrantes; `send()` responde. El agente, el guardián y la auditoría son los mismos para todos
(app/channels/inbound.py).
"""

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

import psycopg

from app.contacts import hash_phone
from app.crypto import encrypt
from app.db.connection import tenant_scope
from app.jobs.queue import Job

IGNORED_TYPES = {"sticker", "reaction", "unsupported", "system"}
UNSUPPORTED_MESSAGE = "Por ahora solo puedo leer mensajes de texto. ¿Me lo escribís?"


@dataclass(frozen=True)
class InboundMessage:
    phone_number_id: str  # número de la empresa que recibió el mensaje (ruteo al tenant)
    wamid: str  # id del mensaje en el canal (wamid de Meta, MessageSid de Twilio)
    sender: str  # wa_id del cliente; se borra del job una vez procesado
    timestamp: int
    type: str
    text: str
    name: str = ""  # nombre de perfil de WhatsApp (para el saludo de las plantillas)


class InvalidSignature(Exception):
    """El webhook no viene del proveedor del canal."""


class SendError(Exception):
    """El canal no aceptó la respuesta. `permanent`: reintentar no lo arregla."""

    def __init__(self, message: str, *, code: int | None, permanent: bool) -> None:
        super().__init__(message)
        self.code = code
        self.permanent = permanent


class Channel(Protocol):
    @property
    def kind(self) -> str:  # tipo en la tabla channels
        ...

    def receive(self, url: str, body: bytes, headers: Mapping[str, str]) -> list[InboundMessage]:
        """Valida la firma (lanza InvalidSignature) y devuelve los mensajes del webhook."""
        ...

    def send(self, *, to: str, body: str) -> str:
        """Envía un texto al wa_id `to` y devuelve el id del mensaje (lanza SendError)."""
        ...


def touch_contact(
    conn: psycopg.Connection, tenant_id: uuid.UUID, message: InboundMessage
) -> uuid.UUID:
    """Crea el contacto si no existe, registra la hora del último mensaje del cliente (abre la
    ventana de 24 h) y guarda cifrada la dirección para escribirle después."""
    address = encrypt(json.dumps({"wa_id": message.sender, "name": message.name}))
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into contacts (tenant_id, phone_hash, last_customer_message_at, wa_address_enc)"
            " values (%s, %s, to_timestamp(%s), %s)"
            " on conflict (tenant_id, phone_hash) do update set last_customer_message_at ="
            " greatest(contacts.last_customer_message_at, excluded.last_customer_message_at),"
            " wa_address_enc = excluded.wa_address_enc"
            " returning id",
            (tenant_id, hash_phone(message.sender), message.timestamp, address),
        ).fetchone()
    assert row is not None
    contact_id: uuid.UUID = row[0]
    return contact_id


def forget_sender(conn: psycopg.Connection, job: Job) -> None:
    """El teléfono y el nombre en claro solo hacen falta para responder: después se borran."""
    conn.execute("update jobs set payload = payload - 'sender' - 'name' where id = %s", (job.id,))
