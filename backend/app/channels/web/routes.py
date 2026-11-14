"""Rutas del chat web: sesión y mensajes con respuesta por Server-Sent Events.

POST /chat/session crea una sesión anónima (sin login) del tenant del chat. POST /chat/messages
recibe un mensaje y responde con un stream `text/event-stream`:

- `typing`: el agente está escribiendo.
- `message`: una respuesta (`{"text": ...}`).
- `done`: fin, con los mensajes que quedan (`{"remaining": n, "remaining_today": m}`): de la
  sesión y de la IP en el día.
- `limit`: se llegó a un límite (`scope`: `session`, `ip` o `daily`); el agente no corre.
- `error`: no se pudo responder; no se descuenta el mensaje.

La página del chat (`/`, `/c/<enlace>`) y el widget que un cliente instala en su sitio
(`/widget.js`, decisión 031) usan estas mismas rutas. Desde otro sitio, el chat solo responde
si su dominio está en la lista de la empresa.

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
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse

from app import i18n
from app.agent.loop import Agent
from app.channels.base import InboundMessage, InvalidSignature
from app.channels.inbound import handle_inbound
from app.channels.web import widget
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


def limit_message(scope: str, *, demo: bool, language: str = "es") -> str:
    """El aviso de un límite, en el idioma de la empresa (decisión 036). En el chat de una
    empresa (su página o el widget en su sitio) no se habla de "la demo"."""
    return i18n.text(language, f"limit_{'demo_' if demo else ''}{scope}", limit=MESSAGE_LIMIT)


LIMIT_MESSAGES = {s: limit_message(s, demo=True) for s in ("session", "ip", "daily")}
COMPANY_LIMIT_MESSAGES = {s: limit_message(s, demo=False) for s in ("session", "ip", "daily")}
ERROR_MESSAGE = i18n.MESSAGES["es"]["chat_error"]
FORBIDDEN = i18n.MESSAGES["es"]["forbidden"]


class LimitReached(Exception):
    def __init__(self, scope: str) -> None:
        super().__init__(scope)
        self.scope = scope


class UnknownSession(Exception):
    pass


class OriginNotAllowed(Exception):
    """El pedido viene de un sitio que no está en la lista de dominios de la empresa."""


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


STATIC = Path(__file__).resolve().parent / "static"
_SLUG = re.compile(r"^[a-z0-9-]{3,60}$")
# Preguntas sugeridas de la demo, si nadie cargó otras en el panel.
DEMO_SUGGESTIONS = [
    "¿Dónde está mi pedido 1001?",
    "¿Tienen cuotas sin interés?",
    "¿Abren los domingos?",
    "Quiero hablar con una persona",
]
DEMO_NOTE = (
    "Demo · Acme Store es una tienda ficticia. Para ver un pedido, verificate con "
    "lucia@example.com."
)
NO_JS = {
    "es": "Para usar el chat hace falta JavaScript.",
    "en": "You need JavaScript to use the chat.",
}


def _shell(
    name: str, slug: str, note: str, accent: str, language: str = "es", *, plain: bool = False
) -> HTMLResponse:
    """La página del chat: un marco que carga `/widget.js` en modo página. Es el mismo widget
    que se instala en el sitio de un cliente, así las dos se ven iguales. `plain`: sin el
    nombre de la empresa en el título (no se pudo cargar)."""
    text = (STATIC / "page.html").read_text(encoding="utf-8")
    title = (
        i18n.text(language, "page_title_plain")
        if plain
        else i18n.text(language, "page_title", empresa=name)
    )
    values = {
        "title": title,
        "slug": slug,
        "note": note,
        "accent": accent.lstrip("#"),
        "lang": i18n.normalize(language),
        "nojs": NO_JS[i18n.normalize(language)],
    }
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", html.escape(value, quote=True))
    return HTMLResponse(text, headers={"Cache-Control": "no-cache"})


def _channel_tenant(conn: psycopg.Connection, slug: str) -> uuid.UUID | None:
    if not _SLUG.match(slug):
        return None
    channel = channel_for_number(conn, f"{WEB_PREFIX}{slug}", kind="web")
    return channel.tenant_id if channel else None


def _slug(deps: WebDeps, canal: str) -> str:
    """El enlace pedido o, sin él, el del chat de la demo."""
    return canal or deps.address.removeprefix(WEB_PREFIX)


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
@router.get("/chat", response_class=HTMLResponse, include_in_schema=False)
def page(deps: Deps) -> HTMLResponse:
    """Página del chat de la demo (Acme Store)."""
    slug = _slug(deps, "")
    with deps.connection() as conn:
        tenant_id = _channel_tenant(conn, slug)
        found = widget.load(conn, tenant_id) if tenant_id else None
    if found is None:  # sin `make demo-setup`: el widget explica que el chat no está disponible
        return _shell("", slug, DEMO_NOTE, widget.DEFAULT_ACCENT, plain=True)
    return _shell(found.name, slug, DEMO_NOTE, found.accent, found.language)


@router.get("/c/{slug}", response_class=HTMLResponse, include_in_schema=False)
def company_chat(slug: str, deps: Deps) -> HTMLResponse:
    """El chat de una empresa en una página propia. El enlace lleva una parte al azar: no se
    adivina."""
    with deps.connection() as conn:
        tenant_id = _channel_tenant(conn, slug)
        if tenant_id is None:
            raise HTTPException(status_code=404, detail="Chat inexistente")
        found = widget.load(conn, tenant_id)
    note = i18n.text(found.language, "page_note", empresa=found.name)
    return _shell(found.name, slug, note, found.accent, found.language)


# -- widget instalable (decisión 031) --------------------------------------------------------
@router.get("/widget.js", include_in_schema=False)
def widget_script() -> Response:
    """El widget: un solo archivo, sin dependencias. El cliente lo carga con una línea de
    `<script>` desde su sitio."""
    return Response(
        (STATIC / "widget.js").read_text(encoding="utf-8"),
        media_type="application/javascript; charset=utf-8",
        headers={
            "Cache-Control": "public, max-age=300",
            "Access-Control-Allow-Origin": "*",
            "Cross-Origin-Resource-Policy": "cross-origin",
            "X-Content-Type-Options": "nosniff",
        },
    )


def _cors(request: Request, domains: tuple[str, ...]) -> dict[str, str]:
    """Encabezados CORS para un pedido al chat. `OriginNotAllowed` si viene de un sitio que la
    empresa no habilitó."""
    origin = request.headers.get("origin")
    if not widget.origin_allowed(origin, request.headers.get("host", ""), domains):
        raise OriginNotAllowed
    return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"} if origin else {}


@router.options("/chat/session", include_in_schema=False)
@router.options("/chat/messages", include_in_schema=False)
@router.options("/chat/updates", include_in_schema=False)
def preflight(request: Request) -> Response:
    """Antes de mandar JSON a otro servidor, el navegador pregunta. Acá se le dice que sí: el
    dominio se controla en el pedido real, que es el que trae la empresa."""
    return Response(
        status_code=204,
        headers={
            "Access-Control-Allow-Origin": request.headers.get("origin") or "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Max-Age": "600",
            "Vary": "Origin",
        },
    )


@router.get("/chat/widget/config")
def widget_config(request: Request, deps: Deps, canal: str = "") -> JSONResponse:
    """Lo que el widget necesita para dibujarse: la marca de la empresa. Si el sitio no está
    habilitado responde `allowed: false`, y el widget no se muestra."""
    slug = _slug(deps, canal)
    with deps.connection() as conn:
        tenant_id = _channel_tenant(conn, slug)
        if tenant_id is None:
            raise HTTPException(status_code=404, detail="Chat inexistente")
        found = widget.load(conn, tenant_id)
    headers = {"Access-Control-Allow-Origin": "*", "Cache-Control": "no-cache"}
    origin, host = request.headers.get("origin"), request.headers.get("host", "")
    if not widget.origin_allowed(origin, host, found.domains):
        return JSONResponse({"allowed": False}, headers=headers)
    version = int(found.updated_at.timestamp()) if found.updated_at else 0
    return JSONResponse(
        {
            "allowed": True,
            "name": found.name,
            "accent": found.accent,
            "logo": f"/chat/widget/logo/{slug}?v={version}" if found.has_logo else None,
            "greeting": widget.greeting(found.name, found.language),
            "suggestions": list(found.suggestions)
            or (DEMO_SUGGESTIONS if slug == _slug(deps, "") else []),
            "limit": MESSAGE_LIMIT,
            "lang": found.language,
        },
        headers=headers,
    )


@router.get("/chat/widget/logo/{slug}", include_in_schema=False)
def widget_logo(slug: str, deps: Deps) -> Response:
    with deps.connection() as conn:
        tenant_id = _channel_tenant(conn, slug)
        found = widget.logo(conn, tenant_id) if tenant_id else None
    if found is None:
        raise HTTPException(status_code=404, detail="Sin logo")
    return Response(
        found[0],
        media_type=found[1],
        headers={
            "Cache-Control": "public, max-age=300",
            "Cross-Origin-Resource-Policy": "cross-origin",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/chat/session", status_code=201)
def create_session(
    request: Request, response: Response, deps: Deps, canal: str = ""
) -> dict[str, Any]:
    """Sesión nueva en el chat de la demo o, con `canal`, en el de una empresa."""
    if canal and not _SLUG.match(canal):
        raise HTTPException(status_code=404, detail="Chat inexistente")
    address = f"{WEB_PREFIX}{canal}" if canal else deps.address
    token = secrets.token_urlsafe(32)
    with deps.connection() as conn:
        tenant_id = _tenant(conn, address)
        try:
            response.headers.update(_cors(request, widget.load(conn, tenant_id).domains))
        except OriginNotAllowed as error:
            raise HTTPException(status_code=403, detail=FORBIDDEN) from error
        with tenant_scope(conn, tenant_id):
            conn.execute(
                "insert into web_sessions (tenant_id, token_hash) values (%s, %s)",
                (tenant_id, token_hash(token)),
            )
    return {"session": token, "limit": MESSAGE_LIMIT, "remaining": MESSAGE_LIMIT}


def _precheck(
    deps: WebDeps, token: str, ip_hash: str, request: Request
) -> tuple[str | None, str, uuid.UUID, dict[str, str], str]:
    """(límite alcanzado antes de abrir el stream, o None; dirección del chat de la sesión; su
    empresa; encabezados CORS; su idioma). `OriginNotAllowed` si el sitio no está habilitado."""
    with deps.connection() as conn:
        tenant_id, address = _route(conn, token)
        found = widget.load(conn, tenant_id)
        cors = _cors(request, found.domains)
        scope = _exceeded(deps, _usage(conn, tenant_id, token, ip_hash, lock=False))
        return scope, address, tenant_id, cors, found.language


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


def _limit_event(scope: str, demo: bool = True, language: str = "es") -> str:
    message = limit_message(scope, demo=demo, language=language)
    return _event("limit", {"scope": scope, "message": message})


async def _stream(
    deps: WebDeps,
    channel: WebChannel,
    messages: list[InboundMessage],
    token: str,
    ip_hash: str,
    tenant_id: uuid.UUID,
    language: str = "es",
) -> AsyncIterator[str]:
    yield _event("typing", {})
    try:
        remaining = await run_in_threadpool(_process, deps, channel, messages, token, ip_hash)
    except LimitReached as limit:
        yield _limit_event(limit.scope, channel.address == deps.address, language)
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
        yield _event("error", {"message": i18n.text(language, "chat_error")})
        return
    for text in channel.outbox:
        yield _event("message", {"text": text, **quick_replies(text)})
    yield _event("done", remaining)


def quick_replies(text: str) -> dict[str, list[str]]:
    """Botones de respuesta rápida para la pregunta de satisfacción (decisión 033): el widget
    los muestra bajo el mensaje y, al tocarlos, manda ese texto como si se hubiera escrito."""
    for lang, answers in (("es", ["Sí", "No"]), ("en", ["Yes", "No"])):
        if i18n.text(lang, "csat_question") in text:
            return {"replies": answers}
    return {}


async def _only(chunk: str) -> AsyncIterator[str]:
    yield chunk


@router.get("/chat/updates")
def updates(
    request: Request, response: Response, deps: Deps, session: str = "", after: str = ""
) -> dict[str, Any]:
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
            response.headers.update(_cors(request, widget.load(conn, tenant_id).domains))
        except UnknownSession as error:
            raise HTTPException(status_code=401, detail="Sesión inválida") from error
        except OriginNotAllowed as error:
            raise HTTPException(status_code=403, detail=FORBIDDEN) from error
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
        scope, address, tenant_id, cors, language = await run_in_threadpool(
            _precheck, deps, token, ip_hash, request
        )
        channel = WebChannel(address)  # el chat de la empresa de la sesión
        messages = channel.receive(str(request.url), body, request.headers)
    except (InvalidSignature, UnknownSession) as error:
        raise HTTPException(status_code=401, detail="Sesión inválida") from error
    except OriginNotAllowed as error:
        raise HTTPException(status_code=403, detail=FORBIDDEN) from error
    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no", **cors}
    if scope is not None:  # sin correr el agente
        stream = _only(_limit_event(scope, address == deps.address, language))
    else:
        stream = _stream(deps, channel, messages, token, ip_hash, tenant_id, language)
    return StreamingResponse(stream, media_type="text/event-stream", headers=headers)
