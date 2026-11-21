"""Los productos de una tienda, leídos del HTML de sus páginas (prueba gratis, decisión 075).

Sin modelo: los datos estructurados que publican las plataformas de tiendas. Primero
`schema.org/Product` en JSON-LD (Shopify, Tiendanube, WooCommerce); si la página no tiene, las
etiquetas `og:` de producto. De cada uno: nombre, precio, moneda, imagen y link. El contenido de
la página es dato: solo se leen esos campos.
"""

import html
import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

MAX_NAME = 120


@dataclass(frozen=True)
class Product:
    name: str
    price: float
    currency: str
    image: str
    url: str


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.scripts: list[str] = []
        self.meta: dict[str, str] = {}
        self._in_ld = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "script" and (values.get("type") or "").lower() == "application/ld+json":
            self._in_ld = True
            self.scripts.append("")
        elif tag == "meta":
            key = (values.get("property") or values.get("name") or "").lower()
            if key and values.get("content"):
                self.meta.setdefault(key, values["content"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_ld = False

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self.scripts[-1] += data


def _nodes(value: Any) -> list[dict[str, Any]]:
    """Todos los objetos del JSON-LD, también los anidados (`@graph`, listas de productos)."""
    found: list[dict[str, Any]] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(reversed(item))
        elif isinstance(item, dict):
            found.append(item)
            for key in ("@graph", "itemListElement", "item", "hasVariant"):
                if key in item:
                    stack.append(item[key])
    return found


def _is_product(node: dict[str, Any]) -> bool:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return "Product" in kinds


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or value.get("name")
    if isinstance(value, list):
        value = value[0] if value else ""
        return _text(value)
    return html.unescape(str(value or "")).strip()


def _price(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^\d.,]", "", str(value or ""))
    if not text:
        return None
    if "," in text and "." in text:  # 12.500,00 o 12,500.00: el último separador es el decimal
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        text = text.replace("." if decimal == "," else ",", "").replace(",", ".")
    elif "," in text:
        whole, _, rest = text.rpartition(",")
        text = f"{whole.replace(',', '')}.{rest}" if len(rest) != 3 else text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def _offer(node: dict[str, Any]) -> tuple[float | None, str]:
    offers = node.get("offers")
    for offer in offers if isinstance(offers, list) else [offers]:
        if not isinstance(offer, dict):
            continue
        price = _price(offer.get("price") or offer.get("lowPrice"))
        spec = offer.get("priceSpecification")
        if price is None and isinstance(spec, dict):
            price = _price(spec.get("price"))
        if price is not None:
            return price, _text(offer.get("priceCurrency")).upper()[:3]
    return None, ""


def _make(
    name: str, price: float | None, currency: str, image: str, url: str, page: str
) -> Product | None:
    name = " ".join(name.split())[:MAX_NAME]
    if not name or price is None or price <= 0:
        return None
    return Product(
        name=name,
        price=round(price, 2),
        currency=currency or "USD",
        image=urljoin(page, image) if image else "",
        url=urljoin(page, url) if url else page,
    )


def extract(markup: str, page_url: str) -> list[Product]:
    """Los productos de una página (los repetidos, una vez)."""
    reader = _Reader()
    try:
        reader.feed(markup)
    except Exception:  # noqa: BLE001 — HTML roto: se lee lo que se pudo
        pass
    found: list[Product] = []
    for script in reader.scripts:
        try:
            data = json.loads(script.strip())
        except ValueError:
            continue
        for node in _nodes(data):
            if not _is_product(node):
                continue
            price, currency = _offer(node)
            product = _make(
                _text(node.get("name")),
                price,
                currency,
                _text(node.get("image")),
                _text(node.get("url")),
                page_url,
            )
            if product is not None:
                found.append(product)
    meta = reader.meta
    if not found and meta.get("og:type", "").lower().startswith("product"):
        product = _make(
            html.unescape(meta.get("og:title", "")),
            _price(meta.get("product:price:amount") or meta.get("og:price:amount")),
            (meta.get("product:price:currency") or meta.get("og:price:currency") or "").upper(),
            meta.get("og:image", ""),
            meta.get("og:url", ""),
            page_url,
        )
        if product is not None:
            found.append(product)
    return unique(found)


def site_name(markup: str) -> str:
    """El nombre de la tienda que declara la página (`og:site_name`), o vacío."""
    reader = _Reader()
    try:
        reader.feed(markup)
    except Exception:  # noqa: BLE001
        pass
    name = reader.meta.get("og:site_name") or reader.meta.get("application-name") or ""
    return " ".join(html.unescape(name).split())[:80]


def unique(products: list[Product], limit: int | None = None) -> list[Product]:
    seen: set[str] = set()
    kept: list[Product] = []
    for product in products:
        key = product.name.lower()
        if key not in seen:
            seen.add(key)
            kept.append(product)
    return kept[:limit] if limit else kept
