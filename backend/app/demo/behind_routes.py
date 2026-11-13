"""Rutas de la vista "detrás de escena" (Hito 8, tarea 6; decisión 041).

- `GET /demo/{slug}`: la página, con el sitio de la tienda y su widget a la izquierda y el panel
  en vivo a la derecha.
- `GET /demo/{slug}/live?session=…`: los pasos de la conversación de esa sesión del chat (el
  panel lo consulta cada segundo y medio). Solo lectura.

Las dos dan 404 si la empresa no la activó (apagada por defecto).
"""

import html
import json
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
from app.demo import behind, northwind
from app.panel import english

router = APIRouter()
PAGE = Path(__file__).resolve().parent / "static" / "behind.html"
PRODUCTS = northwind.API_DIR / "data" / "products.json"
SHELF = [
    "Trail Runner Jacket",
    "Leather Chelsea Boots",
    "Merino Crew Sweater",
    "Alpine Down Vest",
    "Canvas Field Pants",
]
# Colores de muestra para las fotos de los productos (no hay imágenes en la demo).
SWATCHES = {
    "Lake Blue": ("#5b8db8", "#2f5d7c"),
    "Black": ("#3a3d42", "#16181b"),
    "Moss": ("#7d8f5a", "#4b5a33"),
    "Rust": ("#c0673a", "#83391a"),
    "Navy": ("#3b4f7a", "#1d2a47"),
    "Oat": ("#e2d6bf", "#bba98a"),
    "Charcoal": ("#5a5e64", "#2e3135"),
    "Olive": ("#8a8a52", "#5a5a2f"),
    "Khaki": ("#c9b88c", "#9c8a5c"),
    "White": ("#f7f7f4", "#d9d9d2"),
    "Sage": ("#a9b9a0", "#71836a"),
    "Walnut": ("#8a5a3b", "#553420"),
    "Field Tan": ("#c8a774", "#8d6d3e"),
    "Heather Gray": ("#b7b9bc", "#7e8186"),
}
TEXTS: dict[str, dict[str, Any]] = {
    "en": {
        "title": "{name} · Behind the scenes",
        "links": ("New arrivals", "Outerwear", "Knitwear", "Footwear"),
        "kicker": "Fall collection",
        "headline": "Gear that outlasts the weather.",
        "tagline": "Durable outdoor clothing from Portland, Oregon. Free returns within 30 days.",
        "shelf": "Best sellers",
        "note": "Demo · {name} is a fictional store.",
    },
    "es": {
        "title": "{name} · Detrás de escena",
        "links": ("Novedades", "Abrigos", "Tejidos", "Calzado"),
        "kicker": "Colección de temporada",
        "headline": "Lo que buscás, cuando lo necesitás.",
        "tagline": "La tienda online de {name}. Cambios y devoluciones sin costo.",
        "shelf": "Lo más vendido",
        "note": "Demo · {name} es una tienda ficticia.",
    },
}


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


def _shelf(northwind: bool, heading: str) -> str:
    if not northwind:
        return ""
    # Una fila de cinco, con los productos de los pedidos del guion (campera, botas, sweater).
    wanted = SHELF
    products = [p for p in json.loads(PRODUCTS.read_text(encoding="utf-8")) if p["name"] in wanted]
    products.sort(key=lambda p: wanted.index(p["name"]))
    cards = []
    for product in products:
        first, second = SWATCHES.get(product["colors"][0], ("#c9ccd1", "#8b9097"))
        cards.append(
            f'<div class="card"><div class="swatch" style="--c1:{first};--c2:{second}"></div>'
            f"<b>{html.escape(product['name'])}</b>"
            f"<span>${product['price']:,.0f} · {html.escape(product['colors'][0])}</span></div>"
        )
    return f'<h2>{html.escape(heading)}</h2><div class="grid">{"".join(cards)}</div>'


@router.get("/demo/{slug}", response_class=HTMLResponse, include_in_schema=False)
def behind_page(slug: str, deps: Deps) -> HTMLResponse:
    found, _, language, northwind = _company(deps, slug)
    texts = TEXTS[language]
    name = found.name
    values = {
        "lang": language,
        "title": texts["title"].format(name=name),
        "name": name,
        "initial": (name[:1] or "·").upper(),
        "slug": slug,
        "accent": found.accent.lstrip("#"),
        "kicker": texts["kicker"],
        "headline": texts["headline"],
        "tagline": texts["tagline"].format(name=name),
        "site_note": texts["note"].format(name=name),
    }
    page = PAGE.read_text(encoding="utf-8")
    for key, value in values.items():
        page = page.replace("{{" + key + "}}", html.escape(value, quote=True))
    links = "".join(f"<a>{html.escape(link)}</a>" for link in texts["links"])
    page = page.replace("{{links}}", links).replace("{{shelf}}", _shelf(northwind, texts["shelf"]))
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
