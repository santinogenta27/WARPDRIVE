"""Rutas de la vista "detrás de escena" (Hito 8, tarea 6; decisión 041).

- `GET /demo/{slug}`: la página, con el sitio de la tienda y su widget a la izquierda y el panel
  en vivo a la derecha.
- `GET /demo/{slug}/live?session=…`: los pasos de la conversación de esa sesión del chat (el
  panel lo consulta cada segundo y medio). Solo lectura.

Las dos dan 404 si la empresa no la activó (apagada por defecto).
"""

import html
import uuid
from pathlib import Path
from typing import Any

import psycopg
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from app import i18n
from app.channels.web import widget
from app.channels.web.channel import _TOKEN, token_hash
from app.channels.web.routes import Deps, _channel_tenant
from app.db.connection import tenant_scope
from app.demo import behind, store
from app.panel import english

router = APIRouter()
PAGE = Path(__file__).resolve().parent / "static" / "behind.html"
TITLES = {"en": "{name} · Behind the scenes", "es": "{name} · Detrás de escena"}


def _company(deps: Any, slug: str) -> tuple[Any, uuid.UUID, str, bool]:
    """(su configuración del widget, la empresa, su idioma, ¿es Northwind?) o 404."""
    with deps.connection() as conn:
        tenant_id = _channel_tenant(conn, slug)
        if tenant_id is None or not behind.enabled(conn, tenant_id):
            raise HTTPException(status_code=404, detail="No disponible")
        found = widget.load(conn, tenant_id)
        with tenant_scope(conn, tenant_id):
            row = conn.execute(
                "select settings ->> 'demo' from tenants where id = %s", (tenant_id,)
            ).fetchone()
    return found, tenant_id, i18n.normalize(found.language), bool(row and row[0] == "northwind")


def render(*, name: str, slug: str, accent: str, language: str, northwind: bool) -> str:
    """La página: el sitio de la tienda (`app/demo/store.py`), el widget y el panel en vivo."""
    values = {
        "lang": language,
        "title": TITLES[language].format(name=name),
        "name": name,
        "slug": slug,
        "accent": accent.lstrip("#"),
    }
    page = PAGE.read_text(encoding="utf-8")
    for key, value in values.items():
        page = page.replace("{{" + key + "}}", html.escape(value, quote=True))
    return page.replace("{{store}}", store.render(name, language, northwind))


@router.get("/demo/{slug}", response_class=HTMLResponse, include_in_schema=False)
def behind_page(slug: str, deps: Deps) -> HTMLResponse:
    found, _, language, northwind = _company(deps, slug)
    page = render(
        name=found.name, slug=slug, accent=found.accent, language=language, northwind=northwind
    )
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


def _session_tenant(conn: psycopg.Connection, session: str) -> uuid.UUID | None:
    row = conn.execute(
        "select tenant_id from web_sessions where token_hash = %s", (token_hash(session),)
    ).fetchone()
    return row[0] if row else None


@router.get("/demo/{slug}/live", include_in_schema=False)
def behind_live(slug: str, deps: Deps, session: str = "") -> JSONResponse:
    if not _TOKEN.match(session):
        raise HTTPException(status_code=401, detail="Sesión inválida")
    _, tenant_id, language, _ = _company(deps, slug)
    with deps.connection() as conn:
        # La sesión tiene que ser del chat de esta empresa: si no, no hay nada que mostrar.
        if _session_tenant(conn, session) != tenant_id:
            raise HTTPException(status_code=401, detail="Sesión inválida")
        english.set_language(language)
        data = behind.steps(conn, tenant_id, session)
    return JSONResponse(data, headers={"Cache-Control": "no-store"})
