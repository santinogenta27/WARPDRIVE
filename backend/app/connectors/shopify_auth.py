"""Autenticación con Shopify en 2026 (decisión 067).

Desde 2026 el admin de Shopify ya no crea apps propias "legacy". Una tienda se conecta de tres
formas, y las tres terminan en el mismo token de la Admin API:

* **App del Dev Dashboard** (`client_credentials`): la tienda crea su app, nos da el Client ID y
  el Client Secret, y pedimos un token que dura 24 h. Se renueva solo cuando le quedan menos de
  diez minutos. Sirve si la app y la tienda son de la misma organización de Shopify.
* **Instalación por OAuth** (`oauth`, authorization code): la app de la plataforma
  (`SHOPIFY_CLIENT_ID` y `SHOPIFY_CLIENT_SECRET`) se instala en la tienda del cliente con los
  scopes mínimos. El callback verifica el HMAC y el state. El token vence y se renueva con su
  refresh token (`expiring=1`).
* **Token pegado** (`token`): el de una app legacy que la tienda ya tenía. No vence.

Las credenciales se guardan como JSON, cifradas por empresa, en `system_connections`. Un token
pegado antes de esta decisión es texto plano y se sigue leyendo igual.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from typing import Any
from urllib.parse import urlencode

import httpx

from app import net

# Lo mínimo que usa el conector: pedidos (consultar, reembolsar y cancelar), clientes (verificar
# identidad por email), productos e inventario (precios y stock) y descuentos (cupones). Un
# permiso `write_` incluye su `read_`.
SCOPES = ("read_customers", "write_orders", "read_products", "read_inventory", "write_discounts")
RENEW_BEFORE = 600  # segundos antes del vencimiento en que se pide un token nuevo
STATE_SECONDS = 600
METHODS = ("client_credentials", "oauth", "token")


@dataclass(frozen=True)
class Credentials:
    method: str
    access_token: str
    scopes: tuple[str, ...] = ()
    expires_at: float | None = None
    client_id: str | None = None  # solo `client_credentials`: los de la app de la tienda
    client_secret: str | None = None
    refresh_token: str | None = None  # solo `oauth`
    refresh_expires_at: float | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def dump(self) -> str:
        data = {k: v for k, v in asdict(self).items() if v not in (None, (), {})}
        data["scopes"] = list(self.scopes)
        return json.dumps(data)

    def expired(self, now: float | None = None) -> bool:
        return self.expires_at is not None and self.expires_at <= (now or time.time())


class TokenError(ValueError):
    """Shopify no entregó el token. `code` dice qué falta (decisión 086): `shop_not_permitted`,
    `invalid_client`, `store_not_found` o `token_failed`."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


def parse(secret: str | None) -> Credentials:
    """Las credenciales guardadas. Un texto que no es JSON es un token pegado (legacy)."""
    text = (secret or "").strip()
    if text.startswith("{"):
        data = json.loads(text)
        data["scopes"] = tuple(data.get("scopes") or ())
        return Credentials(**{k: v for k, v in data.items() if k in Credentials.__annotations__})
    return Credentials(method="token", access_token=text)


def missing_scopes(granted: list[str] | tuple[str, ...]) -> list[str]:
    have = set(granted)
    return [
        s
        for s in SCOPES
        if s not in have and not (s.startswith("read_") and f"write_{s[5:]}" in have)
    ]


# -- pedidos al endpoint de tokens ----------------------------------------------------------------
def _token_request(
    shop: str, form: dict[str, str], transport: httpx.BaseTransport | None
) -> dict[str, Any]:
    with httpx.Client(
        transport=transport or net.SafeTransport(), timeout=15.0, follow_redirects=False
    ) as client:
        response = client.post(
            f"https://{shop}/admin/oauth/access_token",
            data=form,
            headers={"Accept": "application/json"},
        )
    try:
        body: dict[str, Any] = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200 or not body.get("access_token"):
        error = str(body.get("error") or response.status_code)
        if "shop_not_permitted" in json.dumps(body):
            raise TokenError(
                "Shopify no deja pedir el token con Client ID y Secret en esta tienda: la app "
                "tiene que ser de la misma organización que la tienda. Si es la tienda de un "
                "cliente, usá «Instalar con Shopify».",
                "shop_not_permitted",
            )
        if error in ("invalid_client", "401", "400") or "cannot_be_found" in json.dumps(body):
            raise TokenError(
                "Shopify rechazó el Client ID o el Client Secret: copialos de nuevo desde "
                "Configuración de la app en el Dev Dashboard.",
                "invalid_client",
            )
        if response.status_code == 404:
            raise TokenError(
                "No existe una tienda de Shopify con esa dirección.", "store_not_found"
            )
        raise TokenError(f"Shopify no entregó el token ({error}).", "token_failed")
    return body


def _scopes(body: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(s.strip() for s in str(body.get("scope") or "").split(",") if s.strip())


def client_credentials(
    shop: str,
    client_id: str,
    client_secret: str,
    *,
    transport: httpx.BaseTransport | None = None,
    now: float | None = None,
) -> Credentials:
    """Token de 24 h con las credenciales de una app del Dev Dashboard."""
    client_id, client_secret = client_id.strip(), client_secret.strip()
    if not client_id or not client_secret:
        raise ValueError("Faltan el Client ID o el Client Secret de la app.")
    body = _token_request(
        shop,
        {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        transport,
    )
    return Credentials(
        method="client_credentials",
        access_token=str(body["access_token"]),
        scopes=_scopes(body),
        expires_at=(now or time.time()) + float(body.get("expires_in") or 86399),
        client_id=client_id,
        client_secret=client_secret,
    )


def _oauth_credentials(body: Mapping[str, Any], now: float) -> Credentials:
    refresh_in = body.get("refresh_token_expires_in")
    expires_in = body.get("expires_in")
    return Credentials(
        method="oauth",
        access_token=str(body["access_token"]),
        scopes=_scopes(body),
        expires_at=now + float(expires_in) if expires_in else None,
        refresh_token=str(body["refresh_token"]) if body.get("refresh_token") else None,
        refresh_expires_at=now + float(refresh_in) if refresh_in else None,
    )


def exchange_code(
    shop: str,
    code: str,
    client_id: str,
    client_secret: str,
    *,
    transport: httpx.BaseTransport | None = None,
    now: float | None = None,
) -> Credentials:
    """El código del callback por un token offline que vence (`expiring=1`)."""
    body = _token_request(
        shop,
        {"client_id": client_id, "client_secret": client_secret, "code": code, "expiring": "1"},
        transport,
    )
    return _oauth_credentials(body, now or time.time())


def app_credentials() -> tuple[str, str] | None:
    """La app de la plataforma para instalar por OAuth, si está configurada."""
    client_id = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
    client_secret = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
    return (client_id, client_secret) if client_id and client_secret else None


def fresh(
    shop: str,
    creds: Credentials,
    *,
    transport: httpx.BaseTransport | None = None,
    now: float | None = None,
    app: tuple[str, str] | None = None,
) -> Credentials:
    """Las mismas credenciales si el token sigue vigente; si vence en menos de diez minutos, un
    token nuevo. Un token pegado nunca se renueva."""
    moment = now or time.time()
    if creds.expires_at is None or creds.expires_at - moment > RENEW_BEFORE:
        return creds
    if creds.method == "client_credentials" and creds.client_id and creds.client_secret:
        return client_credentials(
            shop, creds.client_id, creds.client_secret, transport=transport, now=moment
        )
    if creds.method == "oauth" and creds.refresh_token:
        client_id, client_secret = app or app_credentials() or ("", "")
        if not client_id:
            return creds
        body = _token_request(
            shop,
            {
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": creds.refresh_token,
            },
            transport,
        )
        renewed = _oauth_credentials(body, moment)
        return (
            renewed
            if renewed.refresh_token
            else replace(renewed, refresh_token=creds.refresh_token)
        )
    return creds


# -- OAuth: instalación, state y HMAC -----------------------------------------------------------
def authorize_url(shop: str, client_id: str, redirect_uri: str, state: str) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "scope": ",".join(SCOPES),
            "redirect_uri": redirect_uri,
            "state": state,
        }
    )
    return f"https://{shop}/admin/oauth/authorize?{query}"


def valid_hmac(params: Mapping[str, str], client_secret: str) -> bool:
    """El HMAC del callback: SHA-256 en hexadecimal de los demás parámetros ordenados
    (`clave=valor` unidos con `&`), firmado con el Client Secret de la app."""
    given = str(params.get("hmac") or "")
    if not given or not client_secret:
        return False
    message = "&".join(f"{k}={v}" for k, v in sorted(params.items()) if k not in ("hmac",))
    expected = hmac.new(client_secret.encode(), message.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


def _sign(data: str) -> str:
    from app.panel import auth

    return auth._sign(data)


def make_state(tenant_id: uuid.UUID, shop: str, now: float | None = None) -> str:
    """El state de la instalación: la empresa, la tienda, un nonce y el vencimiento, firmados con
    la clave del panel. El nonce también va en una cookie: el callback tiene que venir del mismo
    navegador que empezó."""
    claims = {
        "t": str(tenant_id),
        "s": shop,
        "n": secrets.token_urlsafe(12),
        "x": int((now or time.time()) + STATE_SECONDS),
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload)}"


def read_state(state: str, shop: str, now: float | None = None) -> dict[str, Any] | None:
    """Los datos del state si la firma es válida, no venció y es de esa tienda."""
    payload, _, signature = state.rpartition(".")
    if not payload or not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        claims: dict[str, Any] = json.loads(
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        )
    except ValueError:
        return None
    if claims.get("s") != shop or int(claims.get("x", 0)) <= (now or time.time()):
        return None
    return claims
