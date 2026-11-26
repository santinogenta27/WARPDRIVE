"""Links en los mensajes del agente, en todos los canales (decisión 103).

- Qué es un link: http o https, suelto o en Markdown `[texto](url)`. A una URL suelta no se le
  pega la puntuación de la frase ("…/faq." o "(…/help)").
- Dominios permitidos de una empresa: el de la plataforma, los de su chat (que incluyen los de su
  tienda conectada, decisión 086), los de su sitio y, en una prueba gratis, el de su tienda.
- WhatsApp: el link va solo en su línea, después del texto, y corto:
  `{PUBLIC_BASE_URL}/go/{código}`. El código se guarda en `short_links` (con tenant_id y RLS) y
  es el mismo para el mismo destino.
- `/go/{código}` solo redirige a destinos guardados por el sistema y de un dominio permitido: no
  es una redirección abierta.
"""

import os
import re
import secrets
import string
import uuid
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import parse_qs, urlsplit

import psycopg
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.db.connection import connect, tenant_scope

router = APIRouter()

CODE_LENGTH = 12
_ALPHABET = string.ascii_letters + string.digits
_CODE = re.compile(rf"^[A-Za-z0-9]{{{CODE_LENGTH}}}$")
# `[texto](url)` o una URL suelta. El texto de Markdown no lleva saltos de línea ni corchetes.
_LINK = re.compile(r"\[([^\]\n]{1,120})\]\(([^)\s]+)\)|\bhttps?://[^\s<>\"'`]+", re.I)
_TRAILING = ".,;:!?'\"]"
_PURCHASE = re.compile(r"/(?:cart|checkouts?)(?:/|$)", re.I)


@dataclass(frozen=True)
class Link:
    start: int
    end: int
    url: str
    label: str  # el texto de Markdown ("" si es una URL suelta)


def _trim(raw: str) -> str:
    """Sin la puntuación final de la frase. Un ")" queda si cierra uno de la URL."""
    while raw:
        last = raw[-1]
        if last == ")" and raw.count("(") >= raw.count(")"):
            break
        if last not in _TRAILING and last != ")":
            break
        raw = raw[:-1]
    return raw


def safe_url(raw: str) -> str | None:
    """La URL si es http o https con un dominio; None si no (javascript:, data:, etc.)."""
    try:
        parts = urlsplit(raw)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return raw


def find(text: str) -> list[Link]:
    found: list[Link] = []
    for match in _LINK.finditer(text):
        if match.group(2) is not None:
            url, end = match.group(2), match.end()
        else:
            url = _trim(match.group(0))
            end = match.start() + len(url)
        if safe_url(url) is not None:
            found.append(Link(match.start(), end, url, match.group(1) or ""))
    return found


def is_purchase(url: str) -> bool:
    return bool(_PURCHASE.search(urlsplit(url).path))


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


# -- dominios permitidos -----------------------------------------------------------------------
def platform_url() -> str:
    return (os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")


def trusted_hosts(conn: psycopg.Connection, tenant_id: uuid.UUID) -> tuple[str, ...]:
    """Los dominios de la plataforma y de la empresa, sin `www.` (un `*.` vale como dominio)."""
    from app.channels.web import widget

    hosts = [host_of(platform_url())]
    hosts += [
        d.removeprefix("*.").removeprefix("www.") for d in widget.load(conn, tenant_id).domains
    ]
    with tenant_scope(conn, tenant_id):
        hosts += [host_of(str(r[0])) for r in conn.execute("select url from sites").fetchall()]
    # La prueba gratis (tabla de la plataforma, fuera de RLS): su tienda.
    row = conn.execute(
        "select domain, site_url from trials where company_id = %s"
        " order by created_at desc limit 1",
        (tenant_id,),
    ).fetchone()
    if row is not None:
        hosts += [str(row[0]).removeprefix("www."), host_of(str(row[1]))]
    return tuple(dict.fromkeys(h for h in hosts if h))


def allowed(url: str, hosts: tuple[str, ...]) -> bool:
    host = host_of(url)
    return bool(host) and any(host == h or host.endswith("." + h) for h in hosts)


# -- links cortos ------------------------------------------------------------------------------
def shorten(conn: psycopg.Connection, tenant_id: uuid.UUID, url: str) -> str:
    """`{PUBLIC_BASE_URL}/go/{código}` para un destino de un dominio permitido; cualquier otro
    queda como está (se ve entero, así el cliente sabe a dónde va). Idempotente."""
    if safe_url(url) is None or not allowed(url, trusted_hosts(conn, tenant_id)):
        return url
    refs = parse_qs(urlsplit(url).query).get("ref")
    ref = refs[0] if refs else None
    # 12 caracteres al azar de 62: no se adivina. El mismo destino devuelve el código que ya tenía.
    code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into short_links (tenant_id, code, target, ref) values (%s, %s, %s, %s)"
            " on conflict (tenant_id, target) do update set target = excluded.target"
            " returning code",
            (tenant_id, code, url, ref),
        ).fetchone()
    assert row is not None
    return f"{platform_url()}/go/{row[0]}"


def for_whatsapp(conn: psycopg.Connection, tenant_id: uuid.UUID, text: str) -> str:
    """WhatsApp no tiene botones en un mensaje común: los links salen del texto (un link en
    Markdown deja su texto) y van cortos, cada uno en su línea, después del texto."""
    found = find(text)
    if not found:
        return text
    pieces, last, urls = [], 0, []
    for link in found:
        pieces.append(text[last : link.start])
        pieces.append(link.label)
        last = link.end
        if link.url not in urls:
            urls.append(link.url)
    pieces.append(text[last:])
    body = "".join(pieces)
    # Lo que quedó de la frase: "listo: . ¿Algo más?" -> "listo. ¿Algo más?".
    body = re.sub(r"[ \t]*:?[ \t]+([.,;!?])", r"\1", body)
    body = re.sub(r"[ \t]+\n", "\n", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    short = [shorten(conn, tenant_id, url) for url in urls]
    return (body + "\n\n" if body else "") + "\n".join(short)


def checkouts(conn: psycopg.Connection, tenant_id: uuid.UUID, text: str) -> list[dict[str, Any]]:
    """El total de cada link de compra del mensaje, de los que anotó el guardián (decisión 063):
    el widget lo muestra en el botón. Nada se saca del texto del modelo."""
    urls = [link.url for link in find(text) if is_purchase(link.url)]
    if not urls:
        return []
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select distinct on (url) url, total, currency from checkout_links"
            " where url = any(%s) order by url, created_at desc",
            (urls,),
        ).fetchall()
    return [
        {"url": str(url), "total": float(total), "currency": str(currency)}
        for url, total, currency in rows
        if isinstance(total, Decimal) and total > 0 and currency
    ]


# -- /go/{código} ------------------------------------------------------------------------------
@contextmanager
def _connection() -> Iterator[psycopg.Connection]:
    conn = connect()
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def go_connection() -> Callable[[], AbstractContextManager[psycopg.Connection]]:
    return _connection


Connection = Annotated[
    Callable[[], AbstractContextManager[psycopg.Connection]], Depends(go_connection)
]

_NOT_FOUND = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Link not found</title><style>
body{margin:0;min-height:100vh;display:grid;place-items:center;background:#f6f7f9;color:#14171c;
font:16px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:420px;padding:32px;text-align:center}h1{font-size:21px;margin:0 0 8px}
p{margin:0 0 6px;color:#5d6470}
@media (prefers-color-scheme:dark){body{background:#0f1114;color:#f1f2f4}p{color:#a4aab4}}
</style></head><body><main><h1>This link isn't available</h1>
<p>It may have expired or been copied incompletely. Go back to the chat and ask again.</p>
<p lang="es">Este link no está disponible. Volvé al chat y pedilo de nuevo.</p>
</main></body></html>"""


def _not_found() -> HTMLResponse:
    return HTMLResponse(_NOT_FOUND, status_code=404, headers={"Cache-Control": "no-store"})


# `:path`: cualquier cosa después de /go/ (también una URL entera) recibe la página amable.
@router.get("/go/{code:path}", include_in_schema=False, response_model=None)
def go(code: str, connection: Connection) -> Response:
    """Redirige al destino guardado. Ningún dato de la dirección (salvo el código) se usa."""
    if not _CODE.match(code):
        return _not_found()
    with connection() as conn:
        # Búsqueda por un código al azar, como el ruteo de un número (channels): fuera de un
        # tenant, y enseguida se confirma el destino con los dominios de esa empresa.
        row = conn.execute(
            "select tenant_id, target from short_links where code = %s", (code,)
        ).fetchone()
        if row is None:
            return _not_found()
        tenant_id, target = row
        if safe_url(str(target)) is None or not allowed(
            str(target), trusted_hosts(conn, tenant_id)
        ):
            return _not_found()
    return RedirectResponse(
        str(target),
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
