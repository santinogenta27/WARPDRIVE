"""Rutas del chat web: sesión y mensajes con respuesta por Server-Sent Events.

POST /chat/session crea una sesión anónima (sin login) del tenant del chat. POST /chat/messages
recibe un mensaje y responde con un stream `text/event-stream`:

- `typing`: el agente está escribiendo.
- `message`: una respuesta (`{"text": ...}`).
- `done`: fin, con los mensajes que le quedan a la sesión (`{"remaining": n}`).
- `limit`: la sesión llegó al límite de mensajes; el agente no corre.
- `error`: no se pudo responder; no se descuenta el mensaje.

El agente corre dentro del pedido, en una transacción: si falla, se revierte todo.
"""

import json
import logging
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, StreamingResponse

from app.agent.loop import Agent
from app.channels.base import InboundMessage, InvalidSignature
from app.channels.inbound import handle_inbound
from app.channels.web.channel import DEMO_ADDRESS, WebChannel, session_token, token_hash
from app.channels.whatsapp.routing import channel_for_number
from app.config import load_dotenv
from app.db.connection import connect, tenant_scope

logger = logging.getLogger(__name__)
router = APIRouter()

MESSAGE_LIMIT = 20  # por sesión (spec: demo pública)
LIMIT_MESSAGE = (
    f"Llegaste al límite de {MESSAGE_LIMIT} mensajes de esta demo. ¡Gracias por probarla!"
)
ERROR_MESSAGE = "Tuvimos un problema para responderte. Probá de nuevo en un rato."


class LimitReached(Exception):
    pass


class UnknownSession(Exception):
    pass


@dataclass
class WebDeps:
    connection: Callable[[], AbstractContextManager[psycopg.Connection]]
    agent: Callable[[psycopg.Connection], Agent]
    address: str = DEMO_ADDRESS  # canal web en la tabla channels (tenant Acme en la demo)


@contextmanager
def _transaction() -> Iterator[psycopg.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@lru_cache(maxsize=1)
def _production_deps() -> WebDeps:
    from app.demo.acme import acme_sandbox_client, build_demo_agent

    load_dotenv()
    acme = acme_sandbox_client()  # la API de Acme simulada vive mientras corre la API
    return WebDeps(connection=_transaction, agent=lambda conn: build_demo_agent(conn, acme))


def web_deps() -> WebDeps:
    return _production_deps()


Deps = Annotated[WebDeps, Depends(web_deps)]


def _tenant(conn: psycopg.Connection, address: str) -> uuid.UUID:
    channel = channel_for_number(conn, address, kind="web")
    if channel is None:
        raise HTTPException(status_code=503, detail="El chat web no está configurado")
    return channel.tenant_id


def _used(conn: psycopg.Connection, tenant_id: uuid.UUID, token: str, *, lock: bool) -> int:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select messages from web_sessions where token_hash = %s"
            + (" for update" if lock else ""),
            (token_hash(token),),
        ).fetchone()
    if row is None:
        raise UnknownSession
    used: int = row[0]
    return used


PAGE = Path(__file__).resolve().parent / "static" / "index.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/chat", response_class=HTMLResponse, include_in_schema=False)
def page() -> HTMLResponse:
    """Página del chat de Acme Store (un solo archivo, sin recursos externos)."""
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


@router.post("/chat/session", status_code=201)
def create_session(deps: Deps) -> dict[str, Any]:
    token = secrets.token_urlsafe(32)
    with deps.connection() as conn:
        tenant_id = _tenant(conn, deps.address)
        with tenant_scope(conn, tenant_id):
            conn.execute(
                "insert into web_sessions (tenant_id, token_hash) values (%s, %s)",
                (tenant_id, token_hash(token)),
            )
    return {"session": token, "limit": MESSAGE_LIMIT, "remaining": MESSAGE_LIMIT}


def _remaining(deps: WebDeps, token: str) -> int:
    with deps.connection() as conn:
        return MESSAGE_LIMIT - _used(conn, _tenant(conn, deps.address), token, lock=False)


def _process(deps: WebDeps, channel: WebChannel, messages: list[InboundMessage], token: str) -> int:
    """Corre el agente y descuenta el mensaje, en una sola transacción. Devuelve los restantes."""
    with deps.connection() as conn:
        tenant_id = _tenant(conn, deps.address)
        used = _used(conn, tenant_id, token, lock=True)  # dos pestañas no pasan el límite
        if used >= MESSAGE_LIMIT:
            raise LimitReached
        if not messages:
            return MESSAGE_LIMIT - used
        for message in messages:
            handle_inbound(conn, message, channel=channel, agent=deps.agent)
        with tenant_scope(conn, tenant_id):
            conn.execute(
                "update web_sessions set messages = messages + 1 where token_hash = %s",
                (token_hash(token),),
            )
        return MESSAGE_LIMIT - used - 1


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def _stream(
    deps: WebDeps, channel: WebChannel, messages: list[InboundMessage], token: str
) -> AsyncIterator[str]:
    yield _event("typing", {})
    try:
        remaining = await run_in_threadpool(_process, deps, channel, messages, token)
    except LimitReached:
        yield _event("limit", {"message": LIMIT_MESSAGE})
        return
    except Exception:
        logger.exception("El chat web no pudo responder")
        yield _event("error", {"message": ERROR_MESSAGE})
        return
    for text in channel.outbox:
        yield _event("message", {"text": text})
    yield _event("done", {"remaining": remaining})


async def _only(name: str, data: dict[str, Any]) -> AsyncIterator[str]:
    yield _event(name, data)


@router.post("/chat/messages")
async def post_message(request: Request, deps: Deps) -> StreamingResponse:
    body = await request.body()
    channel = WebChannel(deps.address)
    try:
        messages = channel.receive(str(request.url), body, request.headers)
        token = session_token(body)
        remaining = await run_in_threadpool(_remaining, deps, token)
    except (InvalidSignature, UnknownSession) as error:
        raise HTTPException(status_code=401, detail="Sesión inválida") from error
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    if remaining <= 0:  # sin correr el agente
        stream = _only("limit", {"message": LIMIT_MESSAGE})
    else:
        stream = _stream(deps, channel, messages, token)
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)
