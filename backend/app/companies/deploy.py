"""Deploy: los cinco pasos para poner el agente en marcha (Hito 13, tarea 4; decisión 086).

1. Conectar la tienda (Shopify, Tiendanube u otra con su API).
2. Elegir qué acciones puede hacer el agente y sus límites.
3. Instalar el chat en el sitio: los dominios de la tienda conectada se agregan solos a los
   permitidos, y "Check installation" confirma que el chat cargó desde uno de ellos.
4. WhatsApp (decisión 068).
5. Salir en vivo (decisión 056) y poder pausar.

Este módulo calcula el estado de cada paso, explica una prueba de conexión fallida en términos
de qué falta (un permiso, una credencial o la tienda) y qué hacer, y revisa la instalación.
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx
import psycopg

from app import net
from app.channels.web import widget
from app.channels.whatsapp import selfservice as whatsapp
from app.companies import connection as system
from app.companies import first_steps, go_live
from app.db.connection import tenant_scope

# -- qué falta, en una prueba de conexión --------------------------------------------------------
PERMISSION, CREDENTIAL, STORE, SYSTEM = "permission", "credential", "store", "system"
CODES = {
    "missing_scope": PERMISSION,
    "token_invalid": CREDENTIAL,
    "forbidden": CREDENTIAL,
    "invalid_client": CREDENTIAL,
    "application_cannot_be_found": CREDENTIAL,
    "shop_not_permitted": STORE,
    "shop_frozen": STORE,
    "store_not_found": STORE,
    "shop_unreachable": STORE,
}


@dataclass(frozen=True)
class Diagnosis:
    ok: bool
    missing: str | None  # PERMISSION, CREDENTIAL, STORE o SYSTEM
    what: str  # qué pasó, en una línea
    todo: str  # qué hacer


def explain(result: dict[str, Any] | None, platform: str) -> Diagnosis | None:
    """La prueba de conexión en palabras simples: qué falta y qué hacer."""
    if not result:
        return None
    store = result.get("tienda")
    if result.get("ok"):
        name = f" {store}" if store else " your store"
        return Diagnosis(True, None, f"Connected to{name}.", "")
    code = str(result.get("codigo") or "")
    status = result.get("status")
    kind = CODES.get(code)
    if kind is None:
        kind = CREDENTIAL if status in (401, 403) else STORE if status is None else SYSTEM
    name = {"shopify": "Shopify", "tiendanube": "Tiendanube"}.get(platform, "Your system")
    if kind == PERMISSION:
        scopes = ", ".join(result.get("faltan") or []) or "a permission"
        return Diagnosis(
            False,
            PERMISSION,
            f"Your Shopify app is missing these permissions: {scopes}.",
            "In the Dev Dashboard, open your app → Versions, add them under Scopes and click "
            "Release. Then click Connect again here.",
        )
    if kind == CREDENTIAL:
        todo = {
            "shopify": "Copy the Client ID and Client secret again from your app's Settings in "
            "the Dev Dashboard, check the app is installed on this store, and connect again.",
            "tiendanube": "Create a new access token for your Tiendanube app and paste it again.",
        }.get(platform, "Check the credential you saved for your system and save it again.")
        return Diagnosis(False, CREDENTIAL, f"{name} didn't accept the credentials.", todo)
    if kind == STORE:
        if code == "shop_not_permitted":
            return Diagnosis(
                False,
                STORE,
                "This store isn't in the same Shopify organization as your app.",
                "Create the app from the Dev Dashboard of the organization that owns the store, "
                "or use Install with Shopify.",
            )
        if code == "shop_frozen":
            return Diagnosis(
                False, STORE, "The Shopify store is paused or unpaid.", "Reactivate the store."
            )
        return Diagnosis(
            False,
            STORE,
            f"We couldn't find the store on {name}.",
            "Check the store address (yourstore.myshopify.com) or the store number, and connect "
            "again."
            if platform in ("shopify", "tiendanube")
            else "Check the base URL of your system.",
        )
    return Diagnosis(
        False,
        SYSTEM,
        f"{name} answered with an error ({status}).",
        "Try again in a few minutes. If it keeps failing, check the base URL.",
    )


def platform_of(found: system.SystemConnection | None) -> str:
    base = (found.base_url or "") if found else ""
    prefix = base.split(":", 1)[0]
    if prefix in ("shopify", "tiendanube"):
        return prefix
    return "api" if found and (found.openapi or base) else ""


def add_store_domains(
    conn: psycopg.Connection, tenant_id: uuid.UUID, result: dict[str, Any]
) -> list[str]:
    """Los dominios de la tienda conectada pasan a los permitidos del chat (sin borrar los que
    ya estaban). Devuelve los que se agregaron."""
    hosts = [h for h in result.get("dominios") or [] if isinstance(h, str)]
    return widget.add_domains(conn, tenant_id, hosts) if result.get("ok") and hosts else []


# -- revisar la instalación ----------------------------------------------------------------------
@dataclass(frozen=True)
class InstallCheck:
    status: str  # "loaded", "found", "missing", "no_domains" o "unreachable"
    domain: str = ""


def check_install(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    slug: str,
    *,
    fetch: Any = None,
) -> InstallCheck:
    """¿El chat cargó desde uno de los dominios de la empresa? Si ya cargó, listo. Si no, se lee
    la página de inicio de cada dominio (solo direcciones públicas) y se busca el código: puede
    estar pegado pero sin haberse abierto todavía, o no estar."""
    seen = first_steps.widget_seen(conn, tenant_id)
    if seen:
        return InstallCheck("loaded", seen)
    domains = [d for d in widget.load(conn, tenant_id).domains if not d.startswith("*.")]
    domains = [d for d in domains if d != "localhost"][:3]
    if not domains:
        return InstallCheck("no_domains")
    get = fetch or _fetch
    pattern = re.compile(rf'data-chat=["\']{re.escape(slug)}["\']')
    reached = ""
    for domain in domains:
        try:
            page = get(f"https://{domain}/")
        except (httpx.HTTPError, net.UnsafeURL, net.ResponseTooLarge, ValueError):
            continue
        reached = reached or domain
        if pattern.search(page):
            return InstallCheck("found", domain)
    return InstallCheck("missing", reached) if reached else InstallCheck("unreachable", domains[0])


def _fetch(url: str) -> str:
    with net.safe_client(max_bytes=2 * 1024 * 1024, follow_redirects=True) as client:
        response = client.get(url, timeout=10.0)
        response.raise_for_status()
        return response.text


# -- estado de cada paso -------------------------------------------------------------------------
@dataclass(frozen=True)
class Step:
    key: str
    state: str  # "done", "progress", "todo", "optional"
    label: str  # lo que dice el estado


def steps(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[Step]:
    from app.companies import review

    mode = system.mode_for(conn, tenant_id)
    store = {
        "real": Step("store", "done", "Connected"),
        "demo": Step("store", "done", "Demo store"),
        "simulado": Step("store", "progress", "Test mode"),
    }.get(mode, Step("store", "todo", "Not connected"))
    tools = review.tools(conn, tenant_id)
    approved = sum(t.approved for t in tools)
    if approved:
        actions = Step("actions", "done", f"{approved} of {len(tools)} on")
    elif tools:
        actions = Step("actions", "todo", "None on yet")
    else:
        actions = Step("actions", "todo", "Connect your store first")
    seen = first_steps.widget_seen(conn, tenant_id)
    site = (
        Step("website", "done", "Installed") if seen else Step("website", "todo", "Not installed")
    )
    setup = whatsapp.load(conn, tenant_id)
    wa = (
        Step("whatsapp", "done", "Connected")
        if setup and setup.status == "active"
        else Step("whatsapp", "progress", "Not active")
        if setup
        else Step("whatsapp", "optional", "Optional")
    )
    found = go_live.readiness(conn, tenant_id)
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select settings ? 'live' from tenants where id = %s", (tenant_id,)
        ).fetchone()
    was_live = bool(row and row[0])
    live = (
        Step("live", "done", "Live")
        if found.live
        else Step("live", "progress", "Paused")
        if was_live
        else Step("live", "progress", "Ready")
        if found.ready
        else Step("live", "todo", "Not ready")
    )
    return [store, actions, site, wa, live]


def host_of(origin: str) -> str:
    try:
        return (urlsplit(origin).hostname or "").lower()
    except ValueError:
        return ""
