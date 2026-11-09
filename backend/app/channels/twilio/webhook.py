"""Webhook de Twilio para WhatsApp ("A message comes in" en la consola de Twilio).

Valida la firma, encola cada mensaje (clave de idempotencia: el MessageSid, porque Twilio
reintenta) y responde enseguida con TwiML vacío: la respuesta la manda el worker por la API.
"""

import logging
from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from app.channels.base import InvalidSignature
from app.channels.twilio.channel import JOB_KIND, TwilioWhatsApp, twilio_from_env
from app.channels.whatsapp.webhook import ConnectionStore, MessageStore
from app.config import load_dotenv
from app.db.connection import connect

logger = logging.getLogger(__name__)
router = APIRouter()
EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def twilio_channel() -> TwilioWhatsApp:
    load_dotenv()
    channel = twilio_from_env()
    if channel is None:
        raise HTTPException(status_code=503, detail="Twilio no está configurado")
    return channel


def twilio_store() -> Iterator[MessageStore]:
    conn = connect()
    try:
        yield ConnectionStore(conn, commit=True, kind=JOB_KIND, key_prefix="twilio")
    finally:
        conn.close()


@router.post("/webhooks/twilio")
async def receive(
    request: Request,
    channel: Annotated[TwilioWhatsApp, Depends(twilio_channel)],
    store: Annotated[MessageStore, Depends(twilio_store)],
) -> Response:
    body = await request.body()
    # Twilio firma la URL pública; detrás del túnel, uvicorn la reconstruye con X-Forwarded-*.
    try:
        messages = channel.receive(str(request.url), body, request.headers)
    except InvalidSignature as error:
        logger.warning("Webhook de Twilio con firma inválida (%s): se descarta", request.url)
        raise HTTPException(status_code=401, detail="Firma inválida") from error
    if messages:
        await run_in_threadpool(store.save, messages)
    return Response(EMPTY_TWIML, media_type="text/xml")
