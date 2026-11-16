"""Test de aceptación del rediseño de la tienda de `/demo/northwind-outfitters` (decisión 042).

El sitio de la izquierda de la vista "detrás de escena" tiene que parecer el de una marca outdoor
real para el video, sin tocar el widget ni el panel:

- cada producto con su ilustración SVG propia, en su color, sin fotos ni recursos externos, y
  los productos de los pedidos del guion (NW-4521, NW-4528, NW-4533 y NW-4540);
- estructura de tienda: aviso, header con logo, navegación, buscador, cuenta y carrito, portada
  con "Shop the collection", ocho más vendidos con precio, colores y etiquetas, franja de
  confianza y footer con columnas y el aviso de tienda ficticia;
- los tokens del widget, tema claro y oscuro (también forzable con `?theme=`) y movimiento sutil
  que respeta `prefers-reduced-motion`;
- el chat y el panel siguen donde estaban, y la página sigue sin formularios.

Es HTML generado sin base: se prueba la función que arma la página.
"""

import json
import re

from app.config import REPO_ROOT
from app.demo import behind_routes

SLUG = "northwind-outfitters"
SCRIPT_ORDERS = ("NW-4521", "NW-4528", "NW-4533", "NW-4540")
API_DATA = REPO_ROOT / "mocks" / "northwind-api" / "data"
WIDGET_JS = REPO_ROOT / "backend" / "app" / "channels" / "web" / "static" / "widget.js"


def _page(northwind: bool = True, language: str = "en", name: str = "Northwind Outfitters") -> str:
    return behind_routes.render(
        name=name, slug=SLUG, accent="#2f5d50", language=language, northwind=northwind
    )


def _cards(page: str) -> list[str]:
    return re.findall(r'<article class="product".*?</article>', page, flags=re.S)


def _products() -> list[dict[str, object]]:
    found: list[dict[str, object]] = json.loads(
        (API_DATA / "products.json").read_text(encoding="utf-8")
    )
    return found


# -- 1. productos con ilustración propia -----------------------------------------------------
def test_cada_producto_tiene_su_ilustracion_svg_y_nada_externo() -> None:
    page = _page()
    cards = _cards(page)
    assert len(cards) == 8
    codes = set()
    for card in cards:
        art = re.search(r'<svg class="art"[^>]*data-art="([A-Z]{3})"', card)
        assert art, card[:200]
        codes.add(art.group(1))
    assert codes == {str(p["code"]) for p in _products()}  # una ilustración distinta por producto
    assert "<img" not in page and "swatch" not in page  # ni fotos ni los cuadrados de color
    assert not re.search(r'(src|href)="(https?:)?//', page)  # nada de internet
    assert "url(http" not in page and "@import" not in page


def test_estan_los_productos_de_los_pedidos_del_guion_en_su_color() -> None:
    orders = json.loads((API_DATA / "orders.json").read_text(encoding="utf-8"))
    cards = {
        re.search(r'data-name="([^"]+)"', c).group(1): c  # type: ignore[union-attr]
        for c in _cards(_page())
    }
    for order in (o for o in orders if o["id"] in SCRIPT_ORDERS):
        for item in order["items"]:
            card = cards[item["name"]]
            assert f'data-color="{item["color"]}"' in card, (order["id"], item["name"])


def test_cada_producto_con_precio_colores_y_algunas_etiquetas() -> None:
    by_name = {str(p["name"]): p for p in _products()}
    badges = []
    for card in _cards(_page()):
        product = by_name[re.search(r'data-name="([^"]+)"', card).group(1)]  # type: ignore[union-attr]
        assert f"${product['price']:,.0f}" in card
        colors = re.findall(r'class="dot(?: on)?"[^>]*aria-label="([^"]+)"', card)
        assert colors == product["colors"]
        badges += re.findall(r'<span class="badge[^"]*">([^<]+)</span>', card)
    assert "New" in badges and "Best seller" in badges
    assert 0 < len(badges) < 8  # en algunos, no en todos


# -- 2. estructura de tienda real ------------------------------------------------------------
def test_estructura_de_tienda() -> None:
    page = _page()
    assert "Free shipping on orders over $75 · Free returns within 30 days" in page
    header = re.search(r'<header class="top".*?</header>', page, flags=re.S)
    assert header, "falta el header"
    for part in (
        'class="logo"',
        'class="links"',
        'class="search"',
        'aria-label="Account"',
        'aria-label="Cart"',
    ):
        assert part in header.group(0), part
    hero = re.search(r'<section class="hero".*?</section>', page, flags=re.S)
    assert hero and "<h1>" in hero.group(0) and "Shop the collection" in hero.group(0)
    assert "<svg" in hero.group(0)  # la ilustración grande
    assert "Best sellers" in page
    trust = re.search(r'<section class="trust".*?</section>', page, flags=re.S)
    assert trust
    for item in ("Free shipping", "30-day returns", "Lifetime warranty", "24/7 support"):
        assert item in trust.group(0), item
    footer = re.search(r'<footer class="foot-site".*?</footer>', page, flags=re.S)
    assert footer
    for column in (">Shop<", ">Help<", ">Company<"):
        assert column in footer.group(0), column
    assert "Demo · Northwind Outfitters is a fictional store" in footer.group(0)


def test_otra_empresa_no_hereda_lo_de_northwind() -> None:
    page = _page(northwind=False, language="es", name="Acme")
    assert '<html lang="es">' in page and "Acme" in page
    assert "Demo · Acme es una tienda ficticia" in page
    for northwind_only in ("Free shipping", "Best sellers", "Lifetime warranty", "Portland"):
        assert northwind_only not in page, northwind_only
    assert _cards(page) == []


# -- 3. diseño: tokens del widget, temas y movimiento ----------------------------------------
def _tokens(css: str) -> dict[str, str]:
    return {k: v.strip() for k, v in re.findall(r"(--[a-z-]+)\s*:\s*([^;}]+)", css)}


def test_usa_los_tokens_del_widget_en_claro_y_oscuro() -> None:
    page = _page()
    widget = WIDGET_JS.read_text(encoding="utf-8")
    light = _tokens(re.search(r"\.root\{(.*?)\}", widget, flags=re.S).group(1))  # type: ignore[union-attr]
    dark = _tokens(re.search(r"\.root\[data-theme=dark\]\{(.*?)\}", widget, flags=re.S).group(1))  # type: ignore[union-attr]
    page_light = _tokens(re.search(r":root\s*\{(.*?)\}", page, flags=re.S).group(1))  # type: ignore[union-attr]
    page_dark = _tokens(
        re.search(r':root\[data-theme="dark"\]\s*\{(.*?)\}', page, flags=re.S).group(1)  # type: ignore[union-attr]
    )
    for name in ("--surface", "--ink", "--muted", "--line", "--rim", "--rim-low"):
        assert page_light[name].replace(" ", "") == light[name].replace(" ", ""), name
        assert page_dark[name].replace(" ", "") == dark[name].replace(" ", ""), name
    # el tema sigue al sistema y se puede forzar con ?theme=light|dark (para grabar el video)
    assert "prefers-color-scheme: dark" in page and "theme" in page and "URLSearchParams" in page


def test_movimiento_sutil_al_pasar_el_mouse_que_respeta_movimiento_reducido() -> None:
    page = _page()
    assert re.search(r"\.product:hover[^{]*\{", page)
    assert re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{[^@]*\.product", page)


# -- 4. el chat y el panel no cambian de lugar -----------------------------------------------
def test_el_chat_y_el_panel_siguen_igual() -> None:
    page = _page()
    assert '<html lang="en">' in page
    assert f'data-chat="{SLUG}"' in page and 'data-mode="inline"' in page
    assert 'class="chat" id="chat"' in page  # abajo a la derecha, sobre el sitio
    assert 'id="behind"' in page and "Behind the scenes" in page  # el panel a la derecha
    assert "<form" not in page  # solo lectura: el buscador es decorativo
    # el sitio se desplaza por detrás del chat: el chat no está dentro de lo que se desplaza
    store = re.search(r'<div class="store".*?<!-- /store -->', page, flags=re.S)
    assert store and 'id="chat"' not in store.group(0)
