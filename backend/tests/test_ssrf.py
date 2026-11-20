"""Seguridad de red (SSRF) — test de aceptación (decisión 076).

Toda URL que viene de un usuario (el sitio que se rastrea, la URL base de su sistema, su tienda de
Shopify o Tiendanube, el sitio de una prueba gratis) solo puede ir a direcciones públicas por
HTTPS. Se resuelve el DNS y se verifica la IP antes de conectar, y también en cada redirección; la
conexión va a esa IP (un DNS que cambia después no sirve). Las respuestas tienen tope de tamaño y
los clientes, timeout. Los webhooks del producto son solo de entrada (Meta y Twilio nos llaman):
no hay ninguno que salga a una URL cargada por un usuario.
"""

from typing import Any

import httpx
import pytest

from app import net
from app.companies import connection as system
from app.connectors import shopify, shopify_auth, tiendanube
from app.onboarding import ingest
from app.trial import service as trial

BLOCKED = [
    "127.0.0.1",
    "127.10.0.1",
    "10.0.0.5",
    "172.16.0.1",
    "172.31.255.254",
    "192.168.1.10",
    "169.254.169.254",  # metadatos de la nube
    "169.254.10.10",
    "0.0.0.0",
    "100.64.0.1",
    "::1",
    "fc00::1",
    "fd12:3456::1",
    "fe80::1",
    "::ffff:127.0.0.1",
    "::ffff:169.254.169.254",
]
PUBLIC = ["93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"]


@pytest.fixture(autouse=True)
def _production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ALLOW_PRIVATE_SYSTEM_URLS", raising=False)


def _dns(table: dict[str, list[str]]) -> Any:
    def resolve(host: str) -> list[str]:
        if host not in table:
            raise OSError("no resuelve")
        return table[host]

    return resolve


class _Upstream:
    """El servidor del otro lado: registra a qué IP y con qué Host llegó cada pedido."""

    def __init__(self, routes: dict[str, httpx.Response] | None = None) -> None:
        self.seen: list[httpx.Request] = []
        self.routes = routes or {}

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        found = self.routes.get(f"{request.headers['host']}{request.url.path}")
        return found if found is not None else httpx.Response(200, text="ok")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)


@pytest.mark.parametrize("address", BLOCKED)
def test_bloquea_direcciones_privadas_y_reservadas(address: str) -> None:
    assert not net.is_public_ip(address)
    with pytest.raises(net.UnsafeURL):
        net.public_address(address)
    upstream = _Upstream()
    client = net.safe_client(inner=upstream.transport(), resolver=_dns({"t.example": [address]}))
    with pytest.raises(net.UnsafeURL):
        client.get("https://t.example/")
    assert upstream.seen == []  # nunca se conectó


@pytest.mark.parametrize("address", PUBLIC)
def test_deja_pasar_direcciones_publicas(address: str) -> None:
    assert net.is_public_ip(address)


@pytest.mark.parametrize(
    "url",
    ["https://localhost/", "https://api.localhost/", "https://db.internal/", "https://intranet/"],
)
def test_bloquea_nombres_internos_sin_resolver(url: str) -> None:
    called: list[str] = []
    client = net.safe_client(
        inner=_Upstream().transport(), resolver=lambda h: called.append(h) or []
    )
    with pytest.raises(net.UnsafeURL):
        client.get(url)
    assert called == []


def test_solo_https() -> None:
    upstream = _Upstream()
    client = net.safe_client(
        inner=upstream.transport(), resolver=_dns({"tienda.example": ["93.184.216.34"]})
    )
    with pytest.raises(net.UnsafeURL):
        client.get("http://tienda.example/")
    assert upstream.seen == []


def test_un_dominio_con_alguna_ip_privada_se_bloquea() -> None:
    resolver = _dns({"mixto.example": ["93.184.216.34", "10.0.0.7"]})
    with pytest.raises(net.UnsafeURL):
        net.public_address("mixto.example", resolver)


def test_conecta_a_la_ip_verificada_con_el_dominio_en_host_y_sni() -> None:
    upstream = _Upstream()
    client = net.safe_client(
        inner=upstream.transport(), resolver=_dns({"tienda.example": ["93.184.216.34"]})
    )
    assert client.get("https://tienda.example/envios").text == "ok"
    sent = upstream.seen[0]
    assert sent.url.host == "93.184.216.34"  # no se vuelve a resolver
    assert sent.headers["host"] == "tienda.example"
    assert sent.extensions["sni_hostname"] == "tienda.example"


def test_cada_redireccion_se_verifica() -> None:
    to_metadata = httpx.Response(
        302, headers={"location": "https://169.254.169.254/latest/meta-data/"}
    )
    to_internal = httpx.Response(302, headers={"location": "https://interno.example/admin"})
    to_http = httpx.Response(302, headers={"location": "http://tienda.example/otra"})
    upstream = _Upstream(
        {
            "tienda.example/metadatos": to_metadata,
            "tienda.example/interno": to_internal,
            "tienda.example/http": to_http,
        }
    )
    resolver = _dns({"tienda.example": ["93.184.216.34"], "interno.example": ["192.168.0.4"]})
    client = net.safe_client(inner=upstream.transport(), resolver=resolver, follow_redirects=True)
    for path in ("/metadatos", "/interno", "/http"):
        with pytest.raises(net.UnsafeURL):
            client.get(f"https://tienda.example{path}")
    assert {r.url.host for r in upstream.seen} == {"93.184.216.34"}  # nada llegó adentro


def test_tope_de_tamano_y_timeout() -> None:
    big = b"x" * 2048

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/declarada":
            return httpx.Response(200, content=big)
        return httpx.Response(200, stream=httpx.ByteStream(big))  # sin Content-Length confiable

    resolver = _dns({"tienda.example": ["93.184.216.34"]})
    client = net.safe_client(inner=httpx.MockTransport(handler), resolver=resolver, max_bytes=1024)
    for path in ("/declarada", "/stream"):
        with pytest.raises(net.ResponseTooLarge):
            client.get(f"https://tienda.example{path}")
    assert client.timeout.connect == 5.0 and client.timeout.read == 15.0


def test_rastreo_del_sitio(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError):
        ingest.crawl("http://tienda.example/")  # solo HTTPS
    for url in ("https://127.0.0.1/", "https://169.254.169.254/", "https://[::1]/"):
        with pytest.raises(ValueError):
            ingest.crawl(url)
    # Una página del sitio que redirige adentro no se sigue; el resto del sitio sí se lee.
    pages = {
        "/": '<title>Tienda</title><a href="/sale">Sale</a><a href="/envios">Envíos</a><p>Hola</p>',
        "/envios": "<title>Envíos</title><p>En 48 horas.</p>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sale":
            return httpx.Response(302, headers={"location": "https://10.0.0.1/"})
        body = pages.get(request.url.path)
        return httpx.Response(200, text=body or "", headers={"content-type": "text/html"})

    resolver = _dns({"tienda.example": ["93.184.216.34"]})
    monkeypatch.setattr(net, "_resolve", resolver)
    client = net.safe_client(inner=httpx.MockTransport(handler))
    found = ingest.crawl("https://tienda.example/", client, max_pages=10)
    assert sorted(p.title for p in found) == ["Envíos", "Tienda"]


def test_url_base_del_sistema() -> None:
    for url in (
        "http://api.norte.example",
        "https://localhost:8080",
        "https://10.1.2.3",
        "https://169.254.169.254",
        "https://[fd00::1]",
    ):
        with pytest.raises(system.ConnectionError_):
            system.validate_base_url(url)
    with pytest.raises(system.ConnectionError_):
        system.check_public_host("https://api.norte.example", resolver=lambda h: ["172.20.0.2"])
    connection = system.SystemConnection(
        tenant_id=None,  # type: ignore[arg-type]
        openapi=None,
        base_url="https://api.norte.example",
        auth_kind="none",
        auth_header=None,
        has_credentials=False,
        status="active",
        last_test=None,
    )
    import app.net as module

    original = module._resolve
    module._resolve = lambda host: ["93.184.216.34"]  # type: ignore[assignment]
    try:
        client = system.http_client(connection, None)
    finally:
        module._resolve = original  # type: ignore[assignment]
    assert isinstance(client._transport, net.SafeTransport)  # type: ignore[attr-defined]
    assert client.follow_redirects is False


def test_conectores_de_tiendas() -> None:
    api = shopify.ShopifyTransport("norte.myshopify.com", "shpat_x")._api
    assert isinstance(api._transport, net.SafeTransport)  # type: ignore[attr-defined]
    api = tiendanube.TiendanubeTransport("123456", "tn_x")._api
    assert isinstance(api._transport, net.SafeTransport)  # type: ignore[attr-defined]
    seen: list[Any] = []
    original = httpx.Client.__init__

    def spy(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        seen.append(kwargs.get("transport"))
        raise RuntimeError("sin red en el test")

    httpx.Client.__init__ = spy  # type: ignore[method-assign]
    try:
        with pytest.raises(RuntimeError):  # alcanza con ver el transporte, sin salir a la red
            shopify_auth._token_request("norte.myshopify.com", {"a": "b"}, None)
    finally:
        httpx.Client.__init__ = original  # type: ignore[method-assign]
    assert isinstance(seen[0], net.SafeTransport)


def test_prueba_gratis_rechaza_direcciones_internas() -> None:
    assert trial.normalize_site("http://tienda.example")[0] == "https://tienda.example/"
    for site in ("127.0.0.1", "https://169.254.169.254", "localhost", "10.0.0.8"):
        with pytest.raises(trial.TrialError):
            url, _ = trial.normalize_site(site)
            try:
                net.check_url(url)
            except net.UnsafeURL as error:
                raise trial.TrialError(str(error)) from error
