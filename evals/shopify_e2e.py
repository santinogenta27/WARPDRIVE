"""Verificación de punta a punta contra una tienda de desarrollo REAL de Shopify (decisión 086).

Recorre lo mismo que hace una empresa en Deploy, con la red de verdad:

1. Pide el token con el Client ID y el Client secret de una app del Dev Dashboard (24 h).
2. Revisa que la app tenga los permisos que usa el conector.
3. Conecta la tienda a una empresa de prueba, corre "Test connection" y revisa que los dominios
   de la tienda se agreguen solos a los del chat.
4. Consulta un pedido y busca productos por la API canónica, como lo haría el agente.
5. Con --write, además crea un cupón de un solo uso sobre ese pedido (escribe en la tienda).

Todo lo de la base corre en una transacción contra DATABASE_URL_STAGING que se revierte al final:
no deja datos. No llama a ningún modelo.

Uso:  python evals/shopify_e2e.py [--write]
Necesita en .env: SHOPIFY_TEST_STORE, SHOPIFY_TEST_CLIENT_ID y SHOPIFY_TEST_CLIENT_SECRET. Si
faltan, el script dice exactamente qué crear en Shopify.
"""

import argparse
import os
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
for _path in (HERE.parent / "backend", HERE):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.config import load_dotenv  # noqa: E402

NEEDED = ("SHOPIFY_TEST_STORE", "SHOPIFY_TEST_CLIENT_ID", "SHOPIFY_TEST_CLIENT_SECRET")
SETUP = """Faltan {missing} en .env. Para correr esta verificación:

1. Entrá a https://dev.shopify.com/dashboard/ con tu cuenta de Shopify.
2. En "Dev stores", creá una tienda de desarrollo (tiene que ser de la MISMA organización que la
   app: si la creás desde el admin de una tienda, el token con Client ID y secret no anda y
   Shopify responde shop_not_permitted). Cargale al menos un producto y un pedido de prueba
   (Orders → Create order, marcado como pagado).
3. En "Apps": Create app → Start from Dev Dashboard → nombre "Agente E2E" → Create.
4. En "Versions": en Scopes poné exactamente
   {scopes}
   En App URL, la dirección de la tienda. Tocá Release.
5. "Install app" (tarjeta Installs) → elegí la tienda de desarrollo → Install.
6. En "Settings" de la app copiá el Client ID y el Client secret y agregá a .env:

   SHOPIFY_TEST_STORE=tu-tienda.myshopify.com
   SHOPIFY_TEST_CLIENT_ID=...
   SHOPIFY_TEST_CLIENT_SECRET=...

7. Corré: python evals/shopify_e2e.py   (y con --write para probar también un cupón)
"""


def _step(ok: bool, text: str) -> bool:
    print(f"  {'✓' if ok else '✗'} {text}")
    return ok


def _first_order(shop: str, token: str) -> str | None:
    """El número del último pedido de la tienda, para consultarlo como el agente."""
    import httpx

    from app import net
    from app.connectors.shopify import API_VERSION

    with httpx.Client(transport=net.SafeTransport(), timeout=15.0) as client:
        response = client.post(
            f"https://{shop}/admin/api/{API_VERSION}/graphql.json",
            headers={"X-Shopify-Access-Token": token},
            json={"query": "query Ultimo { orders(first: 1, reverse: true) { nodes { name } } }"},
        )
    nodes: list[dict[str, Any]] = ((response.json().get("data") or {}).get("orders") or {}).get(
        "nodes"
    ) or []
    return str(nodes[0]["name"]).lstrip("#") if nodes else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--write", action="store_true", help="también crea un cupón (escribe)")
    args = parser.parse_args()
    load_dotenv()
    from app.connectors import shopify, shopify_auth

    missing = [name for name in NEEDED if not os.environ.get(name)]
    if missing:
        print(SETUP.format(missing=", ".join(missing), scopes=",".join(shopify_auth.SCOPES)))
        return 2
    url = os.environ.get("DATABASE_URL_STAGING")
    if not url:
        print("Falta DATABASE_URL_STAGING en .env: la prueba no corre contra la base de la demo.")
        return 2
    store = shopify.clean_shop(os.environ["SHOPIFY_TEST_STORE"])
    print(f"Tienda: {store}")
    ok = True

    print("1. Token con Client ID y secret")
    try:
        creds = shopify_auth.client_credentials(
            store, os.environ["SHOPIFY_TEST_CLIENT_ID"], os.environ["SHOPIFY_TEST_CLIENT_SECRET"]
        )
    except ValueError as error:
        from app.companies import deploy

        code = getattr(error, "code", "")
        found = deploy.explain({"ok": False, "codigo": code}, "shopify")
        _step(False, str(error))
        if found:
            print(f"    Qué falta: {found.missing} · {found.what}\n    Qué hacer: {found.todo}")
        return 1
    ok &= _step(bool(creds.access_token), "token recibido (dura 24 horas)")

    print("2. Permisos de la app")
    lacking = shopify_auth.missing_scopes(list(creds.scopes))
    ok &= _step(not lacking, "tiene todos" if not lacking else f"faltan: {', '.join(lacking)}")

    print("3. Conectar, probar y sumar los dominios (como Deploy)")
    from app.channels.web import widget
    from app.companies import connection as system
    from app.companies import deploy
    from app.companies.create import create_company
    from app.connectors import platforms
    from app.db.connection import connect
    from app.db.migrate import migrate

    migrate(url)
    conn = connect(url)
    try:
        company = create_company(
            conn, "Shopify E2E", "e2e@shopify-e2e.example", "clave-e2e-1234567"
        )
        platforms.connect(conn, company.tenant_id, "shopify", store, credentials=creds)
        result = system.test_connection(conn, company.tenant_id)
        found = deploy.explain(result, "shopify")
        ok &= _step(bool(result.get("ok")), found.what if found else str(result))
        if found and not found.ok:
            print(f"    Qué hacer: {found.todo}")
        added = deploy.add_store_domains(conn, company.tenant_id, result)
        domains = widget.load(conn, company.tenant_id).domains
        ok &= _step(store in domains, f"dominios del chat: {', '.join(domains) or '—'}")
        if added:
            print(f"    (agregados solos: {', '.join(added)})")

        print("4. Consultas del agente por la API canónica")
        client = platforms.client(f"shopify:{store}", creds.dump())
        number = _first_order(store, creds.access_token)
        if number is None:
            _step(False, "la tienda no tiene pedidos: creá uno de prueba (Orders → Create order)")
            ok = False
        else:
            order = client.get(f"/orders/{number}")
            body = order.json()
            ok &= _step(
                order.status_code == 200,
                f"pedido {number}: {body.get('status') or body.get('message')}",
            )
        products = client.get("/products", params={"query": ""})
        count = len(products.json().get("products") or []) if products.status_code == 200 else 0
        ok &= _step(products.status_code == 200, f"productos encontrados: {count}")

        if args.write and number is not None:
            print("5. Escritura: un cupón de un solo uso (queda creado en la tienda)")
            coupon = client.post(f"/orders/{number}/coupons", json={"percent": 5})
            ok &= _step(coupon.status_code in (200, 201), f"cupón: {coupon.json()}")
    finally:
        conn.rollback()
        conn.close()
    print("Resultado:", "todo bien" if ok else "hay pasos que fallaron (ver arriba)")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
