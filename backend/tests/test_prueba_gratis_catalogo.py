"""Prueba gratis: el catálogo de cualquier tienda (decisión 101). Sin base de datos.

Visto con https://www.allbirds.com: la prueba encontró un solo producto, tomó cada talle como un
producto distinto ("… - Size 8", "… - Size 8.5") y el agente dijo que la tienda vendía solo eso.
Ahora: las fuentes públicas de cada plataforma, en orden (Shopify, WooCommerce, Squarespace,
sitemap con datos estructurados y, como último recurso, el modelo con validación literal), con las
variantes agrupadas en un producto, sin tarjetas de regalo ni duplicados, y hasta 150 productos.
"""

import gzip
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.trial import catalog, service, sources

SITE = "https://tienda.example"
Handler = Callable[[httpx.Request], httpx.Response]


def _client(routes: dict[str, Any], seen: list[str] | None = None) -> httpx.Client:
    """Un sitio simulado: ruta (con su query, si la tiene) → respuesta, texto o JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        full = path + (f"?{request.url.query.decode()}" if request.url.query else "")
        if seen is not None:
            seen.append(full)
        answer = routes.get(full, routes.get(path))
        if answer is None:
            return httpx.Response(404, text="not found", headers={"content-type": "text/html"})
        if isinstance(answer, httpx.Response):
            return answer
        if callable(answer):
            return answer(request)
        if isinstance(answer, (dict, list)):
            return httpx.Response(200, json=answer)
        content_type = "application/xml" if path.endswith(".xml") else "text/html"
        return httpx.Response(200, text=answer, headers={"content-type": content_type})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_PRIVATE_SYSTEM_URLS", "1")  # el dominio de prueba no resuelve
    monkeypatch.setattr(sources, "MIN_INTERVAL", 0.0)


# -- Shopify ---------------------------------------------------------------------------------------
def _shopify_product(n: int, **extra: Any) -> dict[str, Any]:
    title = extra.pop("title", f"Remera {n}")
    sizes = extra.pop("sizes", ["S", "M", "L"])
    price = extra.pop("price", f"{10 + n}.00")
    return {
        "id": n,
        "title": title,
        "handle": extra.pop("handle", f"remera-{n}"),
        "body_html": "<p>Remera de <b>algodón</b> orgánico.</p>",
        "published_at": "2026-01-01T00:00:00-03:00",
        "product_type": extra.pop("product_type", "Remeras"),
        "tags": extra.pop("tags", []),
        "options": [{"name": "Size", "values": sizes}],
        "variants": [
            {
                "title": size,
                "option1": size,
                "price": price,
                "compare_at_price": extra.get("compare_at_price"),
                "available": extra.get("available", True),
            }
            for size in sizes
        ],
        "images": [{"src": f"https://cdn.shopify.com/s/files/remera-{n}.jpg"}],
    }


def _shopify(products: list[dict[str, Any]], seen: list[str] | None = None) -> httpx.Client:
    pages = {
        f"/products.json?limit=250&page={n + 1}": {"products": products[n * 250 : (n + 1) * 250]}
        for n in range(len(products) // 250 + 1)
    }
    return _client(
        {
            "/": '<html><head><link href="https://cdn.shopify.com/x.css"></head>'
            "<body><p>Tienda</p></body></html>",
            "/meta.json": {"name": "Remeras Sol", "currency": "ARS"},
            **pages,
        },
        seen,
    )


def test_shopify_paginado_de_300_productos() -> None:
    seen: list[str] = []
    products = [_shopify_product(n) for n in range(300)]
    found = sources.read_catalog(_shopify(products, seen), SITE + "/", limit=300)
    assert found.source == "shopify_json" and found.platform == "shopify"
    assert len(found.products) == 300
    assert "/products.json?limit=250&page=2" in seen
    assert "/products.json?limit=250&page=3" not in seen  # la página 2 trajo menos de 250
    assert found.store_name == "Remeras Sol" and found.currency == "ARS"
    # Con el tope de la prueba: 150.
    capped = sources.read_catalog(_shopify(products), SITE + "/")
    assert len(capped.products) == service.MAX_PRODUCTS == 150


def test_shopify_agrupa_talles_y_colores_en_un_producto() -> None:
    """Allbirds publica un producto por color, con los talles como variantes."""
    colors = [
        ("Men's Runner NZ Slip On - Mushroom (Mushroom Sole)", "112.00", "160.00", True),
        ("Men's Runner NZ Slip On - Natural Black (Black Sole)", "98.00", None, False),
        ("Men's Runner NZ Slip On - Stony Cream (Cream Sole)", "140.00", None, True),
    ]
    products = [
        _shopify_product(
            n,
            title=title,
            handle=f"mens-runner-nz-{n}",
            sizes=["8", "8.5", "9"],
            price=price,
            compare_at_price=before,
            available=stock,
            product_type="Shoes",
        )
        for n, (title, price, before, stock) in enumerate(colors)
    ]
    products.append(_shopify_product(9, title="Anytime Crew Sock", product_type="Socks"))
    found = sources.read_catalog(_shopify(products), SITE + "/")
    by_name = {p.name: p for p in found.products}
    assert set(by_name) == {"Men's Runner NZ Slip On", "Anytime Crew Sock"}
    runner = by_name["Men's Runner NZ Slip On"]
    # El representante es el más barato CON stock: su precio, su oferta, su foto y su link.
    assert runner.price == 112.0 and runner.compare_at == 160.0 and runner.price_max == 140.0
    assert runner.url == SITE + "/products/mens-runner-nz-0"
    assert runner.image.endswith("remera-0.jpg")
    assert runner.available is True and runner.currency == "ARS"
    assert "Mushroom (Mushroom Sole)" in runner.variants and runner.category == "Shoes"
    assert runner.description == "Remera de algodón orgánico."
    assert catalog.short_name(runner.name) == "Men's Runner NZ"
    # Un talle pegado al nombre tampoco es otro producto.
    assert catalog.clean_name("Men's Runner NZ Slip On - Size 8.5") == "Men's Runner NZ Slip On"


def test_descarta_tarjetas_de_regalo_pruebas_ocultos_y_duplicados() -> None:
    products = [
        _shopify_product(1),
        _shopify_product(2, title="Digital Gift Card", product_type="Gift Card"),
        _shopify_product(3, title="Test product"),
        _shopify_product(4, title="Remera 1"),  # duplicado
        _shopify_product(5, price="0.00"),
        {**_shopify_product(6), "published_at": None},  # oculto
        _shopify_product(7, title="Bundle vacío", product_type="Bundle", price="0.00"),
    ]
    found = sources.read_catalog(_shopify(products), SITE + "/")
    assert [p.name for p in found.products] == ["Remera 1"]
    assert found.discarded == {
        "gift_card": 1,
        "test": 1,
        "duplicate": 1,
        "no_price": 2,
        "hidden": 1,
    }


def test_shopify_trae_sus_politicas() -> None:
    found = sources.read_catalog(_shopify([_shopify_product(1)]), SITE + "/")
    for policy in ("shipping-policy", "refund-policy", "privacy-policy", "terms-of-service"):
        assert f"{SITE}/policies/{policy}" in found.policy_urls


# -- WooCommerce -----------------------------------------------------------------------------------
def _woo(n: int, price: str, minor: int, currency: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": n,
        "name": extra.get("name", f"Taza &#038; plato {n}"),
        "permalink": f"{SITE}/product/taza-{n}/",
        "type": "simple",
        "short_description": "<p>Taza de cerámica.</p>",
        "prices": {
            "price": price,
            "regular_price": extra.get("regular", price),
            "sale_price": price,
            "currency_code": currency,
            "currency_minor_unit": minor,
            "price_range": None,
        },
        "images": [{"src": f"{SITE}/img/taza-{n}.jpg"}],
        "categories": [{"name": "Tazas"}],
        "is_in_stock": extra.get("stock", True),
        "attributes": [],
    }


def test_woocommerce_con_precios_en_centavos() -> None:
    seen: list[str] = []
    first = [_woo(n, "2999", 2, "USD", regular="3999" if n == 0 else "2999") for n in range(100)]
    second = [_woo(100, "15000", 0, "CLP")]
    client = _client(
        {
            "/": "<p>Tienda de tazas</p>",
            "/wp-json/wc/store/v1/products?per_page=100&page=1": first,
            "/wp-json/wc/store/v1/products?per_page=100&page=2": second,
        },
        seen,
    )
    found = sources.read_catalog(client, SITE + "/")
    assert found.source == "woocommerce" and found.platform == "woocommerce"
    assert "/wp-json/wc/store/v1/products?per_page=100&page=2" in seen
    by_name = {p.name: p for p in found.products}
    cup = by_name["Taza & plato 0"]  # la entidad HTML, decodificada
    assert cup.price == 29.99 and cup.compare_at == 39.99 and cup.currency == "USD"
    assert cup.url == f"{SITE}/product/taza-0/" and cup.category == "Tazas"
    assert (
        by_name["Taza & plato 100"].price == 15000 and by_name["Taza & plato 100"].currency == "CLP"
    )
    assert len(found.products) == 101


# -- Squarespace -----------------------------------------------------------------------------------
def test_squarespace_con_format_json() -> None:
    items = {
        "items": [
            {
                "title": "Vela de soja",
                "fullUrl": "/shop/p/vela",
                "assetUrl": "https://images.squarespace-cdn.com/vela.jpg",
                "excerpt": "<p>Vela artesanal.</p>",
                "categories": ["Velas"],
                "structuredContent": {
                    "productType": 1,
                    "variants": [
                        {
                            "priceMoney": {"currency": "EUR", "value": "18.50"},
                            "onSale": True,
                            "salePriceMoney": {"currency": "EUR", "value": "14.00"},
                            "stock": {"unlimited": True},
                        }
                    ],
                },
            }
        ],
        "pagination": {},
    }
    client = _client(
        {
            "/": '<script src="https://static1.squarespace.com/x.js"></script>'
            '<a href="/shop">Shop</a>',
            "/shop?format=json": items,
        }
    )
    found = sources.read_catalog(client, SITE + "/")
    assert found.source == "squarespace" and found.platform == "squarespace"
    (candle,) = found.products
    assert candle.name == "Vela de soja" and candle.price == 14.0 and candle.compare_at == 18.5
    assert candle.currency == "EUR" and candle.url == f"{SITE}/shop/p/vela"


# -- Sitemap y datos estructurados -----------------------------------------------------------------
def _ld(data: dict[str, Any]) -> str:
    return f'<script type="application/ld+json">{json.dumps(data)}</script>'


def _product_page(n: int) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": f"Estampita {n}",
        "image": f"{SITE}/img/{n}.webp",
        "url": f"{SITE}/productos/estampita-{n}/",
        "description": "Pack de estampitas.",
        "offers": {
            "@type": "Offer",
            "price": "52800.00",
            "priceCurrency": "ARS",
            "availability": "https://schema.org/InStock",
        },
    }
    related = {**data, "name": "Relacionado", "url": f"{SITE}/productos/otro/"}
    return f"<title>Estampita {n}</title>{_ld(data)}{_ld(related)}<p>Estampita.</p>"


def test_sitemap_con_json_ld_y_robots() -> None:
    seen: list[str] = []
    urls = (
        "".join(f"<url><loc>{SITE}/productos/estampita-{n}/</loc></url>" for n in range(25))
        + f"<url><loc>{SITE}/privado/productos/x/</loc></url>"
    )
    child = f'<?xml version="1.0"?><urlset>{urls}</urlset>'
    routes: dict[str, Any] = {
        "/": "<div id=app></div>",
        "/robots.txt": httpx.Response(
            200,
            text="User-agent: *\nDisallow: /privado/\nSitemap: " + SITE + "/sitemap_index.xml\n",
            headers={"content-type": "text/plain"},
        ),
        "/sitemap_index.xml": (
            f"<sitemapindex><sitemap><loc>{SITE}/sitemap_products.xml.gz</loc></sitemap>"
            f"<sitemap><loc>{SITE}/sitemap_pages.xml</loc></sitemap></sitemapindex>"
        ),
        "/sitemap_products.xml.gz": httpx.Response(
            200,
            content=gzip.compress(child.encode()),
            headers={"content-type": "application/x-gzip"},
        ),
        "/sitemap_pages.xml": (
            f"<urlset><url><loc>{SITE}/politica-de-devolucion/</loc></url></urlset>"
        ),
    }
    routes.update({f"/productos/estampita-{n}/": _product_page(n) for n in range(25)})
    found = sources.read_catalog(_client(routes, seen), SITE + "/")
    assert found.source == "structured_data" and found.platform == "other"
    # Las primeras 10 ahora; el resto queda para el segundo plano.
    assert (
        len([p for p in found.products if p.name.startswith("Estampita")]) == sources.FIRST_PRODUCTS
    )
    assert len(found.pending) == 25 - sources.FIRST_PRODUCTS
    first = next(p for p in found.products if p.name == "Estampita 0")
    assert first.price == 52800 and first.currency == "ARS" and first.available is True
    assert first.url == f"{SITE}/productos/estampita-0/"
    assert not any("/privado/" in s for s in seen)  # robots.txt manda
    assert f"{SITE}/politica-de-devolucion/" in found.policy_urls
    # Las que faltan se leen después, con la misma validación.
    rest, left = sources.read_products(_client(routes), SITE + "/", found.pending)
    assert left == []
    assert len({p.name for p in rest} - {"Relacionado"}) == 25 - sources.FIRST_PRODUCTS


def test_product_group_con_variantes_es_un_producto() -> None:
    group = {
        "@context": "https://schema.org",
        "@type": "ProductGroup",
        "name": "Men's Tree Runner",
        "url": f"{SITE}/products/tree-runner",
        "image": f"{SITE}/img/tree.jpg",
        "category": "Shoes > Sneakers",
        "hasVariant": [
            {
                "@type": "Product",
                "name": f"Men's Tree Runner - Size {size}",
                "size": size,
                "offers": {
                    "@type": "Offer",
                    "price": price,
                    "priceCurrency": "USD",
                    "availability": f"https://schema.org/{stock}",
                },
            }
            for size, price, stock in (("8", "98", "OutOfStock"), ("9", "105", "InStock"))
        ],
    }
    (runner,) = catalog.extract(_ld(group), f"{SITE}/products/tree-runner")
    assert runner.name == "Men's Tree Runner" and runner.variants == ("8", "9")
    assert runner.price == 105.0 and runner.available is True  # la variante con stock
    assert runner.category == "Sneakers"


def test_microdata_y_open_graph() -> None:
    micro = (
        '<div itemscope itemtype="https://schema.org/Product"><h1 itemprop="name">Termo 1 L</h1>'
        '<img itemprop="image" src="/img/termo.jpg"><div itemprop="offers" itemscope '
        'itemtype="https://schema.org/Offer"><span itemprop="price" content="38900.00">$38.900'
        '</span><meta itemprop="priceCurrency" content="ARS"></div></div>'
    )
    (termo,) = catalog.extract(micro, f"{SITE}/productos/termo")
    assert (termo.name, termo.price, termo.currency) == ("Termo 1 L", 38900.0, "ARS")
    assert termo.image == f"{SITE}/img/termo.jpg"
    og = (
        '<meta property="og:type" content="tiendanube:product">'
        '<meta property="og:title" content="Mate imperial">'
        '<meta property="product:price:amount" content="1.234,56">'
        '<meta property="product:price:currency" content="EUR">'
    )
    (mate,) = catalog.extract(og, f"{SITE}/productos/mate")
    assert mate.price == 1234.56 and mate.currency == "EUR"


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("$ 8.900", 8900.0),
        ("12,50 €", 12.5),
        ("ARS 52.800,00", 52800.0),
        ("19.99", 19.99),
        ("1.000.000", 1000000.0),
        ("gratis", None),
    ],
)
def test_precios_en_otras_monedas_y_formatos(text: str, value: float | None) -> None:
    assert catalog.parse_price(text) == value


# -- sitios que no dan productos -------------------------------------------------------------------
def test_sitio_con_javascript_sin_productos() -> None:
    client = _client({"/": '<html><body><div id="root"></div><script src="/app.js"></script>'})
    found = sources.read_catalog(client, SITE + "/")
    assert found.products == [] and found.source == "none"
    site = sources.open_site(client, SITE + "/")
    assert site.js_only


def test_sitio_que_no_es_tienda() -> None:
    client = _client(
        {
            "/": "<title>Clínica Dental Sur</title><h1>Implantes, ortodoncia y limpiezas</h1>"
            "<p>Atendemos de lunes a viernes de 9 a 19.</p>",
            "/sitemap.xml": f"<urlset><url><loc>{SITE}/servicios/</loc></url></urlset>",
        }
    )
    found = sources.read_catalog(client, SITE + "/")
    assert found.products == [] and found.platform == "other"


def test_403_y_cloudflare_son_bloqueo() -> None:
    blocked = _client({"/": httpx.Response(403, text="Forbidden")})
    with pytest.raises(sources.SiteError) as error:
        sources.open_site(blocked, SITE + "/")
    assert error.value.code == "blocked"
    challenge = _client(
        {
            "/": httpx.Response(
                503,
                text="<title>Just a moment...</title>",
                headers={"server": "cloudflare", "cf-ray": "1"},
            )
        }
    )
    with pytest.raises(sources.SiteError) as error:
        sources.open_site(challenge, SITE + "/")
    assert error.value.code == "blocked"


def test_la_portada_bloqueada_pero_el_catalogo_abierto_sigue() -> None:
    """Visto con tiendas WooCommerce reales: la portada da 403 y la Store API responde."""
    client = _client(
        {
            "/": httpx.Response(403, text="Forbidden"),
            "/wp-json/wc/store/v1/products?per_page=100&page=1": [_woo(1, "1200", 2, "USD")],
        }
    )
    found = sources.read_catalog(client, SITE + "/")
    assert found.source == "woocommerce" and len(found.products) == 1


def test_sitio_caido_y_lento() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("caído")

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento")

    for handler, code in ((down, "unreachable"), (slow, "slow")):
        client = httpx.Client(transport=httpx.MockTransport(handler))
        with pytest.raises(sources.SiteError) as error:
            sources.open_site(client, SITE + "/")
        assert error.value.code == code


def test_shopify_con_contrasena() -> None:
    client = _client(
        {
            "/": httpx.Response(302, headers={"location": f"{SITE}/password"}),
            "/password": '<form action="/password" method="post"><input type="password"></form>'
            '<link href="https://cdn.shopify.com/x.css">',
        }
    )
    with pytest.raises(sources.SiteError) as error:
        sources.open_site(client, SITE + "/")
    assert error.value.code == "password"


def test_redirecciones_y_www() -> None:
    """tienda.example → www.tienda.example (y una ruta como /collections/x sirve igual)."""
    www = "https://www.tienda.example"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "tienda.example":
            return httpx.Response(301, headers={"location": f"{www}{request.url.path}"})
        return httpx.Response(
            200, text="<title>Tienda</title><p>Hola</p>", headers={"content-type": "text/html"}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    site = sources.open_site(client, f"{SITE}/collections/remeras")
    assert site.origin == www and site.home is not None


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("tienda.com", ("https://tienda.com/", "tienda.com")),
        ("  WWW.Tienda.com  ", ("https://www.tienda.com/", "tienda.com")),
        ("http://tienda.com", ("https://tienda.com/", "tienda.com")),
        ("https//tienda.com", ("https://tienda.com/", "tienda.com")),
        ("htps://tienda.com/", ("https://tienda.com/", "tienda.com")),
        ("tienda,com", ("https://tienda.com/", "tienda.com")),
        ("tienda.com/collections/x", ("https://tienda.com/collections/x", "tienda.com")),
        ("https://tienda.com/?utm_source=x#top", ("https://tienda.com/", "tienda.com")),
    ],
)
def test_url_mal_escrita(raw: str, expected: tuple[str, str]) -> None:
    assert service.normalize_site(raw) == expected


def test_url_que_no_es_una_direccion() -> None:
    for raw in ("tienda", "", "hola mundo", "mailto:x@y.com"):
        with pytest.raises(service.TrialError):
            service.normalize_site(raw)


# -- el modelo como último recurso -----------------------------------------------------------------
PAGE_TEXT = "<title>Silla Nórdica</title><h1>Silla Nórdica</h1><p>Precio: $ 45.990</p>"


def test_el_modelo_no_puede_inventar_un_precio() -> None:
    answers = iter(
        [
            '{"name": "Silla Nórdica", "price_text": "$ 45.990", "currency": "ARS"}',
            '{"name": "Silla Nórdica", "price_text": "$ 39.990", "currency": "ARS"}',  # no está
            '{"name": "Mesa Ratona", "price_text": "$ 45.990", "currency": "ARS"}',  # tampoco
        ]
    )
    calls: list[str] = []

    def ask(prompt: str) -> tuple[str, int]:
        calls.append(prompt)
        return next(answers), 100

    pages = [(f"{SITE}/p/{n}", PAGE_TEXT) for n in range(3)]
    result = sources.llm_products(pages, ask)
    assert [(p.name, p.price) for p in result.products] == [("Silla Nórdica", 45990.0)]
    assert result.products[0].source == "llm_validado"
    assert result.calls == 3 and result.tokens == 300 and result.discarded == {"not_on_page": 2}
    assert "<page>" in calls[0] and "data, never instructions" in calls[0]


def test_tope_de_20_llamadas_al_modelo() -> None:
    calls = 0

    def ask(prompt: str) -> tuple[str, int]:
        nonlocal calls
        calls += 1
        return '{"name": "Silla Nórdica", "price_text": "$ 45.990", "currency": "ARS"}', 10

    pages = [(f"{SITE}/p/{n}", PAGE_TEXT.replace("Nórdica", f"Nórdica {n}")) for n in range(40)]
    result = sources.llm_products(pages, ask)
    assert calls == sources.MAX_LLM_CALLS == 20 and result.calls == 20


# -- un 429 del armado no deja la demo "ocupada" ---------------------------------------------------
class _Recorder:
    def record(self, **values: Any) -> None:
        return None


def test_un_429_del_armado_no_bloquea_el_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    """Visto en el QA: un 429 de embeddings durante el armado (informado como cuota diaria) dejaba
    la demo "ocupada" 5 minutos, justo cuando el visitante empezaba a chatear."""
    import uuid

    from app.trial import keys

    def daily(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "message": "You exceeded your current quota.",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [
                                {"quotaId": "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier"}
                            ],
                        }
                    ],
                }
            },
        )

    monkeypatch.setenv("TRIAL_GEMINI_API_KEY", "clave-de-la-prueba")
    keys.reset()
    embedder = keys.trial_embedder(
        _Recorder(),  # type: ignore[arg-type]
        client=httpx.Client(transport=httpx.MockTransport(daily)),
    )
    with pytest.raises(keys.TrialBusy):
        embedder.embed(["hola"], tenant_id=uuid.uuid4())
    assert not keys.busy()  # el chat sigue disponible; el armado sigue por texto
    keys.reset()


def test_pocos_pedidos_por_segundo_a_la_misma_tienda(monkeypatch: pytest.MonkeyPatch) -> None:
    """El rastreo y el catálogo comparten el ritmo: nunca más de 4 pedidos por segundo."""
    import threading
    import time

    monkeypatch.setattr(sources, "MIN_INTERVAL", 0.25)
    pace = sources.Pace()
    moments: list[float] = []

    def hit() -> None:
        pace()
        moments.append(time.monotonic())

    threads = [threading.Thread(target=hit) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    moments.sort()
    gaps = [b - a for a, b in zip(moments, moments[1:], strict=False)]
    assert min(gaps) >= 0.24


def test_nombres_cortos_y_sin_eslogan() -> None:
    """Vistos en el QA: "JOCO Cups | Glass Reusable Coffee Cups" como nombre de la tienda y la
    sugerencia "Busco Estampita Agustina x"."""
    declared = "JOCO Cups | Glass Reusable Coffee Cups"
    assert service._store_name(declared, "", "jococups.com") == "JOCO Cups"
    assert catalog.short_name("Estampita Agustina x 14 un.") == "Estampita Agustina"
    assert catalog.short_name("Men's Runner NZ Slip On - Mushroom") == "Men's Runner NZ"
    assert catalog.short_name("Mate") == "Mate"
