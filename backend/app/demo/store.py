"""El sitio de la tienda de la vista "detrás de escena" (decisión 042).

Arma el HTML del sitio que se ve a la izquierda de `/demo/{slug}`: aviso, header, portada, los
más vendidos con sus ilustraciones SVG propias (`static/store/`), la franja de confianza y el
footer. Northwind Outfitters tiene la tienda completa; otra empresa, solo header, portada y pie,
sin nada de lo que es propio de Northwind (envíos, garantía, catálogo).

Todo es decorativo: no hay formularios ni enlaces que lleven a otro lado, y nada se carga de
internet.
"""

import html
import json
from pathlib import Path
from typing import Any

from app.demo import northwind

STATIC = Path(__file__).resolve().parent / "static" / "store"
PRODUCTS = northwind.API_DIR / "data" / "products.json"

# Los más vendidos, en este orden: primero los de los pedidos del guion (NW-4521 campera,
# NW-4528 botas y gorro, NW-4533 sweater, NW-4540 pantalón), con el color de cada pedido.
SHELF: list[tuple[str, str, str | None]] = [  # (código, color que se muestra, etiqueta)
    ("TRJ", "Lake Blue", "Best seller"),
    ("CHB", "Walnut", None),
    ("MCS", "Navy", "Best seller"),
    ("CFP", "Olive", None),
    ("ADV", "Rust", "New"),
    ("WBN", "Heather Gray", None),
    ("OTE", "Sage", None),
    ("WCT", "Field Tan", "New"),
]
# El color de cada tela: la ilustración saca de acá sus luces y sombras (con color-mix).
COLORS = {
    "Lake Blue": "#4d7ea6",
    "Black": "#2c2f33",
    "Moss": "#6e7d4c",
    "Rust": "#b0613b",
    "Navy": "#2e3e5e",
    "Oat": "#d9ccb2",
    "Charcoal": "#4b4f55",
    "Olive": "#74734a",
    "Khaki": "#bfae86",
    "White": "#f2f0ea",
    "Sage": "#9db09a",
    "Walnut": "#7a4d32",
    "Field Tan": "#bf9a63",
    "Heather Gray": "#a7aaae",
}
TEXTS: dict[str, dict[str, Any]] = {
    "en": {
        "links": ("New arrivals", "Shop", "Collections", "About"),
        "search": "Search products…",
        "account": "Account",
        "cart": "Cart",
        "kicker": "New season",
        "headline": "What you need, when you need it.",
        "tagline": "The online store of {name}. Free returns.",
        "note": "Demo · {name} is a fictional store.",
    },
    "es": {
        "links": ("Novedades", "Abrigos", "Tejidos", "Calzado"),
        "search": "Buscar productos…",
        "account": "Cuenta",
        "cart": "Carrito",
        "kicker": "Colección de temporada",
        "headline": "Lo que buscás, cuando lo necesitás.",
        "tagline": "La tienda online de {name}. Cambios y devoluciones sin costo.",
        "note": "Demo · {name} es una tienda ficticia.",
    },
}
# Lo propio de Northwind (en inglés): no se muestra en otra empresa.
NORTHWIND: dict[str, Any] = {
    "announcement": "Free shipping on orders over $75 · Free returns within 30 days",
    "links": ("New arrivals", "Outerwear", "Knitwear", "Footwear", "Our story"),
    "search": "Search jackets, boots…",
    "kicker": "Fall / Winter 2026",
    "headline": "Gear that outlasts the weather.",
    "tagline": (
        "Durable outdoor clothing from Portland, Oregon. Made to be worn for decades, "
        "repaired when it tears, and handed down when you're done."
    ),
    "cta": "Shop the collection",
    "secondary": "Explore outerwear",
    "shelf_kicker": "Most loved this season",
    "shelf": "Best sellers",
    "shop_all": "Shop all",
    "colors": "{n} colors",
    "trust": (
        ("truck", "Free shipping", "On every order over $75"),
        ("return", "30-day returns", "Free and no questions asked"),
        ("shield", "Lifetime warranty", "We repair or replace it"),
        ("chat", "24/7 support", "Real answers, any time"),
    ),
    "about": (
        "Outdoor clothing built in Portland, Oregon, since 1998. "
        "Fewer, better things, made to last."
    ),
    "columns": (
        ("Shop", ("New arrivals", "Outerwear", "Knitwear", "Footwear", "Accessories")),
        ("Help", ("Order status", "Shipping", "Returns & exchanges", "Warranty & repairs")),
        ("Company", ("Our story", "Materials", "Stores", "Careers")),
    ),
    "legal": "© 2026 {name} · Portland, Oregon",
}

ICONS = {
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l4.5 4.5"/>',
    "account": (
        '<circle cx="12" cy="8.5" r="3.8"/><path d="M4.5 20.5c1.2-4 4-6 7.5-6s6.3 2 7.5 6"/>'
    ),
    "cart": '<path d="M5.5 8.5h13l-1 12h-11z"/><path d="M9 8.5V7a3 3 0 0 1 6 0v1.5"/>',
    "arrow": '<path d="M5 12h14M13 6l6 6-6 6"/>',
    "truck": (
        '<path d="M2.5 6.5h11v9h-11zM13.5 9.5h4l3 3.2v2.8h-7"/>'
        '<circle cx="7" cy="17" r="1.8"/><circle cx="17" cy="17" r="1.8"/>'
    ),
    "return": '<path d="M9 7L5 11l4 4"/><path d="M5 11h9.5a4.5 4.5 0 0 1 0 9H11"/>',
    "shield": (
        '<path d="M12 3l7 3v5.5c0 4.3-3 7.8-7 9.5-4-1.7-7-5.2-7-9.5V6z"/>'
        '<path d="M8.8 12l2.2 2.2 4.2-4.4"/>'
    ),
    "chat": (
        '<path d="M4.5 6.5a2 2 0 0 1 2-2h11a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H11'
        'l-4 3.2V16.5h-.5a2 2 0 0 1-2-2z"/>'
        '<path d="M8.5 10.5h7"/>'
    ),
}
# La marca de Northwind: una aguja de brújula que apunta al norte, dentro de un círculo.
MARK = (
    '<svg class="mark" viewBox="0 0 32 32" aria-hidden="true">'
    '<circle cx="16" cy="16" r="14.5" fill="none" stroke="currentColor" stroke-width="1.6"/>'
    '<path d="M16 5.5l4.2 10.5H11.8z" fill="currentColor"/>'
    '<path d="M16 26.5l-4.2-10.5h8.4z" fill="currentColor" opacity=".38"/></svg>'
)


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def _icon(name: str, size: int = 22) -> str:
    return (
        f'<svg class="ic" viewBox="0 0 24 24" width="{size}" height="{size}" aria-hidden="true">'
        f"{ICONS[name]}</svg>"
    )


def _art(code: str) -> str:
    return (STATIC / f"{code}.svg").read_text(encoding="utf-8").strip()


def _logo(name: str, is_northwind: bool) -> str:
    first, _, rest = name.partition(" ")
    if is_northwind:
        mark = MARK
    else:
        mark = f'<span class="initial" aria-hidden="true">{_e((name[:1] or "·").upper())}</span>'
    words = f"<b>{_e(first)}</b>" + (f" <span>{_e(rest)}</span>" if rest else "")
    return f'<span class="logo">{mark}<span class="word">{words}</span></span>'


def _header(name: str, texts: dict[str, Any], is_northwind: bool) -> str:
    links = "".join(f"<a>{_e(link)}</a>" for link in texts["links"])
    bag = '<i class="count" aria-hidden="true">2</i>' if is_northwind else ""
    return (
        '<header class="top"><div class="bar">'
        f"{_logo(name, is_northwind)}"
        f'<nav class="links" aria-label="Main">{links}</nav>'
        f'<span class="search" role="search">{_icon("search", 18)}'
        f"<span>{_e(texts['search'])}</span></span>"
        f'<span class="icon" role="img" aria-label="{_e(texts["account"])}">'
        f"{_icon('account')}</span>"
        f'<span class="icon" role="img" aria-label="{_e(texts["cart"])}">'
        f"{_icon('cart')}{bag}</span>"
        "</div></header>"
    )


def _hero(name: str, texts: dict[str, Any], is_northwind: bool) -> str:
    if not is_northwind:
        return (
            '<section class="hero plain"><div class="wrap"><div class="copy">'
            f'<small class="kick">{_e(texts["kicker"])}</small><h1>{_e(texts["headline"])}</h1>'
            f"<p>{_e(texts['tagline'].format(name=name))}</p></div></div></section>"
        )
    return (
        f'<section class="hero">{_art("hero")}<div class="wrap"><div class="copy">'
        f'<small class="kick">{_e(texts["kicker"])}</small><h1>{_e(texts["headline"])}</h1>'
        f"<p>{_e(NORTHWIND['tagline'])}</p>"
        f'<div class="ctas"><span class="cta">{_e(NORTHWIND["cta"])}{_icon("arrow", 18)}</span>'
        f'<span class="more">{_e(NORTHWIND["secondary"])}</span></div>'
        "</div></div></section>"
    )


def _card(product: dict[str, Any], color: str, badge: str | None) -> str:
    dots = "".join(
        f'<button type="button" class="dot{" on" if c == color else ""}" '
        f'style="--s:{COLORS.get(c, "#9aa0a6")}" aria-label="{_e(c)}" '
        f'aria-pressed="{"true" if c == color else "false"}"></button>'
        for c in product["colors"]
    )
    tag = ""
    if badge:
        kind = "new" if badge == "New" else "best"
        tag = f'<span class="badge {kind}">{_e(badge)}</span>'
    count = NORTHWIND["colors"].format(n=len(product["colors"]))
    return (
        f'<article class="product" data-name="{_e(product["name"])}" data-color="{_e(color)}" '
        f'style="--p:{COLORS.get(color, "#9aa0a6")}">'
        f'<div class="tile">{tag}{_art(product["code"])}</div>'
        f'<div class="line"><h3>{_e(product["name"])}</h3><b>${product["price"]:,.0f}</b></div>'
        f'<div class="line sub"><span class="shade">{_e(color)}</span>'
        f'<span class="dots" role="group" aria-label="{_e(count)}">{dots}</span></div>'
        "</article>"
    )


def _shelf() -> str:
    by_code = {p["code"]: p for p in json.loads(PRODUCTS.read_text(encoding="utf-8"))}
    cards = "".join(_card(by_code[code], color, badge) for code, color, badge in SHELF)
    return (
        '<section class="shelf" id="best-sellers"><div class="wrap">'
        f'<div class="head-row"><div><small class="kick">{_e(NORTHWIND["shelf_kicker"])}</small>'
        f"<h2>{_e(NORTHWIND['shelf'])}</h2></div>"
        f'<span class="more">{_e(NORTHWIND["shop_all"])}{_icon("arrow", 16)}</span></div>'
        f'<div class="grid">{cards}</div></div></section>'
    )


def _trust() -> str:
    items = "".join(
        f'<div class="perk">{_icon(icon, 24)}'
        f"<div><b>{_e(title)}</b><span>{_e(text)}</span></div></div>"
        for icon, title, text in NORTHWIND["trust"]
    )
    return (
        '<section class="trust" id="perks"><div class="wrap">'
        f'<div class="perks">{items}</div></div></section>'
    )


def _footer(name: str, texts: dict[str, Any], is_northwind: bool) -> str:
    note = f'<p class="note">{_e(texts["note"].format(name=name))}</p>'
    if not is_northwind:
        return f'<footer class="foot-site"><div class="wrap">{note}</div></footer>'
    columns = "".join(
        f"<div><h4>{_e(title)}</h4>" + "".join(f"<a>{_e(link)}</a>" for link in links) + "</div>"
        for title, links in NORTHWIND["columns"]
    )
    return (
        '<footer class="foot-site" id="footer"><div class="wrap"><div class="cols">'
        f'<div class="about">{_logo(name, True)}<p>{_e(NORTHWIND["about"])}</p></div>'
        f"{columns}</div>"
        f'<div class="legal"><span>{_e(NORTHWIND["legal"].format(name=name))}</span>{note}</div>'
        "</div></footer>"
    )


def render(name: str, language: str, is_northwind: bool) -> str:
    """El sitio de la tienda (lo que va dentro de `.store`)."""
    texts = dict(TEXTS[language])
    if is_northwind:
        texts.update({k: NORTHWIND[k] for k in ("links", "search", "kicker", "headline")})
    parts = []
    if is_northwind:
        parts.append(f'<div class="announce">{_e(NORTHWIND["announcement"])}</div>')
    parts.append(_header(name, texts, is_northwind))
    parts.append("<main>")
    parts.append(_hero(name, texts, is_northwind))
    if is_northwind:
        parts.append(_shelf())
        parts.append(_trust())
    parts.append("</main>")
    parts.append(_footer(name, texts, is_northwind))
    return "".join(parts)
