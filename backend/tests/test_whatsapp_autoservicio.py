"""Test de aceptación: WhatsApp self-service (decisión 068).

Una empresa que ya hizo el trámite de Meta conecta su número desde el panel, sin ayuda:

1. Configuración → Canales → WhatsApp: Phone Number ID, WhatsApp Business Account ID, token
   permanente y App Secret. Todo cifrado; nunca se vuelve a mostrar.
2. El panel muestra la URL del webhook y un verify token propio de la empresa. El webhook
   acepta la verificación de Meta con ese token y valida la firma con el App Secret de la
   empresa.
3. Probar conexión contra la Graph API, con errores claros.
4. Enviar un mensaje de prueba.
5. Activar y desactivar el canal.
6. La guía paso a paso, en español y en inglés.
"""

import hashlib
import hmac
import json
import re
from collections.abc import Iterator
from contextlib import nullcontext
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.channels.whatsapp import selfservice
from app.channels.whatsapp.routing import channel_for_number
from app.channels.whatsapp.webhook import ConnectionStore, lookup_connection, message_store

PHONE_ID = "106540352242922"
WABA_ID = "102290129340398"
TOKEN = "EAAJB-token-permanente"
APP_SECRET = "a1b2c3-secreto-de-la-app"


class FakeGraph:
    """La Graph API de Meta, con respuestas de la forma de la documentación."""

    def __init__(self) -> None:
        self.token = TOKEN
        self.platform_type = "CLOUD_API"
        self.permissions = ["whatsapp_business_messaging", "whatsapp_business_management"]
        self.numbers = [PHONE_ID]
        self.sent: list[dict[str, Any]] = []
        self.subscribed = False

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    @staticmethod
    def error(status: int, code: int, message: str, subcode: int | None = None) -> httpx.Response:
        body: dict[str, Any] = {
            "message": message,
            "type": "OAuthException",
            "code": code,
            "fbtrace_id": "AbCdEf",
        }
        if subcode:
            body["error_subcode"] = subcode
        return httpx.Response(status, json={"error": body})

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert request.url.host == "graph.facebook.com"
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return self.error(
                401, 190, "Error validating access token: Session has expired.", subcode=463
            )
        path = re.sub(r"^/v[\d.]+", "", request.url.path)
        if path == "/me/permissions":
            return httpx.Response(
                200,
                json={"data": [{"permission": p, "status": "granted"} for p in self.permissions]},
            )
        if path == f"/{PHONE_ID}" and request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "verified_name": "Tienda Sur",
                    "display_phone_number": "+54 9 11 5555-0000",
                    "code_verification_status": "VERIFIED",
                    "quality_rating": "GREEN",
                    "platform_type": self.platform_type,
                    "id": PHONE_ID,
                },
            )
        if path == f"/{WABA_ID}/phone_numbers":
            return httpx.Response(200, json={"data": [{"id": n} for n in self.numbers]})
        if path == f"/{WABA_ID}/subscribed_apps" and request.method == "POST":
            self.subscribed = True
            return httpx.Response(200, json={"success": True})
        if path == f"/{PHONE_ID}/messages":
            self.sent.append(json.loads(request.content))
            if "whatsapp_business_messaging" not in self.permissions:
                return self.error(403, 10, "Application does not have permission for this action")
            return httpx.Response(
                200,
                json={
                    "messaging_product": "whatsapp",
                    "contacts": [{"input": "5491155550000", "wa_id": "5491155550000"}],
                    "messages": [{"id": "wamid.HBgLNTQ5MTE1NTU1MDAwMBUCABEYEjQ"}],
                },
            )
        return self.error(400, 100, "Unsupported get request. Object does not exist", subcode=33)


@pytest.fixture
def company(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from app.companies.connection import runtime_for
    from app.companies.create import create_company
    from app.main import app
    from app.panel import auth
    from app.panel.routes import PanelDeps, panel_deps
    from app.policy.guardian import Guardian

    monkeypatch.setenv("PANEL_SECRET", "secreto-de-prueba")
    auth._failures.clear()
    made = create_company(conn, "Tienda Sur", "duena@tiendasur.example", "clave-sur-123")
    graph = FakeGraph()
    monkeypatch.setattr(selfservice, "TRANSPORT", graph.transport)
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=made.tenant_id,
        guardian=lambda c, t: Guardian(c, runtime_for(c, t)[0]),
    )
    app.dependency_overrides[lookup_connection] = lambda: conn
    app.dependency_overrides[message_store] = lambda: ConnectionStore(conn, commit=False)
    client = TestClient(app, base_url="https://app.example")
    login = client.post(
        "/panel/login",
        data={"email": "duena@tiendasur.example", "password": "clave-sur-123"},
        follow_redirects=False,
    )
    assert login.status_code == 303
    yield client, made.tenant_id, graph
    app.dependency_overrides.clear()


def _csrf(client: TestClient) -> str:
    found = re.search(r'name="csrf" value="([^"]+)"', client.get("/panel/configuracion").text)
    assert found
    return found.group(1)


def _post(client: TestClient, path: str, **data: str) -> str:
    done = client.post(
        f"/panel/configuracion/whatsapp{path}",
        data={"csrf": _csrf(client), **data},
        follow_redirects=False,
    )
    assert done.status_code == 303, done.text
    return str(done.headers["location"])


def _save(client: TestClient) -> str:
    return _post(
        client, "", phone_number_id=PHONE_ID, waba_id=WABA_ID, token=TOKEN, app_secret=APP_SECRET
    )


def _webhook(client: TestClient) -> tuple[str, str]:
    page = client.get("/panel/configuracion").text
    url = re.search(r'id="wa-webhook"[^>]*>([^<]+)<', page)
    verify = re.search(r'id="wa-verify"[^>]*>([^<]+)<', page)
    assert url and verify, "el panel muestra la URL del webhook y el verify token"
    return url.group(1).strip(), verify.group(1).strip()


# -- 1. formulario y cifrado ---------------------------------------------------------------------
def test_el_formulario_guarda_todo_cifrado_y_no_lo_vuelve_a_mostrar(
    company: Any, conn: psycopg.Connection
) -> None:
    client, tenant, _ = company
    assert "ok=" in _save(client)
    page = client.get("/panel/configuracion").text
    assert TOKEN not in page and APP_SECRET not in page
    assert PHONE_ID in page and WABA_ID in page
    row = conn.execute(
        "select access_token_enc, app_secret_enc, verify_token_enc, status from channels"
        " where phone_number_id = %s",
        (PHONE_ID,),
    ).fetchone()
    assert row is not None
    assert TOKEN not in row[0] and APP_SECRET not in row[1] and row[2]
    assert row[3] == "inactive"  # hasta probar y activar, no atiende


def test_guardar_sin_token_conserva_el_guardado(company: Any) -> None:
    client, tenant, _ = company
    _save(client)
    again = _post(client, "", phone_number_id=PHONE_ID, waba_id=WABA_ID, token="", app_secret="")
    assert "ok=" in again
    # La Graph API falsa solo acepta el token guardado: la prueba pasa con el de antes.
    assert "ok=" in _post(client, "/probar")
    first = _post(client, "", phone_number_id="", waba_id=WABA_ID, token="", app_secret="")
    assert "error=" in first  # el Phone Number ID es obligatorio


def test_un_numero_de_otra_empresa_no_se_puede_tomar(
    company: Any, conn: psycopg.Connection
) -> None:
    from app.channels.whatsapp.routing import register_whatsapp_channel
    from app.companies.create import create_company

    client, _, _ = company
    other = create_company(conn, "Otra", "otra@otra.example", "clave-otra-123").tenant_id
    register_whatsapp_channel(conn, other, phone_number_id=PHONE_ID, token="t")
    assert "error=" in _save(client)
    found = channel_for_number(conn, PHONE_ID)
    assert found is not None and found.tenant_id == other


# -- 2. webhook propio de la empresa ---------------------------------------------------------------
def test_el_webhook_verifica_con_el_token_de_la_empresa(company: Any) -> None:
    client, _, _ = company
    _save(client)
    url, verify = _webhook(client)
    assert url.startswith("https://app.example/webhooks/whatsapp/")
    path = url.removeprefix("https://app.example")
    ok = client.get(
        path,
        params={"hub.mode": "subscribe", "hub.verify_token": verify, "hub.challenge": "1158201444"},
    )
    assert ok.status_code == 200 and ok.text == "1158201444"
    bad = client.get(
        path, params={"hub.mode": "subscribe", "hub.verify_token": "otro", "hub.challenge": "1"}
    )
    assert bad.status_code == 403
    assert (
        client.get(
            "/webhooks/whatsapp/no-existe",
            params={"hub.mode": "subscribe", "hub.verify_token": verify, "hub.challenge": "1"},
        ).status_code
        == 403
    )


def _payload(phone_id: str = PHONE_ID) -> bytes:
    return json.dumps(
        {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": WABA_ID,
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "5491155550000",
                                    "phone_number_id": phone_id,
                                },
                                "contacts": [
                                    {"profile": {"name": "Ana"}, "wa_id": "5491166660000"}
                                ],
                                "messages": [
                                    {
                                        "from": "5491166660000",
                                        "id": f"wamid.{phone_id}",
                                        "timestamp": "1800000000",
                                        "type": "text",
                                        "text": {"body": "Hola"},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
    ).encode()


def _signed(body: bytes, secret: str = APP_SECRET) -> dict[str, str]:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return {"Content-Type": "application/json", "X-Hub-Signature-256": f"sha256={digest}"}


def test_el_webhook_valida_la_firma_con_el_app_secret_de_la_empresa(
    company: Any, conn: psycopg.Connection
) -> None:
    client, _, _ = company
    _save(client)
    _post(client, "/probar")
    _post(client, "/activar")
    path = _webhook(client)[0].removeprefix("https://app.example")
    body = _payload()
    forged = client.post(path, content=body, headers=_signed(body, "otro-secreto"))
    assert forged.status_code == 401
    ok = client.post(path, content=body, headers=_signed(body))
    assert ok.status_code == 200 and ok.json() == {"received": 1}
    queued = conn.execute(
        "select count(*) from jobs where idempotency_key = %s", (f"whatsapp:wamid.{PHONE_ID}",)
    ).fetchone()
    assert queued == (1,)
    # Un mensaje de un número que no es de la empresa no entra por su webhook.
    other = _payload("999999999")
    assert client.post(path, content=other, headers=_signed(other)).json() == {"received": 0}


# -- 3. probar conexión --------------------------------------------------------------------------
def test_probar_conexion_pasa_y_dice_el_numero(company: Any, conn: psycopg.Connection) -> None:
    client, tenant, _ = company
    _save(client)
    location = _post(client, "/probar")
    assert "ok=" in location
    setup = selfservice.load(conn, tenant)
    assert setup is not None and setup.last_test and setup.last_test["ok"]
    assert "+54 9 11 5555-0000" in setup.last_test["detalle"]


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ("token", "venció"),
        ("permisos", "whatsapp_business_messaging"),
        ("registro", "no está registrado"),
        ("cuenta", "no pertenece"),
    ],
)
def test_probar_conexion_da_errores_claros(
    company: Any, conn: psycopg.Connection, change: str, expected: str
) -> None:
    client, tenant, graph = company
    _save(client)
    if change == "token":
        graph.token = "otro"
    elif change == "permisos":
        graph.permissions = ["whatsapp_business_management"]
    elif change == "registro":
        graph.platform_type = "NOT_APPLICABLE"
    else:
        graph.numbers = ["111"]
    assert "error=" in _post(client, "/probar")
    setup = selfservice.load(conn, tenant)
    assert setup is not None and setup.last_test and not setup.last_test["ok"]
    assert expected in setup.last_test["detalle"]


# -- 4. mensaje de prueba ------------------------------------------------------------------------
def test_envia_un_mensaje_de_prueba(company: Any) -> None:
    client, _, graph = company
    _save(client)
    assert "ok=" in _post(client, "/mensaje-de-prueba", numero="+54 9 11 5555-0000")
    assert graph.sent[-1]["to"] == "5491155550000"
    assert "error=" in _post(client, "/mensaje-de-prueba", numero="hola")


# -- 5. activar y desactivar ---------------------------------------------------------------------
def test_activar_exige_la_prueba_y_desactivar_corta_el_ruteo(
    company: Any, conn: psycopg.Connection
) -> None:
    client, tenant, graph = company
    _save(client)
    assert "error=" in _post(client, "/activar")  # sin probar, no
    assert channel_for_number(conn, PHONE_ID) is None
    _post(client, "/probar")
    assert "ok=" in _post(client, "/activar")
    assert graph.subscribed  # la app queda suscripta a la cuenta de WhatsApp
    found = channel_for_number(conn, PHONE_ID)
    assert found is not None and found.tenant_id == tenant and found.token == TOKEN
    assert "ok=" in _post(client, "/desactivar")
    assert channel_for_number(conn, PHONE_ID) is None


# -- 6. guía en los dos idiomas ------------------------------------------------------------------
def test_la_guia_es_un_paso_a_paso_y_ya_no_dice_que_lo_conecta_el_operador(company: Any) -> None:
    from app.panel import guides

    client, _, _ = company
    page = client.get("/panel/configuracion").text
    assert "Lo conecta el operador" not in page
    for text in (guides.whatsapp_guide(english=False), guides.whatsapp_guide(english=True)):
        for needed in ("whatsapp_business_messaging", "whatsapp_business_management", "App Secret"):
            assert needed in text
    assert "usuario del sistema" in guides.whatsapp_guide(english=False)
    assert "system user" in guides.whatsapp_guide(english=True)
