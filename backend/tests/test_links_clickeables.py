"""Links que se pueden tocar en todos los canales (decisión 103).

Visto en la prueba gratis con deathwishcoffee.com: el agente armó bien el carrito, pero el link
salió como texto crudo ("…/cart/DEMO-P006-STA-OS:1?ref=ea-…") y no se podía tocar. Ahora:
- Chat web: los links se tocan y abren en otra pestaña; un link de compra es un botón con el
  total; los demás muestran el dominio. Solo http y https, todo armado con nodos (sin HTML).
- WhatsApp: el link va solo en su línea, corto (/go/{código}) y con vista previa.
- Prueba gratis: el link de compra es el carrito real de la tienda (Shopify) o la página real del
  producto.
"""

import json
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.agent import presentable
from app.channels import links
from app.channels.web.routes import STATIC
from app.channels.whatsapp.client import WhatsAppClient
from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.main import app
from app.trial import service, sources

NODE = shutil.which("node")
STORE = "https://www.cafe-norte.example"


# -- el chat web: el armado de los links, corrido con Node ----------------------------------------
_HARNESS = r"""
const input = JSON.parse(require("fs").readFileSync(0, "utf8"));
function N(tag) {
  this.tag = tag; this.kids = []; this.attrs = {}; this.className = ""; this.textContent = null;
}
N.prototype.appendChild = function (c) { this.kids.push(c); return c; };
N.prototype.setAttribute = function (k, v) { this.attrs[k] = String(v); };
const doc = {
  createElement: (t) => new N(t),
  createDocumentFragment: () => new N(""),
  createTextNode: (s) => ({ text: String(s) }),
};
const ENT = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" };
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ENT[c]);
function html(n) {
  if (n.text != null) return esc(n.text);
  const inner = n.textContent != null ? esc(n.textContent) : n.kids.map(html).join("");
  if (!n.tag) return inner;
  const attrs = Object.keys(n.attrs).map((k) => " " + k + '="' + esc(n.attrs[k]) + '"').join("");
  const cls = n.className ? ' class="' + esc(n.className) + '"' : "";
  return "<" + n.tag + cls + attrs + ">" + inner + "</" + n.tag + ">";
}
%BLOCK%
const out = renderMessage(doc, input.text, input.opts);
process.stdout.write(JSON.stringify({ body: html(out.body), buttons: out.buttons.map(html) }));
"""


def _block() -> str:
    script = (STATIC / "widget.js").read_text(encoding="utf-8")
    found = re.search(r"/\* links:start.*?\*/(.*?)/\* links:end \*/", script, re.S)
    assert found is not None, "widget.js tiene que marcar el bloque de los links"
    return found.group(1)


def render(text: str, **opts: Any) -> dict[str, Any]:
    if NODE is None:
        pytest.skip("Hace falta Node para correr el widget")
    options = {"hosts": ["cafe-norte.example", "agente.example"], "buy": "Complete your purchase"}
    options["lang"] = "en"
    options.update(opts)
    result = subprocess.run(
        [NODE, "-e", _HARNESS.replace("%BLOCK%", _block())],
        input=json.dumps({"text": text, "opts": options}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    found: dict[str, Any] = json.loads(result.stdout)
    return found


def test_un_link_del_texto_se_toca_y_abre_en_otra_pestana() -> None:
    out = render("Our shipping policy: https://www.cafe-norte.example/policies/shipping")
    body = out["body"]
    assert '<a class="lnk" href="https://www.cafe-norte.example/policies/shipping"' in body
    assert 'target="_blank"' in body and 'rel="noopener noreferrer"' in body
    # Corto: el dominio y un ícono, no la URL entera.
    assert ">cafe-norte.example<" in body and "/policies/shipping<" not in body
    assert out["buttons"] == []


def test_javascript_y_data_no_se_vuelven_links() -> None:
    for text in (
        "Mirá javascript:alert(1) ahora",
        "[Tocá acá](javascript:alert(1))",
        "Imagen: data:text/html;base64,PHNjcmlwdD4=",
        "[x](data:text/html,<script>alert(1)</script>)",
    ):
        out = render(text)
        assert "<a" not in out["body"], (text, out)
        assert "<script>" not in out["body"]


def test_el_texto_con_script_sigue_escapado() -> None:
    out = render("<script>alert('x')</script> *hola* <img src=x onerror=alert(1)>")
    assert "&lt;script&gt;" in out["body"] and "<script>" not in out["body"]
    assert "&lt;img" in out["body"]
    assert "<strong>hola</strong>" in out["body"]  # la negrita de WhatsApp sigue andando


def test_una_url_suelta_terminada_en_punto_no_rompe_el_link() -> None:
    out = render("See https://cafe-norte.example/faq. Or (https://cafe-norte.example/help).")
    assert 'href="https://cafe-norte.example/faq"' in out["body"]
    assert 'href="https://cafe-norte.example/help"' in out["body"]
    assert out["body"].endswith(").")


def test_link_de_carrito_es_un_boton_con_el_total() -> None:
    url = "https://www.cafe-norte.example/cart/4417:1?ref=ea-355163b7586d"
    out = render(
        f"Your cart is ready: {url}", checkouts=[{"url": url, "total": 7, "currency": "USD"}]
    )
    assert url not in out["body"] and "cart/4417" not in out["body"]  # el link crudo no se ve
    assert out["body"] == "Your cart is ready:"
    (button,) = out["buttons"]
    assert button.startswith('<a class="buy" href="https://www.cafe-norte.example/cart/4417:1')
    assert "Complete your purchase · $7.00 →" in button
    assert 'target="_blank"' in button and 'rel="noopener noreferrer"' in button

    # El link en el medio de la frase: no quedan los dos puntos pegados al punto.
    middle = render(f"Here is your checkout link: {url}. The total is $7.00.")
    assert middle["body"] == "Here is your checkout link. The total is $7.00."

    # Sin el monto: el botón sin total. En castellano, su texto.
    plain = render(f"Listo: {url}", buy="Completá tu compra", lang="es")
    assert "Completá tu compra →" in plain["buttons"][0]


def test_un_carrito_de_otro_dominio_muestra_a_donde_va() -> None:
    out = render("Pagá acá: https://pagos.otro-sitio.example/checkouts/abc")
    (button,) = out["buttons"]
    assert "pagos.otro-sitio.example" in button


def test_markdown_y_link_suelto_se_ven_bien() -> None:
    out = render(
        "Read [our returns policy](https://cafe-norte.example/returns) or "
        "https://agente.example/c/abc and [this](https://raro.example/x_y?utm_source=a_b)."
    )
    body = out["body"]
    assert ">our returns policy</a>" in body and "[our" not in body and "](" not in body
    assert ">agente.example<" in body
    # Un dominio que no es de la empresa ni de la plataforma: el dominio a la vista.
    assert "raro.example" in body.split('href="https://raro.example/x_y?utm_source=a_b"', 1)[1]


def test_el_widget_no_arma_links_con_innerhtml() -> None:
    script = (STATIC / "widget.js").read_text(encoding="utf-8")
    assert "bubble.innerHTML" not in script
    block = _block()
    assert "innerHTML" not in block
    assert "textContent" in block and "createTextNode" in block


# -- presentable: una URL con guiones bajos no es un nombre de herramienta -------------------------
def test_url_con_guiones_bajos_pasa_presentable() -> None:
    for message in (
        "Here it is: https://shop.example/products/cold_brew_kit?utm_source=chat_bot",
        "Mirá [el kit](https://shop.example/products/cold_brew_kit).",
        "Your cart: https://www.cafe-norte.example/cart/DEMO-P006-STA-OS:1?ref=ea-1_2",
    ):
        assert presentable.problem(message) is None, message
    assert presentable.problem("Uso consultar_pedido para verlo") == "nombre de herramienta"


# -- WhatsApp: el link solo en su línea, corto y persistido ----------------------------------------
@pytest.fixture
def tenant(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://agente.example")
    company = create_company(
        conn, "Café Norte", f"ana-{uuid.uuid4().hex[:6]}@cafe-norte.example", "clave-larga-123"
    )
    from app.channels.web import widget

    widget.add_domains(conn, company.tenant_id, ["www.cafe-norte.example"])
    return company.tenant_id


def test_en_whatsapp_el_link_va_solo_en_su_linea_y_corto(
    conn: psycopg.Connection, tenant: uuid.UUID
) -> None:
    cart = f"{STORE}/cart/4417:1?ref=ea-355163b7586d"
    body = links.for_whatsapp(conn, tenant, f"Your cart is ready: {cart}. Anything else?")
    text, short = body.rsplit("\n", 1)
    assert text.strip() == "Your cart is ready. Anything else?"
    assert re.fullmatch(r"https://agente\.example/go/[A-Za-z0-9]{12}", short), short
    assert body.count("\n\n") == 1  # una línea en blanco y el link solo

    # Persistido, con su empresa y el ref de la conversación.
    code = short.rsplit("/", 1)[1]
    with tenant_scope(conn, tenant):
        row = conn.execute(
            "select tenant_id, target, ref from short_links where code = %s", (code,)
        ).fetchone()
    assert row == (tenant, cart, "ea-355163b7586d")

    # Idempotente: el mismo carrito con el mismo ref da el mismo código.
    again = links.for_whatsapp(conn, tenant, f"Here you go:\n{cart}")
    assert again.endswith(short)
    other = links.for_whatsapp(conn, tenant, cart.replace("ea-355163b7586d", "ea-000000000000"))
    assert not other.endswith(short)


def test_en_whatsapp_un_link_de_otro_dominio_no_se_acorta(
    conn: psycopg.Connection, tenant: uuid.UUID
) -> None:
    body = links.for_whatsapp(conn, tenant, "Mirá [esto](https://raro.example/x) y listo")
    assert body == "Mirá esto y listo\n\nhttps://raro.example/x"


def test_whatsapp_pide_vista_previa_solo_si_hay_link() -> None:
    sent: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"messages": [{"id": "wamid.1"}]})

    client = WhatsAppClient(http=httpx.Client(transport=httpx.MockTransport(handler)))
    client.send_text(token="t", phone_number_id="1", to="54911", body="Hola")
    client.send_text(
        token="t", phone_number_id="1", to="54911", body="Ver\n\nhttps://a.example/go/x"
    )
    assert [m["text"]["preview_url"] for m in sent] == [False, True]


# -- la redirección /go/{código} -------------------------------------------------------------------
@pytest.fixture
def api(conn: psycopg.Connection) -> Iterator[TestClient]:
    app.dependency_overrides[links.go_connection] = lambda: lambda: nullcontext(conn)
    try:
        yield TestClient(app, follow_redirects=False)
    finally:
        app.dependency_overrides.clear()


def test_go_redirige_al_destino_guardado(
    conn: psycopg.Connection, tenant: uuid.UUID, api: TestClient
) -> None:
    cart = f"{STORE}/cart/4417:2?ref=ea-abc"
    short = links.shorten(conn, tenant, cart)
    response = api.get(urlsplit(short).path)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == cart
    assert parse_qs(urlsplit(response.headers["location"]).query)["ref"] == ["ea-abc"]


def test_go_no_es_una_redireccion_abierta(
    conn: psycopg.Connection, tenant: uuid.UUID, api: TestClient
) -> None:
    # Solo destinos de dominios permitidos: uno ajeno no se guarda.
    assert links.shorten(conn, tenant, "https://evil.example/cart/1:1") == (
        "https://evil.example/cart/1:1"
    )
    with tenant_scope(conn, tenant):
        count = conn.execute(
            "select count(*) from short_links where target like 'https://evil.example%%'"
        ).fetchone()
    assert count == (0,)
    # Un código inexistente (o una URL en la dirección): página 404 amable, sin redirigir.
    for path in (
        "/go/NoExiste1234",
        "/go/abc?to=https://evil.example",
        "/go/https%3A%2F%2Fevil.example",
    ):
        response = api.get(path)
        assert response.status_code == 404, path
        assert "location" not in response.headers
        assert "text/html" in response.headers["content-type"]
        assert "evil.example" not in response.text


def test_la_tabla_de_links_tiene_tenant_y_rls(conn: psycopg.Connection) -> None:
    row = conn.execute(
        "select relrowsecurity, relforcerowsecurity from pg_class where relname = 'short_links'"
    ).fetchone()
    assert row == (True, True)
    policies = conn.execute(
        "select count(*) from pg_policies where tablename = 'short_links'"
    ).fetchone()
    assert policies is not None and policies[0] >= 1


def test_un_link_de_otra_empresa_no_se_ve(conn: psycopg.Connection, tenant: uuid.UUID) -> None:
    links.shorten(conn, tenant, f"{STORE}/cart/1:1?ref=ea-x")
    other = create_company(conn, "Otra", f"o-{uuid.uuid4().hex[:6]}@otra.example", "clave-larga-1")
    with tenant_scope(conn, other.tenant_id):
        seen = conn.execute("select count(*) from short_links").fetchone()
    assert seen == (0,)


def test_el_widget_recibe_los_dominios_propios(conn: psycopg.Connection, tenant: uuid.UUID) -> None:
    from app.channels.web.routes import WebDeps, web_deps
    from app.companies.create import chat_slug

    slug = chat_slug(conn, tenant)
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: nullcontext(conn),
        agent=lambda c: None,  # type: ignore[arg-type,return-value]
        address=f"web:{slug}",
    )
    try:
        data = TestClient(app).get(f"/chat/widget/config?canal={slug}").json()
    finally:
        app.dependency_overrides.clear()
    assert {"agente.example", "cafe-norte.example", "testserver"} <= set(data["link_hosts"])
    assert data["purchase_note"] == ""  # no es una prueba gratis


# -- prueba gratis: el carrito real de la tienda ---------------------------------------------------
def _death_wish_like() -> httpx.Client:
    products = [
        {
            "id": 11,
            "title": "Death Cup Single",
            "handle": "death-cup-single",
            "body_html": "<p>Coffee.</p>",
            "published_at": "2026-01-01",
            "product_type": "Coffee",
            "tags": [],
            "variants": [
                {"id": 4417, "title": "Default Title", "price": "7.00", "available": True},
            ],
            "images": [{"src": "https://cdn.shopify.com/cup.jpg"}],
        },
        {
            "id": 12,
            "title": "Whole Bean",
            "handle": "whole-bean",
            "body_html": "<p>Beans.</p>",
            "published_at": "2026-01-01",
            "product_type": "Coffee",
            "tags": [],
            "variants": [
                {"id": 5001, "title": "1 lb", "price": "19.99", "available": False},
                {"id": 5002, "title": "5 lb", "price": "79.99", "available": True},
            ],
            "images": [{"src": "https://cdn.shopify.com/bean.jpg"}],
        },
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/":
            return httpx.Response(
                200, text='<link href="https://cdn.shopify.com/x.css"><p>Coffee</p>'
            )
        if path == "/meta.json":
            return httpx.Response(200, json={"name": "Café Norte", "currency": "USD"})
        if path == "/products.json" and request.url.params.get("page") == "1":
            return httpx.Response(200, json={"products": products})
        return httpx.Response(404, text="no")

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_shopify_guarda_el_variant_id_real(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ALLOW_PRIVATE_SYSTEM_URLS", "1")
    monkeypatch.setattr(sources, "MIN_INTERVAL", 0.0)
    found = sources.read_catalog(_death_wish_like(), STORE + "/")
    items = {p.name: p.as_dict() for p in found.products}
    assert items["Death Cup Single"]["variant_id"] == "4417"
    beans = items["Whole Bean"]
    assert beans["variant_id"] == "5002"  # la primera disponible
    assert beans["variant_ids"] == {"1 lb": "5001", "5 lb": "5002"}


def _checkout(sku: str, size: str = "One size", quantity: int = 1) -> dict[str, Any]:
    return {
        "url": f"https://agente.example/demo/x/cart/{sku}:{quantity}?ref=ea-355163b7586d",
        "items": [{"sku": sku, "name": "x", "size": size, "quantity": quantity}],
        "total": 7.0,
        "currency": "USD",
    }


def test_prueba_con_shopify_lleva_al_carrito_real_de_la_tienda() -> None:
    products = [
        {"name": "Whole Bean", "url": f"{STORE}/products/whole-bean", "source": "shopify_json",
         "variant_id": "5002", "variant_ids": {"1 lb": "5001", "5 lb": "5002"}},
        {"name": "Death Cup Single", "url": f"{STORE}/products/death-cup-single",
         "source": "shopify_json", "variant_id": "4417", "variant_ids": {}},
    ]  # fmt: skip
    found = service.store_checkout(products, _checkout("DEMO-P002-STA-OS"))
    assert found["url"] == f"{STORE}/cart/4417:1?ref=ea-355163b7586d"
    assert found["total"] == 7.0  # el resto del carrito no cambia
    sized = service.store_checkout(products, _checkout("DEMO-P001-STA-1 LB", "1 lb", 2))
    assert sized["url"] == f"{STORE}/cart/5001:2?ref=ea-355163b7586d"


def test_prueba_que_no_es_shopify_lleva_a_la_pagina_del_producto() -> None:
    products = [
        {"name": "Mate", "url": "https://www.lepou.example/productos/mate/",
         "source": "structured_data"},
    ]  # fmt: skip
    found = service.store_checkout(products, _checkout("DEMO-P001-STA-OS"))
    assert found["url"].startswith("https://www.lepou.example/productos/mate/")
    # La prueba de ejemplo (sin tienda real) sigue con la página de la demo.
    sample = [{"name": "Remera", "url": "", "source": "sample"}]
    assert service.store_checkout(sample, _checkout("DEMO-P001-STA-OS")) == _checkout(
        "DEMO-P001-STA-OS"
    )


def test_la_prueba_explica_el_boton_de_compra_en_su_idioma() -> None:
    assert service.purchase_note("en") == (
        "This opens your real store's checkout. In your pilot, every sale the agent starts "
        "is tracked in your dashboard."
    )
    assert "checkout real de tu tienda" in service.purchase_note("es")
