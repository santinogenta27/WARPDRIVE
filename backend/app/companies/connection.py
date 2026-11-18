"""Conexión con el sistema real de la empresa (decisión 026): su OpenAPI, la URL base y las
credenciales, con una prueba de conexión antes de activar.

Estados: `draft` (cargada) → `tested` (la prueba pasó) → `active` (el agente la usa). Cualquier
cambio en la URL, las credenciales o el OpenAPI vuelve la conexión a `draft`: lo que se activa es
siempre lo que se probó. Mientras no está activa, las herramientas de la empresa responden en
modo simulado (datos armados desde su OpenAPI), sin tocar su sistema.

Las credenciales se cifran con una clave derivada por empresa y nunca se muestran de nuevo. La
URL base tiene que ser HTTPS y pública: el servidor no hace pedidos a direcciones internas.
"""

import ipaddress
import json
import os
import re
import socket
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
import psycopg
import yaml
from psycopg.types.json import Jsonb

from app.crypto import decrypt_for, encrypt_for
from app.db.connection import tenant_scope
from app.tools.runtime import HttpClient, ToolRuntime

AUTH_KINDS = ("none", "bearer", "api_key", "basic")
MAX_OPENAPI_BYTES = 2_000_000
DEMO_BASE_URL = "demo:acme"  # la API simulada de Acme, solo para la demo
DEMO_PREFIX = "demo:"  # cualquier API simulada de una empresa de demo (demo:northwind)
PHONE_FIELDS = ("phone", "telefono", "customer_phone", "celular")
Resolver = Callable[[str], list[str]]
ClientFactory = Callable[["SystemConnection", str | None], HttpClient]


class ConnectionError_(ValueError):
    """Dato inválido de la conexión: el mensaje se le muestra a quien la carga."""


@dataclass(frozen=True)
class SystemConnection:
    tenant_id: uuid.UUID
    openapi: dict[str, Any] | None
    base_url: str | None
    auth_kind: str
    auth_header: str | None
    has_credentials: bool
    status: str
    last_test: dict[str, Any] | None

    @property
    def operations(self) -> int:
        paths = (self.openapi or {}).get("paths") or {}
        return sum(1 for methods in paths.values() for m in methods if m.lower() in _METHODS)


_METHODS = {"get", "post", "put", "patch", "delete"}


def parse_openapi(text: str) -> dict[str, Any]:
    """OpenAPI en JSON o YAML. Tiene que tener al menos una operación."""
    if len(text.encode()) > MAX_OPENAPI_BYTES:
        raise ConnectionError_("El OpenAPI supera los 2 MB.")
    try:
        spec = json.loads(text) if text.lstrip().startswith("{") else yaml.safe_load(text)
    except (ValueError, yaml.YAMLError) as error:
        raise ConnectionError_("El OpenAPI no es un JSON ni un YAML válido.") from error
    if not isinstance(spec, dict) or not isinstance(spec.get("paths"), dict) or not spec["paths"]:
        raise ConnectionError_("El OpenAPI no tiene operaciones (falta `paths`).")
    return spec


def _allow_private() -> bool:
    """Solo para desarrollo local (probar contra una API en localhost)."""
    return os.environ.get("ALLOW_PRIVATE_SYSTEM_URLS", "").strip() == "1"


def _public(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    return ip.is_global and not ip.is_multicast


def validate_base_url(url: str) -> str:
    """URL base normalizada. HTTPS, con host y sin usuario ni clave embebidos."""
    parts = urlsplit(url.strip())
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise ConnectionError_("La URL base tiene que empezar con https:// y tener un dominio.")
    if parts.username or parts.password:
        raise ConnectionError_("La URL base no lleva usuario ni clave: van en las credenciales.")
    if parts.query or parts.fragment:
        raise ConnectionError_("La URL base no lleva parámetros.")
    if not _allow_private():
        if parts.scheme != "https":
            raise ConnectionError_("La URL base tiene que ser HTTPS.")
        try:
            literal = _public(parts.hostname)
        except ValueError:
            literal = parts.hostname.lower() not in ("localhost",) and "." in parts.hostname
        if not literal:
            raise ConnectionError_("La URL base tiene que ser una dirección pública.")
    return url.strip().rstrip("/")


def _resolve(host: str) -> list[str]:
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]


def check_public_host(base_url: str, resolver: Resolver = _resolve) -> None:
    """Antes de conectar: el dominio tiene que resolver solo a direcciones públicas."""
    if _allow_private():
        return
    host = urlsplit(base_url).hostname or ""
    try:
        addresses = resolver(host)
    except OSError as error:
        raise ConnectionError_(f"No se pudo resolver el dominio {host}.") from error
    if not addresses or not all(_public(a) for a in addresses):
        raise ConnectionError_("La URL base apunta a una dirección interna.")


def find_identity_lookup(spec: dict[str, Any]) -> dict[str, Any] | None:
    """La operación que busca un cliente por email (para verificar la identidad por código): un
    GET con parámetro `email` cuya respuesta trae una lista de clientes con teléfono."""
    for path, methods in (spec.get("paths") or {}).items():
        op = methods.get("get") if isinstance(methods, dict) else None
        if not op or "{" in path:
            continue
        params = [_deref(spec, p) for p in op.get("parameters") or []]
        if not any(p.get("in") == "query" and p.get("name") == "email" for p in params):
            continue
        ok = _deref(spec, (op.get("responses") or {}).get("200") or {})
        content = (ok.get("content") or {}).get("application/json") or {}
        schema = _deref(spec, content.get("schema") or {})
        for name, prop in (schema.get("properties") or {}).items():
            prop = _deref(spec, prop)
            if prop.get("type") != "array":
                continue
            item = _deref(spec, prop.get("items") or {})
            phone = next((f for f in PHONE_FIELDS if f in (item.get("properties") or {})), None)
            if phone:
                return {
                    "method": "GET",
                    "path": path,
                    "email_param": "email",
                    "list_field": name,
                    "phone_field": phone,
                }
    return None


def _deref(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while isinstance(node, dict) and "$ref" in node:
        target: Any = spec
        for part in str(node["$ref"]).removeprefix("#/").split("/"):
            target = target[part]
        node = target
    return node if isinstance(node, dict) else {}


# -- guardar y leer ---------------------------------------------------------------------------
def load(conn: psycopg.Connection, tenant_id: uuid.UUID) -> SystemConnection | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select openapi, base_url, auth_kind, auth_header, credentials_enc is not null,"
            " status, last_test from system_connections where tenant_id = %s",
            (tenant_id,),
        ).fetchone()
    return SystemConnection(tenant_id, *row) if row else None


def save_openapi(conn: psycopg.Connection, tenant_id: uuid.UUID, text: str) -> SystemConnection:
    """Guarda el OpenAPI de la empresa. Si trae la búsqueda de clientes por email, queda como la
    forma de verificar la identidad. La conexión vuelve a `draft`."""
    spec = parse_openapi(text)
    lookup = find_identity_lookup(spec)
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into system_connections (tenant_id, openapi) values (%s, %s)"
            " on conflict (tenant_id) do update set openapi = excluded.openapi,"
            " status = 'draft', last_test = null, updated_at = now()",
            (tenant_id, Jsonb(spec)),
        )
        if lookup is not None:
            conn.execute(
                "update tenants set settings = settings || %s where id = %s",
                (Jsonb({"identity_lookup": lookup}), tenant_id),
            )
    found = load(conn, tenant_id)
    assert found is not None
    return found


def save_access(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    base_url: str,
    auth_kind: str,
    auth_header: str | None = None,
    secret: str | None = None,
) -> SystemConnection:
    """Guarda la URL base y las credenciales (cifradas para esta empresa). Sin `secret`, se
    conservan las credenciales ya guardadas. La conexión vuelve a `draft`: hay que probarla."""
    url = validate_base_url(base_url)
    if auth_kind not in AUTH_KINDS:
        raise ConnectionError_("Tipo de autenticación desconocido.")
    header = (auth_header or "").strip() or None
    if auth_kind == "api_key":
        if not header or not header.replace("-", "").replace("_", "").isalnum():
            raise ConnectionError_("Falta el nombre del encabezado de la clave de API.")
    else:
        header = None
    if auth_kind == "basic" and secret and ":" not in secret:
        raise ConnectionError_("Para autenticación básica, la credencial es usuario:clave.")
    encrypted = encrypt_for(tenant_id, secret) if secret else None
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into system_connections (tenant_id, base_url, auth_kind, auth_header,"
            " credentials_enc) values (%s, %s, %s, %s, %s)"
            " on conflict (tenant_id) do update set base_url = excluded.base_url,"
            " auth_kind = excluded.auth_kind, auth_header = excluded.auth_header,"
            " credentials_enc = case when excluded.auth_kind = 'none' then null"
            "  else coalesce(excluded.credentials_enc, system_connections.credentials_enc) end,"
            " status = 'draft', last_test = null, updated_at = now()",
            (tenant_id, url, auth_kind, header, encrypted),
        )
    found = load(conn, tenant_id)
    assert found is not None
    if found.auth_kind != "none" and not found.has_credentials:
        raise ConnectionError_("Faltan las credenciales.")
    return found


def _secret(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select credentials_enc from system_connections where tenant_id = %s", (tenant_id,)
        ).fetchone()
    return decrypt_for(tenant_id, row[0]) if row and row[0] else None


def auth_headers(connection: SystemConnection, secret: str | None) -> dict[str, str]:
    if connection.auth_kind == "none" or not secret:
        return {}
    if connection.auth_kind == "bearer":
        return {"Authorization": f"Bearer {secret}"}
    if connection.auth_kind == "api_key":
        return {str(connection.auth_header): secret}
    return {}  # basic: va en `auth` del cliente


def http_client(connection: SystemConnection, secret: str | None) -> HttpClient:
    """Cliente HTTP hacia el sistema de la empresa. No sigue redirecciones (podrían llevar a
    una dirección interna) y verifica que el dominio sea público."""
    assert connection.base_url is not None
    from app.connectors import platforms

    if platforms.is_platform(connection.base_url):
        # Shopify o Tiendanube (decisión 057): el conector habla con su API, siempre HTTPS a su
        # dominio, y traduce la API canónica de tienda.
        return platforms.client(connection.base_url, secret)
    check_public_host(connection.base_url)
    basic = None
    if connection.auth_kind == "basic" and secret:
        user, _, password = secret.partition(":")
        basic = httpx.BasicAuth(user, password)
    return httpx.Client(
        base_url=connection.base_url,
        headers=auth_headers(connection, secret),
        auth=basic,
        timeout=15.0,
        follow_redirects=False,
    )


# -- prueba de conexión y activación ----------------------------------------------------------
def _probe(
    spec: dict[str, Any] | None, lookup: dict[str, Any] | None
) -> tuple[str, dict[str, str]]:
    """Qué pedido de solo lectura se usa para probar: la búsqueda de clientes por email (con un
    email que no existe), un GET sin parámetros obligatorios o, si no hay, la raíz."""
    if lookup:
        return str(lookup["path"]), {str(lookup.get("email_param", "email")): "prueba@example.com"}
    for path, methods in ((spec or {}).get("paths") or {}).items():
        op = methods.get("get") if isinstance(methods, dict) else None
        if not op or "{" in path:
            continue
        params = [_deref(spec or {}, p) for p in op.get("parameters") or []]
        if not any(p.get("required") for p in params):
            return str(path), {}
    return "/", {}


def test_connection(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    client_factory: ClientFactory = http_client,
) -> dict[str, Any]:
    """Hace un pedido de solo lectura al sistema de la empresa con sus credenciales y guarda el
    resultado. Pasa si el sistema responde y acepta las credenciales."""
    connection = load(conn, tenant_id)
    if connection is None or not connection.base_url:
        raise ConnectionError_("Primero cargá la URL base del sistema.")
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings -> 'identity_lookup' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    lookup = row[0] if row and isinstance(row[0], dict) else None
    path, params = _probe(connection.openapi, lookup)
    started = time.perf_counter()
    result: dict[str, Any] = {"operacion": f"GET {path}"}
    try:
        client = client_factory(connection, _secret(conn, tenant_id))
        response = client.request("GET", path, params=params or None, timeout=10.0)
    except ConnectionError_ as error:
        result |= {"ok": False, "detalle": str(error)}
    except Exception as error:  # noqa: BLE001 — cualquier falla de red es "no responde"
        result |= {"ok": False, "detalle": f"El sistema no respondió ({type(error).__name__})."}
    else:
        status = response.status_code
        result["status"] = status
        if status in (401, 403):
            result |= {"ok": False, "detalle": "El sistema rechazó las credenciales."}
        elif 300 <= status < 400:
            result |= {"ok": False, "detalle": "El sistema redirige: revisá la URL base."}
        elif status >= 500:
            result |= {"ok": False, "detalle": f"El sistema respondió con un error ({status})."}
        else:
            result |= {"ok": True, "detalle": "El sistema respondió y aceptó las credenciales."}
    result["ms"] = round((time.perf_counter() - started) * 1000)
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update system_connections set last_test = %s, tested_at = now(), updated_at = now(),"
            " status = case when %s then 'tested' else 'draft' end where tenant_id = %s",
            (Jsonb(result), bool(result["ok"]), tenant_id),
        )
    return result


def probe_tools(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    sample: str,
    *,
    client_factory: ClientFactory = http_client,
) -> list[dict[str, Any]]:
    """Prueba cada herramienta de solo lectura contra el sistema de la empresa con un dato de
    ejemplo (un número de pedido), como la prueba de conexión: un pedido GET y nada más. Las que
    modifican algo no se prueban contra el sistema real (decisión 057)."""
    from app.companies import review

    connection = load(conn, tenant_id)
    if connection is None or not connection.base_url:
        raise ConnectionError_("Primero cargá la URL base del sistema.")
    client = client_factory(connection, _secret(conn, tenant_id))
    found: list[dict[str, Any]] = []
    for tool in review.tools(conn, tenant_id):
        upstream = tool.manifest["upstream"]
        row: dict[str, Any] = {"herramienta": tool.name, "operacion": upstream["path"]}
        if upstream["method"] != "GET":
            found.append(row | {"ok": None, "detalle": "Modifica datos: no se prueba acá."})
            continue
        fields = re.findall(r"{(\w+)}", upstream["path"])
        path = upstream["path"].format(**{f: quote(sample.strip(), safe="") for f in fields})
        try:
            response = client.request("GET", path, timeout=10.0)
        except Exception as error:  # noqa: BLE001 — cualquier falla de red es "no responde"
            found.append(row | {"ok": False, "detalle": f"No respondió ({type(error).__name__})."})
            continue
        status = response.status_code
        detail = (
            "Respondió bien."
            if status == 200
            else "El sistema no encontró ese dato de ejemplo."
            if status == 404
            else "El sistema rechazó las credenciales."
            if status in (401, 403)
            else f"Respondió con un error ({status})."
        )
        found.append(row | {"ok": status == 200, "detalle": detail})
    return found


def activate(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    """Pasa el agente de la empresa del modo simulado a su sistema real. Solo con la prueba de
    conexión aprobada."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "update system_connections set status = 'active', activated_at = now(),"
            " updated_at = now() where tenant_id = %s and status in ('tested', 'active')"
            " returning 1",
            (tenant_id,),
        ).fetchone()
    if row is None:
        raise ConnectionError_("Primero tiene que pasar la prueba de conexión.")


def deactivate(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    """Vuelve al modo simulado sin perder la conexión probada."""
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update system_connections set status = 'tested', updated_at = now()"
            " where tenant_id = %s and status = 'active'",
            (tenant_id,),
        )


def connect_demo(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    spec: dict[str, Any],
    *,
    base_url: str = DEMO_BASE_URL,
) -> None:
    """Empresas de demo (Acme y Northwind): su "sistema" es una API simulada en proceso. Queda
    activa, sin credenciales."""
    lookup = find_identity_lookup(spec)
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "insert into system_connections (tenant_id, openapi, base_url, status, activated_at)"
            " values (%s, %s, %s, 'active', now())"
            " on conflict (tenant_id) do update set openapi = excluded.openapi,"
            " base_url = excluded.base_url, status = 'active', updated_at = now()",
            (tenant_id, Jsonb(spec), base_url),
        )
        if lookup is not None:
            conn.execute(
                "update tenants set settings = settings || %s where id = %s",
                (Jsonb({"identity_lookup": lookup}), tenant_id),
            )


# -- runtime del agente -----------------------------------------------------------------------
class NoSystemRuntime(ToolRuntime):
    """La empresa no conectó ningún sistema: toda herramienta falla y el agente deriva."""

    def __init__(self) -> None:
        super().__init__(httpx.Client())

    def call(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("La empresa no tiene un sistema conectado.")


def mode_for(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str:
    """A qué sistema llegan las herramientas de la empresa: `demo` (API simulada de Acme),
    `real` (su sistema, con la conexión activa), `simulado` (respuestas armadas desde su
    OpenAPI, hasta que active la conexión) o `sin_sistema`."""
    connection = load(conn, tenant_id)
    if connection is None:
        return "demo" if _is_demo(conn, tenant_id) else "sin_sistema"
    if (connection.base_url or "").startswith(DEMO_PREFIX):
        return "demo"
    if connection.status == "active" and connection.base_url:
        return "real"
    return "simulado" if connection.openapi else "sin_sistema"


def runtime_for(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    demo: Callable[[], HttpClient] | None = None,
    demos: Mapping[str, Callable[[], HttpClient]] | None = None,
    client_factory: ClientFactory = http_client,
) -> tuple[ToolRuntime, str]:
    """El runtime de herramientas de la empresa y su modo (ver `mode_for`). `demo`: la API
    simulada de Acme; `demos`: las de otras empresas de demo, por su URL (`demo:northwind`).
    Sin ellas, la empresa de demo queda sin sistema: la API simulada es solo para la demo."""
    from app.onboarding.report import SandboxRuntime

    mode = mode_for(conn, tenant_id)
    connection = load(conn, tenant_id)
    if mode == "demo":
        url = (connection.base_url if connection is not None else None) or DEMO_BASE_URL
        factory = demo if url == DEMO_BASE_URL else (demos or {}).get(url)
        return (ToolRuntime(factory()), mode) if factory is not None else (NoSystemRuntime(), mode)
    if mode == "real" and connection is not None:
        return ToolRuntime(client_factory(connection, _secret(conn, tenant_id))), mode
    if mode == "simulado" and connection is not None and connection.openapi:
        return SandboxRuntime(connection.openapi), mode
    return NoSystemRuntime(), "sin_sistema"


def _is_demo(conn: psycopg.Connection, tenant_id: uuid.UUID) -> bool:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings ->> 'demo' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    return bool(row and row[0] == "acme")
