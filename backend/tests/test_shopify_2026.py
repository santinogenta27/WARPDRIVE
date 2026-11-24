"""Test de aceptación: la conexión con Shopify como funciona en 2026 (decisión 067).

1. Autenticación: credenciales de una app del Dev Dashboard (client credentials, token de 24 h
   renovado solo antes de que venza), instalación por OAuth (HMAC y state) y el token pegado a
   mano de una app legacy.
2. El conector habla con la Admin GraphQL API: pedido con envío y seguimiento, reembolso,
   código de descuento único y cancelación antes del despacho, con los mismos controles.
3. Configuración → Sistema: elegir el método, probar la conexión y ver errores claros.
4. La guía para crear la app en el Dev Dashboard.
"""

import hashlib
import hmac
import json
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from shopify_admin import ALL_SCOPES, SHOP, UNSHIPPED, FakeShopify

from app.connectors import platforms, shopify, shopify_auth
from app.tools.runtime import ToolRuntime

NOW = 1_800_000_000.0


def _runtime(store: FakeShopify, secret: str = "shpat_123") -> ToolRuntime:
    return ToolRuntime(platforms.client(f"shopify:{SHOP}", secret, store.transport))


# -- 1. autenticación ----------------------------------------------------------------------------
def test_client_credentials_pide_el_token_y_lo_renueva_solo_antes_de_que_venza() -> None:
    store = FakeShopify()
    creds = shopify_auth.client_credentials(
        SHOP, "cid_123", "shpss_secreto", transport=store.transport, now=NOW
    )
    sent = store.token_requests[0]
    assert sent == {
        "grant_type": "client_credentials",
        "client_id": "cid_123",
        "client_secret": "shpss_secreto",
    }
    assert creds.method == "client_credentials" and creds.access_token == "shpat_nuevo1"
    assert creds.expires_at == NOW + 86399
    assert set(creds.scopes) == set(ALL_SCOPES)

    # Recién pedido: no se renueva. A diez minutos de vencer, sí.
    same = shopify_auth.fresh(SHOP, creds, transport=store.transport, now=NOW + 3600)
    assert same is creds and len(store.token_requests) == 1
    renewed = shopify_auth.fresh(SHOP, creds, transport=store.transport, now=NOW + 86399 - 600)
    assert renewed.access_token == "shpat_nuevo2" and len(store.token_requests) == 2


def test_client_credentials_con_un_secreto_equivocado_da_un_error_claro() -> None:
    store = FakeShopify()
    with pytest.raises(ValueError, match="Client ID o el Client Secret"):
        shopify_auth.client_credentials(SHOP, "cid_123", "otro", transport=store.transport)


def test_oauth_arma_la_instalacion_con_los_scopes_minimos_y_verifica_hmac() -> None:
    url = shopify_auth.authorize_url(SHOP, "cid_app", "https://app.example/cb", "estado-1")
    parts = urlsplit(url)
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert parts.netloc == SHOP and parts.path == "/admin/oauth/authorize"
    assert query["scope"] == ",".join(shopify_auth.SCOPES)
    assert query["state"] == "estado-1" and query["redirect_uri"] == "https://app.example/cb"
    assert "write_products" not in query["scope"]  # nada más que lo que el conector usa

    # El ejemplo de la documentación: HMAC-SHA256 en hexadecimal de los parámetros ordenados.
    params = {
        "code": "0907a61c0c8d55e99db179b68161bc00",
        "shop": SHOP,
        "state": "estado-1",
        "timestamp": "1337178173",
    }
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
    signed = params | {"hmac": hmac.new(b"shpss_app", message.encode(), hashlib.sha256).hexdigest()}
    assert shopify_auth.valid_hmac(signed, "shpss_app")
    assert not shopify_auth.valid_hmac(signed | {"shop": "otra.myshopify.com"}, "shpss_app")
    assert not shopify_auth.valid_hmac(params, "shpss_app")


def test_oauth_cambia_el_codigo_por_un_token_que_vence_y_se_renueva() -> None:
    store = FakeShopify()
    creds = shopify_auth.exchange_code(
        SHOP, "codigo", "cid_app", "shpss_app", transport=store.transport, now=NOW
    )
    assert store.token_requests[0]["expiring"] == "1"
    assert creds.method == "oauth" and creds.refresh_token == "shprt_1"
    assert creds.expires_at == NOW + 3600
    renewed = shopify_auth.fresh(
        SHOP, creds, transport=store.transport, now=NOW + 3500, app=("cid_app", "shpss_app")
    )
    assert store.token_requests[1]["grant_type"] == "refresh_token"
    assert store.token_requests[1]["refresh_token"] == "shprt_1"
    assert renewed.refresh_token == "shprt_2"


def test_el_token_pegado_de_una_app_legacy_sigue_andando() -> None:
    creds = shopify_auth.parse("shpat_123")
    assert creds.method == "token" and creds.access_token == "shpat_123"
    assert creds.expires_at is None
    assert shopify_auth.fresh(SHOP, creds, now=NOW) is creds  # nunca se renueva
    found = _runtime(FakeShopify()).call("GET", "/orders/{order_id}", {"order_id": "1001"})
    assert found.ok


# -- 2. GraphQL ----------------------------------------------------------------------------------
def test_el_pedido_trae_el_envio_y_el_seguimiento_por_graphql() -> None:
    store = FakeShopify()
    found = _runtime(store).call("GET", "/orders/{order_id}", {"order_id": "1001"})
    assert found.ok
    order = found.data
    assert order["id"] == "1001" and order["status"] == "shipped" and order["total"] == 45000.0
    assert (order["carrier"], order["tracking_number"]) == ("Andreani", "AR123")
    assert order["shipped_at"] == "2026-11-11T12:00:00Z"
    assert store.call("Pedido")["variables"] == {"q": "name:1001"}
    assert _runtime(store).call("GET", "/orders/{order_id}", {"order_id": "9"}).status_code == 404


def test_reembolso_con_refund_create_e_idempotencia() -> None:
    store = FakeShopify()
    refund = _runtime(store).call(
        "POST", "/orders/{order_id}/refunds", {"order_id": "1001", "amount": 45000}, key="k1"
    )
    assert refund.status_code == 201 and refund.data["amount"] == 45000
    sent = store.call("Reembolso")
    assert "@idempotent(key: $key)" in sent["query"]
    assert sent["variables"]["key"] == "k1"
    assert sent["variables"]["input"]["transactions"] == [
        {
            "orderId": "gid://shopify/Order/450789469",
            "parentId": "gid://shopify/OrderTransaction/389404469",
            "kind": "REFUND",
            "gateway": "mercado_pago",
            "amount": "45000.00",
        }
    ]


def test_cupon_es_un_codigo_de_descuento_de_un_solo_uso() -> None:
    store = FakeShopify()
    coupon = _runtime(store).call(
        "POST", "/orders/{order_id}/coupons", {"order_id": "1001", "percent": 10}
    )
    assert coupon.status_code == 201 and coupon.data["code"].startswith("GRACIAS-")
    discount = store.call("Descuento")["variables"]["d"]
    assert discount["code"] == coupon.data["code"]
    assert discount["usageLimit"] == 1 and discount["appliesOncePerCustomer"] is True
    assert discount["customerGets"] == {"value": {"percentage": 0.1}, "items": {"all": True}}


def test_cancela_un_pedido_antes_del_despacho_y_no_despues() -> None:
    store = FakeShopify()
    runtime = _runtime(store)
    done = runtime.call("POST", "/orders/{order_id}/cancel", {"order_id": "1002"}, key="c1")
    assert done.status_code == 200 and done.data["status"] == "cancelled"
    assert store.call("Cancelar")["variables"]["id"] == UNSHIPPED["id"]
    shipped = runtime.call("POST", "/orders/{order_id}/cancel", {"order_id": "1001"}, key="c2")
    assert shipped.status_code == 409 and shipped.data["code"] == "already_shipped"
    assert store.operations().count("Cancelar") == 1  # al despachado no se le pide nada


def test_todo_va_por_graphql_y_nada_por_rest() -> None:
    store = FakeShopify()
    runtime = _runtime(store)
    runtime.call("GET", "/customers", {"email": "ana@example.com"})
    runtime.call("GET", "/orders/{order_id}", {"order_id": "1001"})
    runtime.call("GET", "/products", {"query": "campera"})
    # La tienda falsa falla con cualquier ruta que no sea /admin/api/2026-10/graphql.json.
    assert store.operations() == ["Clientes", "Pedido", "Productos"]


def test_errores_claros_de_token_vencido_y_permiso_faltante() -> None:
    expired = _runtime(FakeShopify(token="otro")).call(
        "GET", "/orders/{order_id}", {"order_id": "1001"}
    )
    assert expired.status_code == 401 and "venció" in expired.data["message"]
    no_scope = _runtime(FakeShopify(scopes=["read_products"])).call(
        "GET", "/orders/{order_id}", {"order_id": "1001"}
    )
    assert no_scope.status_code == 403 and "read_orders" in no_scope.data["message"]


def test_cancelar_pedido_es_una_herramienta_de_shopify() -> None:
    assert "cancelarPedido" in platforms.OPERATIONS["shopify"]


# -- 3. Configuración → Sistema ------------------------------------------------------------------
@pytest.fixture
def company(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from app.companies.create import create_company
    from app.main import app
    from app.panel import auth
    from app.panel.routes import PanelDeps, panel_deps

    monkeypatch.setenv("PANEL_SECRET", "secreto-de-prueba")
    auth._failures.clear()
    made = create_company(conn, "Tienda Sur", "duena@tiendasur.example", "clave-sur-123")
    store = FakeShopify()
    monkeypatch.setattr(platforms, "UPSTREAM", store.transport)
    from app.companies.connection import runtime_for
    from app.policy.guardian import Guardian

    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=made.tenant_id,
        guardian=lambda c, t: Guardian(c, runtime_for(c, t)[0]),
    )
    client = TestClient(app, base_url="https://app.example")
    login = client.post(
        "/panel/login",
        data={"email": "duena@tiendasur.example", "password": "clave-sur-123"},
        follow_redirects=False,
    )
    assert login.status_code == 303, login.text
    yield client, made.tenant_id, store
    app.dependency_overrides.clear()


def _csrf(client: TestClient) -> str:
    import re

    page = client.get("/panel/deploy").text
    found = re.search(r'name="csrf" value="([^"]+)"', page)
    assert found
    return found.group(1)


def test_sistema_ofrece_los_tres_metodos_y_la_guia(company: Any) -> None:
    client, _, _ = company
    page = client.get("/panel/deploy").text
    assert 'value="app"' in page and 'value="token"' in page
    assert "Dev Dashboard" in page and "Client secret" in page
    assert "write_discounts" in page  # la guía dice qué permisos marcar


def test_conectar_con_client_id_y_secret_guarda_cifrado_y_prueba(
    company: Any, conn: psycopg.Connection
) -> None:
    from app.companies.connection import _secret, load

    client, tenant, store = company
    done = client.post(
        "/panel/configuracion/sistema/plataforma",
        data={
            "csrf": _csrf(client),
            "plataforma": "shopify",
            "metodo": "app",
            "tienda": "mitienda",
            "client_id": "cid_123",
            "client_secret": "shpss_secreto",
        },
        follow_redirects=False,
    )
    assert done.status_code == 303 and "error" not in done.headers["location"], done.headers
    stored = json.loads(_secret(conn, tenant) or "{}")
    assert stored["method"] == "client_credentials" and stored["client_secret"] == "shpss_secreto"
    assert stored["access_token"] == "shpat_nuevo1"
    with conn.cursor() as cur:  # cifrado: el secreto no está en claro en la base
        cur.execute(
            "select credentials_enc::text from system_connections where tenant_id = %s", (tenant,)
        )
        row = cur.fetchone()
    assert row and "shpss_secreto" not in row[0]

    tested = client.post(
        "/panel/configuracion/sistema/probar", data={"csrf": _csrf(client)}, follow_redirects=False
    )
    assert "ok=" in tested.headers["location"], tested.headers
    found = load(conn, tenant)
    assert found is not None and found.status == "tested"
    assert "Permisos" in store.operations()


def test_la_prueba_dice_que_permiso_falta(company: Any, conn: psycopg.Connection) -> None:
    from app.companies.connection import load

    client, tenant, store = company
    store.scopes = ["read_orders", "read_products"]
    platforms.connect(conn, tenant, "shopify", "mitienda", "shpat_nuevo0")
    store.token = "shpat_nuevo0"
    client.post("/panel/configuracion/sistema/probar", data={"csrf": _csrf(client)})
    found = load(conn, tenant)
    assert found is not None and found.last_test is not None
    detail = found.last_test["detalle"]
    assert not found.last_test["ok"]
    assert "write_orders" in detail and "write_discounts" in detail and "read_customers" in detail


def test_la_prueba_dice_que_el_token_vencio(company: Any, conn: psycopg.Connection) -> None:
    from app.companies.connection import load

    client, tenant, _ = company
    platforms.connect(conn, tenant, "shopify", "mitienda", "shpat_viejo")
    client.post("/panel/configuracion/sistema/probar", data={"csrf": _csrf(client)})
    found = load(conn, tenant)
    assert found is not None and found.last_test is not None
    assert not found.last_test["ok"] and "venció" in found.last_test["detalle"]


def test_el_token_de_24_h_se_renueva_y_se_guarda_al_usarlo(
    company: Any, conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.companies.connection import _secret, runtime_for
    from app.crypto import encrypt_for

    _, tenant, store = company
    platforms.connect(
        conn,
        tenant,
        "shopify",
        "mitienda",
        "x",
        method="app",
        client_id="cid_123",
        client_secret="shpss_secreto",
    )
    stored = json.loads(_secret(conn, tenant) or "{}")
    # Simula que pasó casi un día.
    stored["expires_at"] = 1.0
    with conn.cursor() as cur:
        cur.execute(
            "update system_connections set credentials_enc = %s, status = 'active'"
            " where tenant_id = %s",
            (encrypt_for(tenant, json.dumps(stored)), tenant),
        )
    runtime, mode = runtime_for(conn, tenant)
    assert mode == "real"
    assert runtime.call("GET", "/orders/{order_id}", {"order_id": "1001"}).ok
    again = json.loads(_secret(conn, tenant) or "{}")
    assert again["access_token"] == "shpat_nuevo2" and again["expires_at"] > 1.0


def test_oauth_instala_con_state_y_hmac(
    company: Any, conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.companies.connection import _secret, load

    client, tenant, _ = company
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "cid_app")
    monkeypatch.setenv("SHOPIFY_CLIENT_SECRET", "shpss_app")
    start = client.post(
        "/panel/configuracion/sistema/shopify/instalar",
        data={"csrf": _csrf(client), "tienda": "mitienda"},
        follow_redirects=False,
    )
    assert start.status_code == 303
    target = urlsplit(start.headers["location"])
    query = {k: v[0] for k, v in parse_qs(target.query).items()}
    assert target.netloc == SHOP and query["client_id"] == "cid_app"
    assert (
        query["redirect_uri"] == "https://app.example/panel/configuracion/sistema/shopify/callback"
    )

    def signed(state: str, shop: str = SHOP) -> str:
        params = {"code": "codigo", "shop": shop, "state": state, "timestamp": "1800000000"}
        message = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        digest = hmac.new(b"shpss_app", message.encode(), hashlib.sha256).hexdigest()
        return "/panel/configuracion/sistema/shopify/callback?" + urlencode(
            params | {"hmac": digest}
        )

    forged = client.get(signed("otro-state"), follow_redirects=False)
    assert "error=" in forged.headers["location"]
    assert load(conn, tenant) is None

    tampered = signed(query["state"]).replace("code=codigo", "code=otro")
    assert "error=" in client.get(tampered, follow_redirects=False).headers["location"]

    done = client.get(signed(query["state"]), follow_redirects=False)
    assert "ok=" in done.headers["location"], done.headers
    stored = json.loads(_secret(conn, tenant) or "{}")
    assert stored["method"] == "oauth" and stored["refresh_token"].startswith("shprt_")
    assert "client_secret" not in stored  # el de la app vive en el entorno, no por empresa
    found = load(conn, tenant)
    assert found is not None and found.base_url == f"shopify:{SHOP}"
    # El state se usa una sola vez.
    again = client.get(signed(query["state"]), follow_redirects=False)
    assert "error=" in again.headers["location"]


def test_conectar_no_cambia_la_empresa_de_otra_sesion(company: Any) -> None:
    """El state lleva la empresa: una instalación empezada en otra empresa no se guarda acá."""
    client, _, _ = company
    state = shopify_auth.make_state(uuid.uuid4(), SHOP)
    assert shopify_auth.read_state(state, SHOP) is not None
    assert shopify_auth.read_state(state, "otra.myshopify.com") is None
    assert shopify_auth.read_state(state + "x", SHOP) is None


def test_el_transporte_usa_el_token_de_las_credenciales_guardadas() -> None:
    store = FakeShopify(token="shpat_guardado")
    creds = shopify_auth.Credentials(method="token", access_token="shpat_guardado")
    runtime = ToolRuntime(platforms.client(f"shopify:{SHOP}", creds.dump(), store.transport))
    assert runtime.call("GET", "/orders/{order_id}", {"order_id": "1001"}).ok
    assert isinstance(shopify.API_VERSION, str) and shopify.API_VERSION == "2026-10"


def test_el_cliente_http_usa_httpx() -> None:
    assert isinstance(platforms.client(f"shopify:{SHOP}", "shpat_123"), httpx.Client)
