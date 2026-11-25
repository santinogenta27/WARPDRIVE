"""Los productos de una tienda (prueba gratis, decisiones 075 y 101).

Sin modelo: los datos que publican las plataformas. De una página, JSON-LD (`Product`,
`ProductGroup` con `hasVariant`, `Offer`, `AggregateOffer`), después microdata y por último Open
Graph de producto. Las fuentes de catálogo (`sources`) arman los mismos `Product`.

Un producto es UN producto, no sus variantes: los talles y colores se agrupan con su rango de
precios. El nombre queda limpio (sin talle ni color pegados) y precio, imagen y link son siempre los
de un mismo producto real, así coinciden con su página. El contenido de la página es dato: solo se
leen estos campos.
"""

import html
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlsplit

MAX_NAME = 120
MAX_DESCRIPTION = 240
MAX_VARIANTS = 12
SHORT_WORDS = 3
SHORT_CHARS = 24

GIFT_CARD = re.compile(
    r"gift\s*card|gift\s*certificate|e-?gift|tarjeta\s+de\s+regalo|giftcard|voucher|"
    r"vale\s+de\s+regalo|gift\s*voucher|carte\s+cadeau",
    re.IGNORECASE,
)
TEST_PRODUCT = re.compile(
    r"^(test|testing|prueba|sample|dummy|do not (buy|purchase)|no comprar)\b|\b(test product|"
    r"producto de prueba|placeholder)\b",
    re.IGNORECASE,
)
# "… - Size 8", ", Talle 40", " / M", "(Size 8.5)": el talle pegado al nombre.
_SIZE_SUFFIX = re.compile(
    r"\s*(?:[-–—/,|]\s*|\(\s*)(?:size|talle|talla|taille|größe|tam(?:año)?\.?)\s*[:#]?\s*"
    r"[\w./-]{1,8}\s*\)?\s*$",
    re.IGNORECASE,
)
_VARIANT_SEPARATOR = re.compile(r"\s+[-–—|]\s+")
_THOUSANDS_DOT = re.compile(r"^\d{1,3}(\.\d{3})+$")
_THOUSANDS_COMMA = re.compile(r"^\d{1,3}(,\d{3})+$")


@dataclass(frozen=True)
class Product:
    name: str
    price: float
    currency: str
    image: str
    url: str
    category: str = ""
    description: str = ""
    compare_at: float | None = None  # el precio antes de la oferta (mayor que `price`)
    available: bool | None = None  # None: la fuente no lo dice
    variants: tuple[str, ...] = ()
    price_max: float | None = None  # con variantes de distinto precio
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "short": short_name(self.name),
            "price": self.price,
            "price_max": self.price_max,
            "compare_at": self.compare_at,
            "currency": self.currency,
            "image": self.image,
            "url": self.url,
            "category": self.category,
            "description": self.description,
            "available": self.available,
            "variants": list(self.variants),
            "source": self.source,
        }


# -- texto y precios -----------------------------------------------------------------------------
def clean_text(value: Any, limit: int = MAX_NAME) -> str:
    """Sin etiquetas ni entidades HTML, en una línea."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", str(value or "")))
    return " ".join(text.split())[:limit].strip()


def short_description(value: Any) -> str:
    text = clean_text(value, 2000)
    if len(text) <= MAX_DESCRIPTION:
        return text
    cut = text[:MAX_DESCRIPTION]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[: end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…"


def parse_price(value: Any) -> float | None:
    """Un precio en cualquier formato: 12.5, "12,50", "1.234,56", "1,234.56", "$ 8.900", "12,50 €".
    Un solo separador seguido de exactamente tres cifras es de miles (nadie cobra milésimos)."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value) if value >= 0 else None
    text = re.sub(r"[^\d.,]", "", str(value or ""))
    text = text.strip(".,")
    if not text or not re.search(r"\d", text):
        return None
    if "," in text and "." in text:  # el último separador es el decimal
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        text = text.replace(thousands, "").replace(decimal, ".")
    elif _THOUSANDS_DOT.match(text):
        text = text.replace(".", "")
    elif _THOUSANDS_COMMA.match(text):
        text = text.replace(",", "")
    elif "," in text:
        whole, _, rest = text.rpartition(",")
        text = f"{whole.replace(',', '')}.{rest}"
    elif text.count(".") > 1:
        whole, _, rest = text.rpartition(".")
        text = f"{whole.replace('.', '')}.{rest}"
    try:
        return float(text)
    except ValueError:
        return None


def clean_name(name: str) -> str:
    """El nombre sin el talle pegado ("Runner - Size 8" → "Runner")."""
    text = clean_text(name)
    for _ in range(2):
        stripped = _SIZE_SUFFIX.sub("", text).strip(" -–—/,|")
        if stripped == text or not stripped:
            break
        text = stripped
    return text


def base_name(name: str) -> str:
    """El nombre antes del último " - ": "Tree Runner - Mushroom (White Sole)" → "Tree Runner"."""
    parts = _VARIANT_SEPARATOR.split(name)
    return " - ".join(parts[:-1]) if len(parts) > 1 else name


def variant_name(name: str) -> str:
    parts = _VARIANT_SEPARATOR.split(name)
    return parts[-1] if len(parts) > 1 else ""


def short_name(name: str) -> str:
    """Para las sugerencias: "Men's Runner NZ Slip On - Mushroom" → "Men's Runner NZ"."""
    words = base_name(clean_name(name)).replace("®", "").replace("™", "").split()
    kept: list[str] = []
    for word in words:
        if len(kept) >= SHORT_WORDS or (kept and len(" ".join([*kept, word])) > SHORT_CHARS):
            break
        kept.append(word)
    # Sin una cola que no dice nada: "Estampita Agustina x" → "Estampita Agustina".
    while len(kept) > 1 and (len(kept[-1].strip(".,")) <= 1 or kept[-1].lower() in _FILLERS):
        kept.pop()
    return " ".join(kept).strip(" -,:") or name[:SHORT_CHARS]


_FILLERS = {
    "x",
    "de",
    "del",
    "la",
    "el",
    "con",
    "para",
    "y",
    "and",
    "with",
    "for",
    "the",
    "of",
    "-",
}


def discard_reason(product: Product) -> str | None:
    """Por qué un producto no se muestra (None: se muestra)."""
    if not product.name:
        return "no_name"
    if product.price is None or product.price <= 0:
        return "no_price"
    if GIFT_CARD.search(product.name) or GIFT_CARD.search(product.category):
        return "gift_card"
    if TEST_PRODUCT.search(product.name):
        return "test"
    return None


def make(
    name: Any,
    price: float | None,
    currency: str,
    image: Any,
    url: Any,
    page: str,
    **extra: Any,
) -> Product | None:
    """Un producto válido o None (sin nombre o sin precio no es un producto). El nombre se limpia
    de talles al agrupar (`select`): dos nombres iguales tal como vienen son un duplicado."""
    title = clean_text(name)
    if not title or price is None or price <= 0:
        return None
    image_url = _absolute(page, _text(image))
    link = _absolute(page, _text(url)) or page
    compare_at = extra.pop("compare_at", None)
    return Product(
        name=title,
        price=round(price, 2),
        currency=(currency or "").upper()[:3] or "USD",
        image=image_url,
        url=link,
        compare_at=round(compare_at, 2) if compare_at and compare_at > price else None,
        **extra,
    )


def _absolute(page: str, value: str) -> str:
    if not value:
        return ""
    if value.startswith("//"):
        value = "https:" + value
    found = urljoin(page, value)
    return found if urlsplit(found).scheme in ("http", "https") else ""


# -- agrupar y filtrar ---------------------------------------------------------------------------
def group(products: Iterable[Product]) -> list[Product]:
    """Agrupa las variantes: los productos con el mismo nombre (sin talle) y los que comparten el
    nombre antes del último " - " y la categoría (un producto por color: "Mug" y "Hoodie" de la
    misma marca no se juntan). El representante es el más barato con stock: su precio, su imagen y
    su link."""
    items = [replace(p, name=clean_name(p.name)) for p in products]

    def family(p: Product) -> str:
        return f"{base_name(p.name).lower()}\n{p.category.lower()}"

    bases: dict[str, int] = {}
    for p in items:
        bases[family(p)] = bases.get(family(p), 0) + 1
    groups: dict[str, list[Product]] = {}
    order: list[str] = []
    for p in items:
        shared = bases[family(p)] > 1 and base_name(p.name) != p.name
        key = family(p) if shared else p.name.lower()
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(p)
    return [_merge(groups[key]) for key in order]


def _merge(members: list[Product]) -> Product:
    if len(members) == 1:
        return members[0]
    stocked = [p for p in members if p.available is not False] or members
    lead = min(stocked, key=lambda p: p.price)
    colors = [variant_name(p.name) for p in members if variant_name(p.name)]
    variants = list(dict.fromkeys([*colors, *(v for p in members for v in p.variants)]))
    prices = [p.price for p in members] + [p.price_max for p in members if p.price_max]
    availability = [p.available for p in members if p.available is not None]
    return replace(
        lead,
        name=base_name(lead.name) if colors else lead.name,
        variants=tuple(variants[:MAX_VARIANTS]),
        price_max=max(prices) if max(prices) > lead.price else None,
        available=any(availability) if availability else None,
    )


def unique(products: Iterable[Product], limit: int | None = None) -> list[Product]:
    seen: set[str] = set()
    kept: list[Product] = []
    for product in products:
        key = product.name.lower()
        if key not in seen:
            seen.add(key)
            kept.append(product)
    return kept[:limit] if limit else kept


def select(
    products: Iterable[Product], limit: int, discarded: dict[str, int] | None = None
) -> list[Product]:
    """Descarta duplicados (el mismo nombre tal como viene), agrupa variantes, descarta lo que no
    es un producto (contando por qué) y deja hasta `limit`."""
    counts = discarded if discarded is not None else {}
    raw: set[str] = set()
    distinct: list[Product] = []
    for product in products:
        key = product.name.lower()
        if key in raw:
            counts["duplicate"] = counts.get("duplicate", 0) + 1
            continue
        raw.add(key)
        distinct.append(product)
    kept: list[Product] = []
    seen: set[str] = set()
    for product in group(distinct):
        reason = discard_reason(product)
        if reason is None and product.name.lower() in seen:
            reason = "duplicate"
        if reason is not None:
            counts[reason] = counts.get(reason, 0) + 1
            continue
        seen.add(product.name.lower())
        kept.append(product)
    return kept[:limit]


# -- leer una página -----------------------------------------------------------------------------
class _Reader(HTMLParser):
    """JSON-LD, etiquetas meta y microdata de `schema.org/Product`."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.scripts: list[str] = []
        self.meta: dict[str, str] = {}
        self.items: list[dict[str, str]] = []  # microdata: un dict por Product
        self._in_ld = False
        # Las etiquetas abiertas y, si abren un `itemscope`, el producto al que suman propiedades
        # (las de una oferta anidada van al mismo producto).
        self._open: list[tuple[str, dict[str, str] | None]] = []
        self._prop: tuple[str, dict[str, str]] | None = None

    VOID = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "col", "wbr"}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k: v or "" for k, v in attrs}
        self._prop = None
        if tag == "script" and values.get("type", "").lower() == "application/ld+json":
            self._in_ld = True
            self.scripts.append("")
            return
        item = self._current()
        if tag == "meta" and item is None:
            key = (values.get("property") or values.get("name") or "").lower()
            if key and values.get("content"):
                self.meta.setdefault(key, values["content"])
        prop = values.get("itemprop", "").split()[0] if values.get("itemprop") else ""
        if item is not None and prop and "itemscope" not in values:
            content = values.get("content") or values.get("href") or values.get("src")
            if content:
                item.setdefault(prop, content)
            elif tag not in self.VOID:
                self._prop = (prop, item)
        scope: dict[str, str] | None = None
        if "itemscope" in values:
            if re.search(r"schema\.org/Product\b", values.get("itemtype", "")):
                scope = {}
                self.items.append(scope)
            else:
                scope = item
        if tag not in self.VOID:
            self._open.append((tag, scope))

    def _current(self) -> dict[str, str] | None:
        for _, item in reversed(self._open):
            if item is not None:
                return item
        return None

    def handle_endtag(self, tag: str) -> None:
        if tag == "script":
            self._in_ld = False
        for n in range(len(self._open) - 1, -1, -1):  # tolera etiquetas sin cerrar
            if self._open[n][0] == tag:
                del self._open[n:]
                break
        self._prop = None

    def handle_data(self, data: str) -> None:
        if self._in_ld:
            self.scripts[-1] += data
        elif self._prop is not None and data.strip():
            name, item = self._prop
            item.setdefault(name, data.strip())


def _read(markup: str) -> _Reader:
    reader = _Reader()
    try:
        reader.feed(markup)
        reader.close()
    except Exception:  # noqa: BLE001 — HTML roto: se lee lo que se pudo
        pass
    return reader


def _nodes(value: Any) -> list[dict[str, Any]]:
    """Todos los objetos del JSON-LD, también los anidados (`@graph`, listas de productos). Las
    variantes de un `ProductGroup` NO se devuelven sueltas: las lee el grupo."""
    found: list[dict[str, Any]] = []
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(reversed(item))
        elif isinstance(item, dict):
            found.append(item)
            for key in ("@graph", "itemListElement", "item", "mainEntity"):
                if key in item:
                    stack.append(item[key])
    return found


def _types(node: dict[str, Any]) -> set[str]:
    kind = node.get("@type")
    kinds = kind if isinstance(kind, list) else [kind]
    return {str(k).rsplit("/", 1)[-1] for k in kinds if k}


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("url") or value.get("contentUrl") or value.get("@id") or value.get("name")
    if isinstance(value, list):
        return _text(value[0]) if value else ""
    return html.unescape(str(value or "")).strip()


def _availability(value: Any) -> bool | None:
    text = str(value or "").lower()
    if not text:
        return None
    if "instock" in text or "limitedavailability" in text or "preorder" in text:
        return True
    if "outofstock" in text or "soldout" in text or "discontinued" in text:
        return False
    return None


def _offers(node: dict[str, Any]) -> list[dict[str, Any]]:
    offers = node.get("offers")
    found = []
    for offer in offers if isinstance(offers, list) else [offers]:
        if isinstance(offer, dict):
            found.append(offer)
            if isinstance(offer.get("offers"), list):  # AggregateOffer con sus ofertas
                found.extend(o for o in offer["offers"] if isinstance(o, dict))
    return found


def _offer(
    node: dict[str, Any],
) -> tuple[float | None, float | None, str, bool | None, float | None]:
    """(precio, precio máximo, moneda, stock, precio antes de la oferta)."""
    prices: list[float] = []
    currency = ""
    stock: list[bool] = []
    before: float | None = None
    for offer in _offers(node):
        price = parse_price(offer.get("price"))
        if price is None:
            price = parse_price(offer.get("lowPrice"))
        high = parse_price(offer.get("highPrice"))
        spec = offer.get("priceSpecification")
        specs = spec if isinstance(spec, list) else [spec]
        for s in specs:
            if not isinstance(s, dict):
                continue
            value = parse_price(s.get("price"))
            if value is None:
                continue
            if (
                "strikethrough" in str(s.get("priceType", "")).lower()
                or "list" in str(s.get("priceType", "")).lower()
            ):
                before = value
            elif price is None:
                price = value
            currency = currency or _text(s.get("priceCurrency"))
        if price is not None and price > 0:
            prices.append(price)
            if high and high > price:
                prices.append(high)
            currency = currency or _text(offer.get("priceCurrency"))
        available = _availability(offer.get("availability"))
        if available is not None:
            stock.append(available)
    if not prices:
        return None, None, currency, None, None
    low, high_ = min(prices), max(prices)
    return low, high_ if high_ > low else None, currency, (any(stock) if stock else None), before


def _category(node: dict[str, Any]) -> str:
    value = node.get("category")
    if isinstance(value, dict):
        value = value.get("name")
    if isinstance(value, list):
        value = value[-1] if value else ""
    return clean_text(str(value or "").split(">")[-1], 60)


def _variant_label(variant: dict[str, Any], group_name: str) -> str:
    for key in ("size", "color"):
        if variant.get(key):
            return clean_text(_text(variant[key]), 40)
    name = clean_text(variant.get("name"))
    if group_name and name.lower().startswith(group_name.lower()):
        name = name[len(group_name) :].strip(" -–—/,|")
    return name[:40]


def _from_node(node: dict[str, Any], page: str) -> Product | None:
    kinds = _types(node)
    name = _text(node.get("name"))
    image = node.get("image")
    url = _text(node.get("url")) or _text(node.get("mainEntityOfPage"))
    if "ProductGroup" in kinds or isinstance(node.get("hasVariant"), list):
        variants = [v for v in node.get("hasVariant") or [] if isinstance(v, dict)]
        offers = [_offer(v) for v in variants]
        priced = [(o, v) for o, v in zip(offers, variants, strict=True) if o[0] is not None]
        own = _offer(node)
        if not priced and own[0] is None:
            return None
        stocked = [(o, v) for o, v in priced if o[3] is not False] or priced
        if stocked:
            (low, _, currency, _, before), lead = min(stocked, key=lambda x: x[0][0] or 0.0)
        else:
            low, _, currency, _, before = own
            lead = {}
        prices = [o[0] for o, _ in priced if o[0] is not None] + [
            o[1] for o, _ in priced if o[1] is not None
        ]
        high = max(prices) if prices else None
        availability = [o[3] for o, _ in priced if o[3] is not None]
        labels = list(dict.fromkeys(_variant_label(v, clean_text(name)) for _, v in priced))
        return make(
            name,
            low,
            currency,
            image or lead.get("image"),
            url or _text(lead.get("url")),
            page,
            category=_category(node),
            description=short_description(node.get("description")),
            compare_at=before,
            available=any(availability) if availability else None,
            variants=tuple(label for label in labels if label)[:MAX_VARIANTS],
            price_max=high if high and low and high > low else None,
            source="structured_data",
        )
    price, high, currency, available, before = _offer(node)
    return make(
        name,
        price,
        currency,
        image,
        url,
        page,
        category=_category(node),
        description=short_description(node.get("description")),
        compare_at=before,
        available=available,
        price_max=high,
        source="structured_data",
    )


def extract(markup: str, page_url: str) -> list[Product]:
    """Los productos de una página (los repetidos, una vez). JSON-LD; si no hay, microdata; si no,
    Open Graph de producto."""
    reader = _read(markup)
    found: list[Product] = []
    for script in reader.scripts:
        try:
            data = json.loads(script.strip())
        except ValueError:
            continue
        for node in _nodes(data):
            kinds = _types(node)
            if not kinds & {"Product", "ProductGroup", "IndividualProduct", "ProductModel"}:
                continue
            product = _from_node(node, page_url)
            if product is not None:
                found.append(product)
    if not found:
        for item in reader.items:
            product = make(
                item.get("name"),
                parse_price(item.get("price") or item.get("lowPrice")),
                item.get("priceCurrency", ""),
                item.get("image"),
                item.get("url"),
                page_url,
                description=short_description(item.get("description")),
                available=_availability(item.get("availability")),
                source="structured_data",
            )
            if product is not None:
                found.append(product)
    meta = reader.meta
    if not found and "product" in meta.get("og:type", "").lower():
        product = make(
            meta.get("og:title", ""),
            parse_price(
                meta.get("product:price:amount")
                or meta.get("og:price:amount")
                or meta.get("product:sale_price:amount")
            ),
            (meta.get("product:price:currency") or meta.get("og:price:currency") or ""),
            meta.get("og:image", ""),
            meta.get("og:url", ""),
            page_url,
            description=short_description(meta.get("og:description")),
            available=_availability(
                meta.get("product:availability") or meta.get("og:availability")
            ),
            source="structured_data",
        )
        if product is not None:
            found.append(product)
    return unique(found)


def main_product(markup: str, page_url: str) -> Product | None:
    """El producto de una página de producto (no los relacionados que muestra al pie)."""
    found = extract(markup, page_url)
    path = urlsplit(page_url).path.rstrip("/")
    for product in found:
        if urlsplit(product.url).path.rstrip("/") == path:
            return product
    return found[0] if found else None


def site_name(markup: str) -> str:
    """El nombre de la tienda que declara la página (`og:site_name`, el de la organización del
    JSON-LD o `application-name`), o vacío."""
    reader = _read(markup)
    name = reader.meta.get("og:site_name") or reader.meta.get("application-name") or ""
    if not name:
        for script in reader.scripts:
            try:
                data = json.loads(script.strip())
            except ValueError:
                continue
            for node in _nodes(data):
                if _types(node) & {"Organization", "Store", "OnlineStore", "WebSite"}:
                    name = _text(node.get("name"))
                    if name:
                        break
            if name:
                break
    return " ".join(html.unescape(name).split())[:80]
