"""Pedidos HTTP a direcciones que carga un usuario, sin SSRF (decisión 076).

Toda URL que viene de una empresa o de un visitante (su sitio, la URL base de su sistema, su
tienda de Shopify o Tiendanube) sale por `SafeTransport`:

* Solo HTTPS.
* Antes de conectar se resuelve el DNS y se exige que **todas** las direcciones sean públicas: nada
  de localhost, redes privadas, reservadas, link-local (incluida 169.254.169.254, la de metadatos
  de la nube), loopback IPv6 ni fc00::/7.
* La conexión va a la IP verificada (no se vuelve a resolver: un DNS que cambia entre la
  verificación y la conexión no sirve), con el nombre original en `Host` y en el SNI, así el
  certificado se valida contra el dominio.
* Cada redirección es un pedido nuevo que pasa por el mismo control.
* La respuesta tiene tope de tamaño; el cliente, timeout.

`ALLOW_PRIVATE_SYSTEM_URLS=1` lo apaga, solo para desarrollo local.
"""

import ipaddress
import os
import socket
from collections.abc import Callable
from typing import Any

import httpx

Resolver = Callable[[str], list[str]]
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
TIMEOUT = httpx.Timeout(15.0, connect=5.0)
# Además de lo que Python ya considera no global: por las dudas, explícitas.
BLOCKED = tuple(
    ipaddress.ip_network(net)
    for net in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.168.0.0/16",
        "198.18.0.0/15",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)
_BLOCKED_NAMES = ("localhost", ".localhost", ".local", ".internal", ".lan", ".home.arpa")


class UnsafeURL(ValueError):
    """La dirección no es pública o no es HTTPS. El mensaje se puede mostrar."""


class ResponseTooLarge(ValueError):
    """La respuesta supera el tope de tamaño."""


def allow_private() -> bool:
    """Solo para desarrollo local (probar contra una API en localhost)."""
    return os.environ.get("ALLOW_PRIVATE_SYSTEM_URLS", "").strip() == "1"


def is_public_ip(address: str) -> bool:
    ip = ipaddress.ip_address(address.split("%", 1)[0])
    if isinstance(ip, ipaddress.IPv6Address):
        mapped = ip.ipv4_mapped or ip.sixtofour
        if mapped is not None:
            return is_public_ip(str(mapped))
    if any(ip in net for net in BLOCKED if net.version == ip.version):
        return False
    return ip.is_global and not ip.is_multicast


def _resolve(host: str) -> list[str]:
    return sorted({str(info[4][0]) for info in socket.getaddrinfo(host, 443)})


def public_address(host: str, resolver: Resolver | None = None) -> str:
    """La IP pública a la que conectar para `host`. `UnsafeURL` si alguna no es pública."""
    name = host.strip("[]").lower().rstrip(".")
    if not name:
        raise UnsafeURL("La dirección no tiene dominio.")
    try:
        ipaddress.ip_address(name.split("%", 1)[0])
        literal = True
    except ValueError:
        literal = False
    if literal:
        if not is_public_ip(name):
            raise UnsafeURL("La dirección apunta a una red interna.")
        return name
    if name == "localhost" or name.endswith(_BLOCKED_NAMES) or "." not in name:
        raise UnsafeURL("La dirección apunta a una red interna.")
    try:
        addresses = (resolver or _resolve)(name)
    except OSError as error:
        raise UnsafeURL(f"No se pudo resolver el dominio {name}.") from error
    if not addresses:
        raise UnsafeURL(f"No se pudo resolver el dominio {name}.")
    if not all(is_public_ip(a) for a in addresses):
        raise UnsafeURL("La dirección apunta a una red interna.")
    return addresses[0]


def check_url(url: str, resolver: Resolver | None = None) -> None:
    """La validación de `SafeTransport`, para avisar antes (al guardar o al empezar)."""
    if allow_private():
        return
    parts = httpx.URL(url)
    if parts.scheme != "https":
        raise UnsafeURL("La dirección tiene que ser HTTPS.")
    public_address(parts.host, resolver)


def _limited(response: httpx.Response, max_bytes: int) -> httpx.Response:
    declared = response.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        response.close()
        raise ResponseTooLarge(f"La respuesta supera {max_bytes // 1024} KB.")
    body = bytearray()
    try:
        for chunk in response.stream:  # type: ignore[union-attr]
            body.extend(chunk)
            if len(body) > max_bytes:
                raise ResponseTooLarge(f"La respuesta supera {max_bytes // 1024} KB.")
    finally:
        response.close()
    headers = [(k, v) for k, v in response.headers.multi_items() if k.lower() != "content-length"]
    return httpx.Response(
        response.status_code,
        headers=headers,
        content=bytes(body),
        extensions=response.extensions,
    )


class SafeTransport(httpx.BaseTransport):
    """Transporte que solo conecta a IPs públicas verificadas, por HTTPS, con tope de tamaño."""

    def __init__(
        self,
        inner: httpx.BaseTransport | None = None,
        *,
        resolver: Resolver | None = None,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._inner = inner or httpx.HTTPTransport()
        self._resolver = resolver
        self._max_bytes = max_bytes

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if allow_private():
            return _limited(self._inner.handle_request(request), self._max_bytes)
        url = request.url
        if url.scheme != "https":
            raise UnsafeURL("La dirección tiene que ser HTTPS.")
        ip = public_address(url.host, self._resolver)
        headers = request.headers.copy()
        headers["Host"] = url.netloc.decode("ascii")
        extensions: dict[str, Any] = {**request.extensions, "sni_hostname": url.host}
        pinned = httpx.Request(
            request.method,
            url.copy_with(host=ip),
            headers=headers,
            stream=request.stream,
            extensions=extensions,
        )
        return _limited(self._inner.handle_request(pinned), self._max_bytes)

    def close(self) -> None:
        self._inner.close()


def safe_client(
    *,
    max_bytes: int = MAX_RESPONSE_BYTES,
    resolver: Resolver | None = None,
    inner: httpx.BaseTransport | None = None,
    **kwargs: Any,
) -> httpx.Client:
    """Un cliente HTTP para URLs de usuarios. Por defecto no sigue redirecciones."""
    kwargs.setdefault("timeout", TIMEOUT)
    kwargs.setdefault("follow_redirects", False)
    transport = SafeTransport(inner, resolver=resolver, max_bytes=max_bytes)
    return httpx.Client(transport=transport, **kwargs)
