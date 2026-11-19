"""Webhook de la WhatsApp Cloud API.

GET: handshake de verificación de Meta. POST: verifica la firma `X-Hub-Signature-256`, guarda
cada mensaje como job (clave de idempotencia: el wamid, porque Meta reenvía webhooks) y
responde 200 enseguida. El agente corre en el worker (app/channels/whatsapp/handler.py).
"""

import hashlib
import hmac
import json
import logging
import os
from collections.abc import Iterator
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Protocol

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import PlainTextResponse

from app.channels.base import InboundMessage
from app.config import load_dotenv
from app.db.connection import connect
from app.jobs.queue import enqueue

__all__ = ["JOB_KIND", "ConnectionStore", "InboundMessage", "MessageStore", "parse_webhook"]

logger = logging.getLogger(__name__)
router = APIRouter()
JOB_KIND = "whatsapp_message"


def parse_webhook(payload: dict[str, Any]) -> list[InboundMessage]:
    """Mensajes entrantes del payload. Los estados (entregado, leído) se ignoran."""
    found: list[InboundMessage] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            value = change.get("value") or {}
            number = str((value.get("metadata") or {}).get("phone_number_id", ""))
            names = {
                str(c.get("wa_id")): str((c.get("profile") or {}).get("name") or "")
                for c in value.get("contacts") or []
            }
            for message in value.get("messages") or []:
                sender = str(message.get("from") or "")
                if not number or not sender or not message.get("id"):
                    continue
                found.append(
                    InboundMessage(
                        phone_number_id=number,
                        wamid=str(message["id"]),
                        sender=sender,
                        timestamp=int(message.get("timestamp") or 0),
                        type=str(message.get("type") or "unknown"),
                        text=str((message.get("text") or {}).get("body") or ""),
                        name=names.get(sender, ""),
                    )
                )
    return found


def failed_deliveries(payload: dict[str, Any]) -> list[str]:
    """Envíos que Meta aceptó (200) pero no pudo entregar: llegan después como estado `failed`.

    Devuelve una línea por envío con el wamid y el error, sin el teléfono del destinatario.
    """
    found: list[str] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            for status in (change.get("value") or {}).get("statuses") or []:
                if status.get("status") != "failed":
                    continue
                errors = ", ".join(
                    f"{e.get('code')} {e.get('title') or e.get('message') or ''}".strip()
                    for e in status.get("errors") or []
                )
                found.append(f"{status.get('id')}: {errors or 'sin detalle'}")
    return found


def valid_signature(body: bytes, header: str | None, app_secret: str) -> bool:
    if not app_secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.removeprefix("sha256="))


# -- dependencias (los tests las reemplazan) -------------------------------------------------
@dataclass(frozen=True)
class WebhookSettings:
    app_secret: str
    verify_token: str


def webhook_settings() -> WebhookSettings:
    load_dotenv()
    return WebhookSettings(
        app_secret=os.environ.get("WHATSAPP_APP_SECRET", ""),
        verify_token=os.environ.get("WHATSAPP_VERIFY_TOKEN", ""),
    )


class MessageStore(Protocol):
    def save(self, messages: list[InboundMessage]) -> None: ...


class ConnectionStore:
    """Encola los mensajes en la conexión dada; con `commit`, confirma antes de responder.

    `kind` y `key_prefix` permiten usarlo con otros canales (Twilio)."""

    def __init__(
        self,
        conn: psycopg.Connection,
        *,
        commit: bool,
        kind: str = JOB_KIND,
        key_prefix: str = "whatsapp",
    ) -> None:
        self._conn = conn
        self._commit = commit
        self._kind = kind
        self._key_prefix = key_prefix

    def save(self, messages: list[InboundMessage]) -> None:
        for message in messages:
            enqueue(
                self._conn,
                self._kind,
                asdict(message),
                idempotency_key=f"{self._key_prefix}:{message.wamid}",
            )
        if self._commit:
            self._conn.commit()


def message_store() -> Iterator[MessageStore]:
    conn = connect()
    try:
        yield ConnectionStore(conn, commit=True)
    finally:
        conn.close()


# -- rutas -----------------------------------------------------------------------------------
Settings = Annotated[WebhookSettings, Depends(webhook_settings)]
Store = Annotated[MessageStore, Depends(message_store)]


@router.get("/webhooks/whatsapp", response_class=PlainTextResponse)
def verify(
    settings: Settings,
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
) -> str:
    if (
        mode == "subscribe"
        and settings.verify_token
        and hmac.compare_digest(token, settings.verify_token)
    ):
        return challenge
    raise HTTPException(status_code=403, detail="Token de verificación inválido")


@router.post("/webhooks/whatsapp")
async def receive(request: Request, settings: Settings, store: Store) -> dict[str, int]:
    body = await request.body()
    if not valid_signature(body, request.headers.get("X-Hub-Signature-256"), settings.app_secret):
        logger.warning("Webhook de WhatsApp con firma inválida: se descarta")
        raise HTTPException(status_code=401, detail="Firma inválida")
    try:
        payload = json.loads(body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="JSON inválido") from error
    for failure in failed_deliveries(payload):
        logger.warning("Meta no entregó un mensaje enviado: %s", failure)
    messages = parse_webhook(payload)
    if messages:
        await run_in_threadpool(store.save, messages)
    return {"received": len(messages)}


# -- webhook propio de cada empresa (WhatsApp self-service, decisión 068) ----------------------
def lookup_connection() -> Iterator[psycopg.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


Lookup = Annotated[psycopg.Connection, Depends(lookup_connection)]


@router.get("/webhooks/whatsapp/{key}", response_class=PlainTextResponse)
def verify_company(
    key: str,
    conn: Lookup,
    mode: Annotated[str, Query(alias="hub.mode")] = "",
    token: Annotated[str, Query(alias="hub.verify_token")] = "",
    challenge: Annotated[str, Query(alias="hub.challenge")] = "",
) -> str:
    """La verificación de Meta con el verify token de esa empresa."""
    from app.channels.whatsapp.selfservice import channel_for_webhook

    channel = channel_for_webhook(conn, key)
    if (
        channel is not None
        and mode == "subscribe"
        and hmac.compare_digest(token.encode(), channel.verify_token.encode())
    ):
        return challenge
    raise HTTPException(status_code=403, detail="Token de verificación inválido")


@router.post("/webhooks/whatsapp/{key}")
async def receive_company(key: str, request: Request, conn: Lookup, store: Store) -> dict[str, int]:
    """Los mensajes de la empresa, firmados con el App Secret de su app de Meta. Solo entran
    los de su número; con el canal inactivo no entra ninguno."""
    from app.channels.whatsapp.selfservice import channel_for_webhook

    body = await request.body()
    channel = await run_in_threadpool(channel_for_webhook, conn, key)
    header = request.headers.get("X-Hub-Signature-256")
    if channel is None or not valid_signature(body, header, channel.app_secret):
        logger.warning("Webhook de WhatsApp de una empresa con firma inválida: se descarta")
        raise HTTPException(status_code=401, detail="Firma inválida")
    try:
        payload = json.loads(body)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="JSON inválido") from error
    for failure in failed_deliveries(payload):
        logger.warning("Meta no entregó un mensaje enviado: %s", failure)
    messages = [
        m
        for m in parse_webhook(payload)
        if channel.status == "active" and m.phone_number_id == channel.phone_number_id
    ]
    if messages:
        await run_in_threadpool(store.save, messages)
    return {"received": len(messages)}
