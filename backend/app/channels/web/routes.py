"""Rutas del chat web: sesión y mensajes con respuesta por Server-Sent Events.

POST /chat/session crea una sesión anónima (sin login) del tenant del chat. POST /chat/messages
recibe un mensaje y responde con un stream `text/event-stream`:

- `typing`: el agente está escribiendo.
- `message`: una respuesta (`{"text": ...}`).
- `done`: fin, con los mensajes que quedan (`{"remaining": n, "remaining_today": m}`): de la
  sesión y de la IP en el día.
- `limit`: se llegó a un límite (`scope`: `session`, `ip` o `daily`); el agente no corre.
- `error`: no se pudo responder; no se descuenta el mensaje.

Límites (la demo es pública y el modelo gratuito tiene cuota diaria): 20 mensajes por sesión,
un máximo por IP por día y un máximo total por día. El agente corre dentro del pedido, en una
transacción: si falla, se revierte todo y no se descuenta nada.
"""

import hashlib
import html
import json
import logging
import os
import re
import secrets
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
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
from app.channels.web.channel import (
    _TOKEN,
    DEMO_ADDRESS,
    WebChannel,
    session_token,
    token_hash,
)
from app.channels.whatsapp.routing import channel_for_number
from app.config import load_dotenv
from app.contacts import WEB_PREFIX, hash_address
from app.db.connection import connect, tenant_scope
from app.llm.errors import QuotaExhausted
from app.obs import alerts

logger = logging.getLogger(__name__)
router = APIRouter()

MESSAGE_LIMIT = 20  # por sesión (spec: demo pública)
LIMIT_MESSAGES = {
    "session": f"Llegaste al límite de {MESSAGE_LIMIT} mensajes de esta conversación. "
    "¡Gracias por probar la demo!",
    "ip": "Llegaste al límite de uso diario de la demo desde tu conexión. "
    "Volvé a probar mañana. ¡Gracias!",
    "daily": "La demo llegó a su límite de uso por hoy. Volvé a probar mañana. ¡Gracias!",
}
ERROR_MESSAGE = "Tuvimos un problema para responderte. Probá de nuevo en un rato."


class LimitReached(Exception):
    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope


class UnknownSession(Exception):
    pass


@dataclass
class WebDeps:
    connection: Callable[[], AbstractContextManager[psycopg.Connection]]
    agent: Callable[[psycopg.Connection], Agent]
    address: str = DEMO_ADDRESS  # canal web en la tabla channels (tenant Acme en la demo)
    # El agente de la empresa de la sesión (cada empresa tiene su chat en /c/<enlace>). Sin
    # esto, todas las sesiones usan `agent`.
    agent_for: Callable[[psycopg.Connection, uuid.UUID], Agent] | None = None
    # Dónde se guarda un error que cortó una respuesta (la transacción del turno se revierte).
    record_error: Callable[[uuid.UUID, str, str], None] | None = None
    ip_daily_limit: int = 30  # mensajes por IP en 24 h
    daily_limit: int = 300  # mensajes en total en 24 h (cuida la cuota diaria del modelo)
    client_ip: str = "direct"  # de dónde sale la IP del cliente (ver client_ip)


def client_ip(mode: str, headers: Mapping[str, str], peer: str | None) -> str:
    """IP del cliente según `mode`: `direct` (el par TCP; uvicorn ya aplica X-Forwarded-For de
    un proxy local como cloudflared), `<encabezado>` o `<encabezado>:first|last` (para listas
    como X-Forwarded-For). Sin el encabezado, el par TCP."""
    fallback = peer or "desconocida"
    if mode == "direct":
        return fallback
    name, _, position = mode.partition(":")
    values = [v.strip() for v in (headers.get(name) or "").split(",") if v.strip()]
    if not values:
        return fallback
    return values[-1] if position == "last" else values[0]


def _ip_hash(ip: str) -> str:
    """Hash con sal (ENCRYPTION_KEY): alcanza para contar, sin guardar la IP."""
    salt = os.environ.get("ENCRYPTION_KEY", "")
    return hashlib.sha256(f"{salt}:{ip}".encode()).hexdigest()


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
    from app.companies.agent import build_agent
    from app.demo.acme import build_demo_agent, shared_acme

    load_dotenv()
    return WebDeps(
        connection=_transaction,
        # La API de Acme simulada vive en memoria y la comparte el panel ("Reiniciar demo" la
        # vuelve a su estado inicial): se toma la vigente en cada mensaje.
        agent=lambda conn: build_demo_agent(conn, shared_acme()),
        # Cada sesión usa el agente de su empresa: Acme con la API simulada, las demás con su
        # sistema (o en modo simulado hasta que activen la conexión).
        agent_for=build_agent,
        record_error=alerts.persist_error,
        ip_daily_limit=int(os.environ.get("WEB_CHAT_IP_DAILY_LIMIT") or 30),
        daily_limit=int(os.environ.get("WEB_CHAT_DAILY_LIMIT") or 300),
        client_ip=os.environ.get("WEB_CHAT_CLIENT_IP") or "direct",
    )


def web_deps() -> WebDeps:
    return _production_deps()


Deps = Annotated[WebDeps, Depends(web_deps)]


def _tenant(conn: psycopg.Connection, address: str) -> uuid.UUID:
    channel = channel_for_number(conn, address, kind="web")
    if channel is None:
        raise HTTPException(status_code=503, detail="El chat web no está configurado")
    return channel.tenant_id


def _route(conn: psycopg.Connection, token: str) -> tuple[uuid.UUID, str]:
    """(empresa, dirección de su chat web) de una sesión. Es una consulta fuera de un tenant,
    como el ruteo de un webhook: el token de la sesión es lo que identifica a la empresa."""
    row = conn.execute(
        "select s.tenant_id, c.phone_number_id from web_sessions s join channels c"
        " on c.tenant_id = s.tenant_id and c.kind = 'web' and c.status = 'active'"
        " where s.token_hash = %s order by c.created_at limit 1",
        (token_hash(token),),
    ).fetchone()
    if row is None:
        raise UnknownSession
    return row[0], str(row[1])


@dataclass(frozen=True)
class _Usage:
    session: int  # mensajes de la sesión
    ip: int  # de la IP en las últimas 24 h
    total: int  # de todo el chat del tenant en las últimas 24 h


def _usage(
    conn: psycopg.Connection, tenant_id: uuid.UUID, token: str, ip_hash: str, *, lock: bool
) -> _Usage:
    with tenant_scope(conn, tenant_id):
        if lock:  # dos pedidos simultáneos de la misma IP no pasan el límite
            conn.execute("select pg_advisory_xact_lock(hashtext(%s))", (ip_hash,))
        row = conn.execute(
            "select messages from web_sessions where token_hash = %s"
            + (" for update" if lock else ""),
            (token_hash(token),),
        ).fetchone()
        if row is None:
            raise UnknownSession
        counts = conn.execute(
            "select count(*) filter (where ip_hash = %s), count(*) from web_usage"
            " where created_at > now() - interval '1 day'",
            (ip_hash,),
        ).fetchone()
    assert counts is not None
    return _Usage(session=row[0], ip=counts[0], total=counts[1])


def _exceeded(deps: WebDeps, usage: _Usage) -> str | None:
    if usage.session >= MESSAGE_LIMIT:
        return "session"
    if usage.total >= deps.daily_limit:
        return "daily"
    if usage.ip >= deps.ip_daily_limit:
        return "ip"
    return None


PAGE = Path(__file__).resolve().parent / "static" / "index.html"


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/chat", response_class=HTMLResponse, include_in_schema=False)
def page() -> HTMLResponse:
    """Página del chat de Acme Store (un solo archivo, sin recursos externos)."""
    return HTMLResponse(PAGE.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


def _js(value: str) -> str:
    """Cadena de JavaScript segura dentro de <script> (no puede cerrar la etiqueta)."""
    return json.dumps(value).replace("<", "\\u003c").replace(">", "\\u003e")


def company_page(name: str, slug: str) -> str:
    """La página del chat para una empresa: la misma de la demo, con su nombre, su enlace y
    sin los ejemplos de Acme."""
    safe = html.escape(name)
    text = PAGE.read_text(encoding="utf-8")
    start, end = text.index(HOW_START), text.index(HOW_END)
    text = text[:start] + COMPANY_FOOTER + text[end:]
    text = HELP.sub(COMPANY_HELP, text, count=1)
    text = SUGGESTIONS.sub("var SUGGESTIONS = [];", text, count=1)
    text = text.replace('var KEY = "acme-chat";', f"var KEY = {_js('chat-' + slug)};")
    text = text.replace(
        'fetch("/chat/session", { method: "POST" })',
        f'fetch("/chat/session?canal=" + encodeURIComponent({_js(slug)}),'
        ' { method: "POST" })',
    )
    greeting = f"¡Hola! Soy el asistente virtual de {name}. ¿En qué te puedo ayudar?"
    text = GREETING.sub(lambda _: f"var GREETING = {_js(greeting)};", text, count=1)
    text = text.replace('"Equipo de Acme Store"', _js(f"Equipo de {name}"))
    return text.replace("Acme Store", safe)


HOW_START = "Demo de Enterprise Data AI"
HOW_END = '<button type="button" id="how">'
COMPANY_FOOTER = "Chat de prueba · "
# El cuadro "Cómo probarla" de la demo trae ejemplos de Acme: cada empresa tiene el suyo.
HELP = re.compile(r'<h2 id="help-title">.*?</ul>', re.S)
COMPANY_HELP = (
    '<h2 id="help-title">Chat de prueba</h2><p class="lead">Este chat usa el asistente de '
    "Acme Store con sus tipos de caso activos, sus herramientas aprobadas y sus documentos."
    "</p><ul><li>Escribí como lo haría un cliente. Las conversaciones aparecen en el panel, "
    "en Casos.</li><li>Cada conversación tiene hasta 20 mensajes.</li></ul>"
)
SUGGESTIONS = re.compile(r"var SUGGESTIONS = \[.*?\];")
GREETING = re.compile(r'var GREETING = ".*?";')
_SLUG = re.compile(r"^[a-z0-9-]{3,60}$")


@router.get("/c/{slug}", response_class=HTMLResponse, include_in_schema=False)
def company_chat(slug: str, deps: Deps) -> HTMLResponse:
    """El chat de prueba de una empresa. El enlace lleva una parte al azar: no se adivina."""
    if not _SLUG.match(slug):
        raise HTTPException(status_code=404, detail="Chat inexistente")
    with deps.connection() as conn:
        channel = channel_for_number(conn, f"{WEB_PREFIX}{slug}", kind="web")
        if channel is None:
            raise HTTPException(status_code=404, detail="Chat inexistente")
        with tenant_scope(conn, channel.tenant_id):
            row = conn.execute(
                "select name from tenants where id = %s", (channel.tenant_id,)
            ).fetchone()
    assert row is not None
    return HTMLResponse(company_page(str(row[0]), slug), headers={"Cache-Control": "no-cache"})


@router.post("/chat/session", status_code=201)
def create_session(deps: Deps, canal: str = "") -> dict[str, Any]:
    """Sesión nueva en el chat de la demo o, con `canal`, en el de una empresa."""
    if canal and not _SLUG.match(canal):
        raise HTTPException(status_code=404, detail="Chat inexistente")
    address = f"{WEB_PREFIX}{canal}" if canal else deps.address
    token = secrets.token_urlsafe(32)
    with deps.connection() as conn:
        tenant_id = _tenant(conn, address)
        with tenant_scope(conn, tenant_id):
            conn.execute(
                "insert into web_sessions (tenant_id, token_hash) values (%s, %s)",
                (tenant_id, token_hash(token)),
            )
    return {"session": token, "limit": MESSAGE_LIMIT, "remaining": MESSAGE_LIMIT}


def _precheck(deps: WebDeps, token: str, ip_hash: str) -> tuple[str | None, str, uuid.UUID]:
    """(límite alcanzado antes de abrir el stream, o None; dirección del chat de la sesión; su
    empresa)."""
    with deps.connection() as conn:
        tenant_id, address = _route(conn, token)
        scope = _exceeded(deps, _usage(conn, tenant_id, token, ip_hash, lock=False))
        return scope, address, tenant_id


def _process(
    deps: WebDeps,
    channel: WebChannel,
    messages: list[InboundMessage],
    token: str,
    ip_hash: str,
) -> dict[str, int]:
    """Corre el agente y descuenta el mensaje, en una sola transacción y con los límites
    bloqueados. Devuelve los mensajes que quedan."""
    with deps.connection() as conn:
        tenant_id, _ = _route(conn, token)
        agent = deps.agent
        if deps.agent_for is not None:
            build = deps.agent_for

            def agent(c: psycopg.Connection) -> Agent:
                return build(c, tenant_id)

        usage = _usage(conn, tenant_id, token, ip_hash, lock=True)
        scope = _exceeded(deps, usage)
        if scope is not None:
            raise LimitReached(scope)
        if not messages:
            return {
                "remaining": MESSAGE_LIMIT - usage.session,
                "remaining_today": deps.ip_daily_limit - usage.ip,
            }
        for message in messages:
            handle_inbound(conn, message, channel=channel, agent=agent)
        with tenant_scope(conn, tenant_id):
            conn.execute(
                "update web_sessions set messages = messages + 1 where token_hash = %s",
                (token_hash(token),),
            )
            conn.execute(
                "insert into web_usage (tenant_id, ip_hash) values (%s, %s)",
                (tenant_id, ip_hash),
            )
        return {
            "remaining": MESSAGE_LIMIT - usage.session - 1,
            "remaining_today": max(deps.ip_daily_limit - usage.ip - 1, 0),
        }


def _event(name: str, data: dict[str, Any]) -> str:
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _limit_event(scope: str) -> str:
    return _event("limit", {"scope": scope, "message": LIMIT_MESSAGES[scope]})


async def _stream(
    deps: WebDeps,
    channel: WebChannel,
    messages: list[InboundMessage],
    token: str,
    ip_hash: str,
    tenant_id: uuid.UUID,
) -> AsyncIterator[str]:
    yield _event("typing", {})
    try:
        remaining = await run_in_threadpool(_process, deps, channel, messages, token, ip_hash)
    except LimitReached as limit:
        yield _limit_event(limit.scope)
        return
    except Exception as error:
        logger.exception("El chat web no pudo responder")
        # Para las métricas y la alerta de errores: el tipo de error, sin texto del cliente.
        kind = "cuota" if isinstance(error, QuotaExhausted) else "chat_web"
        await run_in_threadpool(
            alerts.record_error,
            tenant_id,
            kind,
            persist=deps.record_error,
            detail=type(error).__name__,
        )
        yield _event("error", {"message": ERROR_MESSAGE})
        return
    for text in channel.outbox:
        yield _event("message", {"text": text})
    yield _event("done", remaining)


async def _only(chunk: str) -> AsyncIterator[str]:
    yield chunk


@router.get("/chat/updates")
def updates(deps: Deps, session: str = "", after: str = "") -> dict[str, Any]:
    """Mensajes de una persona del equipo para esta sesión (respuestas o avisos desde el panel),
    posteriores a `after`. La página lo consulta cada pocos segundos."""
    if not _TOKEN.match(session):
        raise HTTPException(status_code=401, detail="Sesión inválida")
    try:
        since = datetime.fromisoformat(after) if after else datetime.min.replace(tzinfo=UTC)
    except ValueError as error:
        raise HTTPException(status_code=400, detail="Fecha inválida") from error
    with deps.connection() as conn:
        try:
            tenant_id, _ = _route(conn, session)
        except UnknownSession as error:
            raise HTTPException(status_code=401, detail="Sesión inválida") from error
        with tenant_scope(conn, tenant_id):
            rows = conn.execute(
                "select m.id, m.text, m.created_at from case_messages m"
                " join cases c on c.id = m.case_id join contacts k on k.id = c.contact_id"
                " where k.phone_hash = %s and m.role = 'human' and m.created_at > %s"
                " and c.archived_at is null"
                " order by m.created_at limit 20",
                (hash_address(f"{WEB_PREFIX}{token_hash(session)}"), since),
            ).fetchall()
    return {
        "messages": [
            {"id": str(message_id), "text": text, "at": at.isoformat()}
            for message_id, text, at in rows
        ]
    }


@router.post("/chat/messages")
async def post_message(request: Request, deps: Deps) -> StreamingResponse:
    body = await request.body()
    peer = request.client.host if request.client else None
    ip_hash = _ip_hash(client_ip(deps.client_ip, request.headers, peer))
    try:
        token = session_token(body)
        scope, address, tenant_id = await run_in_threadpool(_precheck, deps, token, ip_hash)
        channel = WebChannel(address)  # el chat de la empresa de la sesión
        messages = channel.receive(str(request.url), body, request.headers)
    except (InvalidSignature, UnknownSession) as error:
        raise HTTPException(status_code=401, detail="Sesión inválida") from error
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    if scope is not None:  # sin correr el agente
        stream = _only(_limit_event(scope))
    else:
        stream = _stream(deps, channel, messages, token, ip_hash, tenant_id)
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)
