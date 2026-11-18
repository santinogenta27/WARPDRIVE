"""Conectar una plataforma de e-commerce sin programar (decisión 057).

La empresa elige Shopify o Tiendanube, carga su tienda y su token, y queda:

* la conexión del sistema con la API canónica de tienda como OpenAPI y una dirección propia
  (`shopify:mitienda.myshopify.com` o `tiendanube:123456`); el token, cifrado para la empresa;
* la búsqueda de clientes por email para verificar identidad;
* las herramientas de la tienda generadas del OpenAPI canónico, sin aprobar (la empresa las
  revisa, aprueba y fija sus límites en Configuración, como siempre).

Después se prueba la conexión y se activa con los mismos botones que cualquier sistema.
"""

import json
import uuid
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app.config import REPO_ROOT
from app.connectors import shopify, tiendanube
from app.crypto import encrypt_for
from app.db.connection import tenant_scope

PLATFORMS = {"shopify": "Shopify", "tiendanube": "Tiendanube"}
CANONICAL_SPEC = REPO_ROOT / "mocks" / "northwind-api" / "openapi.json"
# Las operaciones de la API canónica que cada plataforma implementa.
OPERATIONS = {
    "shopify": ["consultarPedido", "iniciarReembolso", "ofrecerCupon"],
    "tiendanube": ["consultarPedido", "iniciarReembolso", "ofrecerCupon"],
}


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
        return shopify.client(store, secret or "", upstream)
    return tiendanube.client(store, secret or "", upstream)


def connect(
    conn: psycopg.Connection, tenant_id: uuid.UUID, platform: str, store: str, token: str
) -> None:
    from app.companies.connection import find_identity_lookup
    from app.onboarding.toolgen import build_manifests, store_tools

    target = address(platform, store)
    token = token.strip()
    if not token or any(c.isspace() for c in token):
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
            (tenant_id, Jsonb(spec), target, encrypt_for(tenant_id, token)),
        )
        lookup = find_identity_lookup(spec)
        if lookup is not None:
            conn.execute(
                "update tenants set settings = settings || %s where id = %s",
                (Jsonb({"identity_lookup": lookup}), tenant_id),
            )
    store_tools(conn, tenant_id, build_manifests(spec, {platform: OPERATIONS[platform]}))
