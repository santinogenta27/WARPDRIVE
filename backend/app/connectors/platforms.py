"""Conectar una plataforma de e-commerce sin programar (decisiones 057 y 067).

La empresa elige Shopify o Tiendanube, carga su tienda y sus credenciales, y queda:

* la conexión del sistema con la API canónica de tienda como OpenAPI y una dirección propia
  (`shopify:mitienda.myshopify.com` o `tiendanube:123456`); las credenciales, cifradas para la
  empresa;
* la búsqueda de clientes por email para verificar identidad;
* las herramientas de la tienda generadas del OpenAPI canónico, sin aprobar (la empresa las
  revisa, aprueba y fija sus límites en Configuración, como siempre).

Shopify se conecta con una app del Dev Dashboard (Client ID y Secret), con la instalación por
OAuth o con el token de una app legacy (ver `shopify_auth`). Después se prueba la conexión y se
activa con los mismos botones que cualquier sistema.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app.config import REPO_ROOT
from app.connectors import shopify, shopify_auth, tiendanube
from app.crypto import encrypt_for
from app.db.connection import tenant_scope

PLATFORMS = {"shopify": "Shopify", "tiendanube": "Tiendanube"}
CANONICAL_SPEC = REPO_ROOT / "mocks" / "northwind-api" / "openapi.json"
# Las operaciones de la API canónica que cada plataforma implementa.
OPERATIONS = {
    "shopify": [
        "consultarPedido",
        "iniciarReembolso",
        "ofrecerCupon",
        "cancelarPedido",
        "buscarProductos",
        "armarCarrito",
    ],
    "tiendanube": [
        "consultarPedido",
        "iniciarReembolso",
        "ofrecerCupon",
        "buscarProductos",
        "armarCarrito",
    ],
}
# Solo para tests: el transporte hacia Shopify (tokens y API). En producción, la red.
UPSTREAM: httpx.BaseTransport | None = None


def canonical_spec(path: Path = CANONICAL_SPEC) -> dict[str, Any]:
    spec: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return spec


def address(platform: str, store: str) -> str:
    if platform == "shopify":
        return f"shopify:{shopify.clean_shop(store)}"
    if platform == "tiendanube":
        return f"tiendanube:{tiendanube.clean_store(store)}"
    raise ValueError("Elegí Shopify o Tiendanube.")


def is_platform(base_url: str | None) -> bool:
    return (base_url or "").split(":", 1)[0] in PLATFORMS


def client(base_url: str, secret: str | None, upstream: httpx.BaseTransport | None = None) -> Any:
    """El cliente HTTP del runtime de herramientas para una plataforma conectada."""
    platform, _, store = base_url.partition(":")
    if platform == "shopify":
        return shopify.client(store, secret or "", upstream or UPSTREAM)
    return tiendanube.client(store, secret or "", upstream or UPSTREAM)


def diagnose_path(base_url: str | None) -> str | None:
    """La consulta de la prueba de conexión propia de la plataforma, si tiene una."""
    return shopify.DIAGNOSE_PATH if (base_url or "").startswith("shopify:") else None


def live_secret(
    conn: psycopg.Connection, tenant_id: uuid.UUID, base_url: str | None, secret: str | None
) -> str | None:
    """Las credenciales con un token vigente. El token de Shopify que vence (24 h con Client ID
    y Secret, o el de OAuth) se renueva solo cuando le quedan menos de diez minutos, y el nuevo
    se guarda cifrado. Si Shopify no lo renueva, siguen las de antes: la llamada falla con un
    error claro ("venció")."""
    if not secret or not (base_url or "").startswith("shopify:"):
        return secret
    shop = (base_url or "").partition(":")[2]
    creds = shopify_auth.parse(secret)
    try:
        renewed = shopify_auth.fresh(shop, creds, transport=UPSTREAM)
    except (ValueError, httpx.HTTPError):
        return secret
    if renewed is creds:
        return secret
    text = renewed.dump()
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update system_connections set credentials_enc = %s, updated_at = now()"
            " where tenant_id = %s",
            (encrypt_for(tenant_id, text), tenant_id),
        )
    return text


def shopify_credentials(
    store: str, method: str, *, token: str = "", client_id: str = "", client_secret: str = ""
) -> shopify_auth.Credentials:
    """Las credenciales de Shopify según el método elegido en el panel."""
    if method == "app":
        return shopify_auth.client_credentials(
            shopify.clean_shop(store), client_id, client_secret, transport=UPSTREAM
        )
    token = token.strip()
    if not token or any(c.isspace() for c in token):
        raise ValueError("Falta el token de acceso de la tienda.")
    return shopify_auth.Credentials(method="token", access_token=token)


def connect(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    platform: str,
    store: str,
    token: str = "",
    *,
    method: str = "token",
    client_id: str = "",
    client_secret: str = "",
    credentials: shopify_auth.Credentials | None = None,
) -> None:
    from app.companies.connection import find_identity_lookup
    from app.onboarding.toolgen import build_manifests, store_tools

    target = address(platform, store)
    if platform == "shopify":
        creds = credentials or shopify_credentials(
            store, method, token=token, client_id=client_id, client_secret=client_secret
        )
        # El token pegado se guarda como antes (texto); los que vencen, como JSON.
        secret = creds.access_token if creds.method == "token" else creds.dump()
    else:
        secret = token.strip()
        if not secret or any(c.isspace() for c in secret):
            raise ValueError("Falta el token de acceso de la tienda.")
    spec = canonical_spec()
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into system_connections (tenant_id, openapi, base_url, auth_kind,"
            " credentials_enc, status) values (%s, %s, %s, 'bearer', %s, 'draft')"
            " on conflict (tenant_id) do update set openapi = excluded.openapi,"
            " base_url = excluded.base_url, auth_kind = excluded.auth_kind,"
            " credentials_enc = excluded.credentials_enc, status = 'draft', last_test = null,"
            " updated_at = now()",
            (tenant_id, Jsonb(spec), target, encrypt_for(tenant_id, secret)),
        )
        lookup = find_identity_lookup(spec)
        if lookup is not None:
            conn.execute(
                "update tenants set settings = settings || %s where id = %s",
                (Jsonb({"identity_lookup": lookup}), tenant_id),
            )
    store_tools(conn, tenant_id, build_manifests(spec, {platform: OPERATIONS[platform]}))
