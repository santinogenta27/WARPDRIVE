"""La página de carrito de la demo de Northwind (Hito 10, punto 7; decisión 065).

El link de compra que manda el agente en la demo lleva acá: `/demo/{slug}/cart/{SKU:cantidad,…}`
con el identificador de la conversación (`ref`) y el descuento, si lo hay. Muestra el carrito con
los precios del catálogo y un botón de compra simulada: no se cobra nada. Al tocarlo, la API
simulada anota la compra de esa conversación, y en el panel (Métricas → Actualizar ventas)
aparece como venta atribuida al agente.
"""

import html
import json
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

from app.channels.web.routes import Deps
from app.demo import northwind
from app.demo.behind_routes import _company
from app.panel.routes import Form

router = APIRouter(include_in_schema=False)
_STYLE = """<style>
:root{--ink:#1f2a24;--muted:#5d6b63;--line:#e3e7e4;--bg:#f7f6f2;--card:#fff;--accent:#2f5d50}
@media (prefers-color-scheme:dark){:root{--ink:#e9eee9;--muted:#a7b3ab;--line:#33403a;
--bg:#141a17;--card:#1c2420;--accent:#7fb8a4}}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.5 system-ui,sans-serif}
main{max-width:560px;margin:40px auto;padding:0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px}
.row{display:flex;justify-content:space-between;gap:12px;padding:8px 0;
border-bottom:1px solid var(--line)}
.muted{color:var(--muted);font-size:14px}
button{margin-top:16px;width:100%;padding:12px;border:0;border-radius:10px;
background:var(--accent);color:#fff;font-size:16px;cursor:pointer}
</style>"""


def _page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(
        f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{html.escape(title)}</title>{_STYLE}</head><body><main>{body}</main></body></html>",
        headers={"Cache-Control": "no-store"},
    )


def _catalog() -> dict[str, tuple[dict[str, Any], dict[str, Any]]]:
    api = northwind.api_module()
    products = json.loads(api.PRODUCTS_PATH.read_text(encoding="utf-8"))
    return {
        v["sku"]: (view, v) for view in map(api.product_view, products) for v in view["variants"]
    }


def _northwind(deps: Any, slug: str) -> None:
    _, _, _, is_northwind = _company(deps, slug)
    if not is_northwind:
        raise HTTPException(status_code=404, detail="No disponible")


TRIAL_TEXTS = {
    "es": ("Tu carrito", "Ver en tu tienda", "Descuento", "Esta es la tienda de ejemplo de tu "
           "prueba. En tu piloto, el link lleva al checkout de tu tienda: el asistente solo manda "
           "el link, nunca cobra."),
    "en": ("Your cart", "See it in your store", "Discount", "This is your trial's sample store. "
           "In your pilot, the link goes to your store's checkout: the assistant only sends the "
           "link, it never takes a payment."),
}  # fmt: skip


def _trial_cart(
    deps: Any, tenant_id: Any, language: str, items: str, discount: str
) -> HTMLResponse:
    """El link de compra de una prueba gratis (decisión 075): el carrito con sus productos."""
    from app.trial import service

    with deps.connection() as conn:
        trial = service.for_tenant(conn, tenant_id)
    if trial is None:
        raise HTTPException(status_code=404, detail="No disponible")
    api = northwind.api_module()
    catalog = {
        v["sku"]: view
        for view in map(api.product_view, service.mock_products(trial.products))
        for v in view["variants"]
    }
    title, see, off_label, note = TRIAL_TEXTS["en" if language == "en" else "es"]
    rows, total = "", 0.0
    for part in items.split(","):
        sku, _, quantity = part.partition(":")
        view = catalog.get(sku.strip().upper())
        if view is None:
            raise HTTPException(status_code=404, detail="No existe ese producto")
        count = max(int(quantity or 1), 1)
        total += view["price"] * count
        link = (
            f' · <a href="{html.escape(view["url"])}" rel="noopener">{see}</a>'
            if view.get("url")
            else ""
        )
        rows += (
            f'<div class="row"><span>{html.escape(view["name"])} × {count}{link}</span>'
            f"<b>{html.escape(view['currency'])} {view['price'] * count:,.2f}</b></div>"
        )
    off = ""
    if discount:
        percent = float("".join(c for c in discount if c.isdigit() or c == ".") or 0)
        total *= 1 - percent / 100
        off = f'<div class="row muted"><span>{off_label} {html.escape(discount)}</span></div>'
    currency = html.escape(next(iter(catalog.values()))["currency"]) if catalog else ""
    body = (
        f'<h1>{title}</h1><div class="card">{rows}{off}<div class="row"><b>Total</b>'
        f'<b>{currency} {total:,.2f}</b></div></div><p class="muted">{note}</p>'
    )
    return _page(f"{title} · {trial.name}", body)


@router.get("/demo/{slug}/cart/{items}", response_class=HTMLResponse)
def cart_page(slug: str, items: str, deps: Deps, ref: str = "", discount: str = "") -> HTMLResponse:
    _, tenant_id, language, is_northwind = _company(deps, slug)
    if not is_northwind:
        return _trial_cart(deps, tenant_id, language, items, discount)
    catalog = _catalog()
    rows, total = "", 0.0
    for part in items.split(","):
        sku, _, quantity = part.partition(":")
        found = catalog.get(sku.strip().upper())
        if found is None:
            raise HTTPException(status_code=404, detail="No existe ese producto")
        view, variant = found
        count = max(int(quantity or 1), 1)
        total += view["price"] * count
        rows += (
            f'<div class="row"><span>{html.escape(view["name"])} · '
            f"{html.escape(variant['color'])} · {html.escape(variant['size'])} × {count}</span>"
            f"<b>${view['price'] * count:,.2f}</b></div>"
        )
    off = ""
    if discount:
        percent = float("".join(c for c in discount if c.isdigit() or c == ".") or 0)
        total *= 1 - percent / 100
        off = f'<div class="row muted"><span>Discount {html.escape(discount)}</span></div>'
    form = (
        f'<form method="post" action="/demo/{html.escape(slug)}/cart/complete">'
        f'<input type="hidden" name="ref" value="{html.escape(ref)}">'
        '<button type="submit">Complete purchase (demo, nothing is charged)</button></form>'
        if ref
        else ""
    )
    body = (
        '<h1>Your cart</h1><div class="card">'
        f'{rows}{off}<div class="row"><b>Total</b><b>${total:,.2f}</b></div>{form}</div>'
        '<p class="muted">Northwind Outfitters is a demo store. This page stands in for the '
        "store's checkout: the assistant only sends the link, it never takes a payment.</p>"
    )
    return _page("Your cart · Northwind Outfitters", body)


@router.post("/demo/{slug}/cart/complete", response_class=HTMLResponse)
def complete(slug: str, deps: Deps, form: Form) -> HTMLResponse:
    """La compra simulada: la API de la demo la anota para la conversación del link."""
    _northwind(deps, slug)
    ref = form.get("ref", "")
    done = northwind.shared_northwind().post(f"/checkouts/{quote(ref, safe='')}/complete")
    if done.status_code != 200:
        raise HTTPException(status_code=404, detail="No existe ese carrito")
    order = html.escape(str(done.json().get("order_id")))
    body = (
        f'<h1>Thank you!</h1><div class="card"><p>Order <b>{order}</b> is placed (demo: nothing '
        'was charged).</p><p class="muted">In the team dashboard, Metrics → Update sales shows '
        "it as a sale attributed to the assistant.</p></div>"
    )
    return _page("Thank you · Northwind Outfitters", body)
