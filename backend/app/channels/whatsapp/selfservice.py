"""WhatsApp self-service (decisión 068): una empresa que ya hizo el trámite de Meta conecta su
número desde el panel, sin ayuda.

Reutiliza la tabla `channels` (ruteo por `phone_number_id`, token cifrado) y el webhook. Lo
nuevo por empresa:

* el **App Secret** de su app de Meta, para validar la firma `X-Hub-Signature-256`;
* un **verify token** y una **URL de webhook** propios (`/webhooks/whatsapp/{webhook_key}`);
* la **prueba de conexión** contra la Graph API, el **mensaje de prueba** y **activar o
  desactivar** el canal. Un canal guardado queda inactivo hasta pasar la prueba y activarse.

Los secretos se cifran como el token del canal y nunca se vuelven a mostrar. El verify token sí
se muestra: hay que copiarlo en Meta.
"""

import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app.channels.whatsapp.client import GRAPH_URL, GRAPH_VERSION, OUTSIDE_WINDOW
from app.crypto import decrypt, encrypt
from app.db.connection import tenant_scope

# Solo para tests: el transporte hacia la Graph API. En producción, la red.
TRANSPORT: httpx.BaseTransport | None = None
PERMISSIONS = ("whatsapp_business_messaging", "whatsapp_business_management")
_DIGITS = re.compile(r"^\d{5,25}$")


class SetupError(ValueError):
    """Un dato inválido o un error de Meta: el mensaje se le muestra a la empresa."""


@dataclass(frozen=True)
class WhatsAppSetup:
    channel_id: uuid.UUID
    phone_number_id: str
    waba_id: str | None
    status: str
    webhook_key: str | None
    verify_token: str | None
    has_token: bool
    has_app_secret: bool
    last_test: dict[str, Any] | None


def _row_setup(row: tuple[Any, ...]) -> WhatsAppSetup:
    return WhatsAppSetup(
        channel_id=row[0],
        phone_number_id=row[1],
        waba_id=row[2],
        status=row[3],
        webhook_key=row[4],
        verify_token=decrypt(row[5]) if row[5] else None,
        has_token=bool(row[6]),
        has_app_secret=bool(row[7]),
        last_test=row[8],
    )


_COLUMNS = (
    "id, phone_number_id, waba_id, status, webhook_key, verify_token_enc,"
    " access_token_enc is not null, app_secret_enc is not null, last_test"
)


def load(conn: psycopg.Connection, tenant_id: uuid.UUID) -> WhatsAppSetup | None:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            f"select {_COLUMNS} from channels where kind = 'whatsapp' order by created_at limit 1"
        ).fetchone()
    return _row_setup(row) if row else None


def _secrets(conn: psycopg.Connection, tenant_id: uuid.UUID) -> tuple[str, str]:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select access_token_enc, app_secret_enc from channels where kind = 'whatsapp'"
            " order by created_at limit 1"
        ).fetchone()
    if not row or not row[0]:
        raise SetupError("Primero guardá los datos de WhatsApp.")
    return decrypt(row[0]), decrypt(row[1]) if row[1] else ""


def save(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    *,
    phone_number_id: str,
    waba_id: str,
    token: str = "",
    app_secret: str = "",
) -> WhatsAppSetup:
    """Guarda (o actualiza) el número de la empresa. Sin token o App Secret, se conservan los
    guardados. El canal vuelve a inactivo: hay que probar y activar de nuevo."""
    phone_number_id, waba_id = phone_number_id.strip(), waba_id.strip()
    token, app_secret = token.strip(), app_secret.strip()
    if not _DIGITS.match(phone_number_id):
        raise SetupError(
            "El Phone Number ID son solo números (está en WhatsApp → Configuración de la API)."
        )
    if not _DIGITS.match(waba_id):
        raise SetupError("El WhatsApp Business Account ID son solo números.")
    if any(c.isspace() for c in token + app_secret):
        raise SetupError("El token y el App Secret van sin espacios.")
    # Un número es de una sola empresa. Se mira fuera del tenant, como el ruteo del webhook.
    owner = conn.execute(
        "select tenant_id from channels where phone_number_id = %s", (phone_number_id,)
    ).fetchone()
    if owner is not None and owner[0] != tenant_id:
        raise SetupError("Ese número ya está conectado a otra empresa. Escribinos si es tuyo.")
    current = load(conn, tenant_id)
    if current is None and (not token or not app_secret):
        raise SetupError("Faltan el token permanente y el App Secret.")
    with tenant_scope(conn, tenant_id):
        if current is None:
            conn.execute(
                "insert into channels (tenant_id, kind, phone_number_id, waba_id,"
                " access_token_enc, app_secret_enc, verify_token_enc, webhook_key, status)"
                " values (%s, 'whatsapp', %s, %s, %s, %s, %s, %s, 'inactive')",
                (
                    tenant_id,
                    phone_number_id,
                    waba_id,
                    encrypt(token),
                    encrypt(app_secret),
                    encrypt(secrets.token_urlsafe(24)),
                    secrets.token_urlsafe(12),
                ),
            )
        else:
            conn.execute(
                "update channels set phone_number_id = %s, waba_id = %s,"
                " access_token_enc = coalesce(%s, access_token_enc),"
                " app_secret_enc = coalesce(%s, app_secret_enc),"
                " verify_token_enc = coalesce(verify_token_enc, %s),"
                " webhook_key = coalesce(webhook_key, %s),"
                " status = 'inactive', last_test = null, updated_at = now() where id = %s",
                (
                    phone_number_id,
                    waba_id,
                    encrypt(token) if token else None,
                    encrypt(app_secret) if app_secret else None,
                    encrypt(secrets.token_urlsafe(24)),
                    secrets.token_urlsafe(12),
                    current.channel_id,
                ),
            )
    found = load(conn, tenant_id)
    assert found is not None
    return found


# -- Graph API ----------------------------------------------------------------------------------
def explain(response: httpx.Response) -> str:
    """El error de la Graph API con un mensaje claro para la empresa."""
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        error = {}
    code = error.get("code")
    if code == 190 or response.status_code == 401:
        return (
            "El token venció o se revocó. Generá uno nuevo del usuario del sistema con "
            "vencimiento «Nunca» y guardalo de nuevo."
        )
    if code in (10, 200, 3, 294) or response.status_code == 403:
        return (
            "Al token le faltan permisos: whatsapp_business_messaging y "
            "whatsapp_business_management, y el usuario del sistema tiene que tener asignadas "
            "la app y la cuenta de WhatsApp."
        )
    if code == 100:
        return (
            "Meta no encuentra ese Phone Number ID o el token no tiene acceso a él. Revisá el "
            "número en WhatsApp → Configuración de la API."
        )
    if code == 133010:
        return "El número no está registrado en la Cloud API: registralo en WhatsApp Manager."
    if code == 131030:
        return "Ese destinatario no está en la lista de números permitidos de la app de prueba."
    if code == OUTSIDE_WINDOW:
        return (
            "WhatsApp solo deja escribir libremente dentro de las 24 h del último mensaje de ese "
            "número. Mandá primero un mensaje desde ese teléfono a tu número."
        )
    message = str(error.get("message") or response.status_code)
    return f"Meta respondió con un error: {message} (código {code})."


def _graph(token: str) -> httpx.Client:
    return httpx.Client(
        base_url=f"{GRAPH_URL}/{GRAPH_VERSION}",
        headers={"Authorization": f"Bearer {token}"},
        transport=TRANSPORT,
        timeout=15.0,
        follow_redirects=False,
    )


def _check(setup: WhatsAppSetup, token: str) -> str:
    """Token, permisos, número registrado y número de la cuenta. Devuelve el número visible."""
    with _graph(token) as graph:
        number = graph.get(
            f"/{setup.phone_number_id}",
            params={"fields": "display_phone_number,verified_name,platform_type,quality_rating"},
        )
        if number.status_code != 200:
            raise SetupError(explain(number))
        data = number.json()
        granted = graph.get("/me/permissions")
        if granted.status_code == 200:
            have = {
                p.get("permission")
                for p in granted.json().get("data") or []
                if p.get("status") == "granted"
            }
            missing = [p for p in PERMISSIONS if p not in have]
            if missing:
                raise SetupError(
                    f"Al token le faltan permisos: {', '.join(missing)}. Generalo de nuevo "
                    "marcándolos."
                )
        if data.get("platform_type") not in (None, "CLOUD_API"):
            raise SetupError(
                "El número no está registrado en la Cloud API: registralo en WhatsApp Manager "
                "(o en WhatsApp → Configuración de la API) y probá de nuevo."
            )
        if setup.waba_id:
            numbers = graph.get(f"/{setup.waba_id}/phone_numbers")
            if numbers.status_code != 200:
                raise SetupError(explain(numbers))
            ids = {str(n.get("id")) for n in numbers.json().get("data") or []}
            if setup.phone_number_id not in ids:
                raise SetupError(
                    "El número no pertenece a esa WhatsApp Business Account: revisá los dos IDs."
                )
    name = data.get("verified_name") or ""
    return f"{data.get('display_phone_number', setup.phone_number_id)} {name}".strip()


def test(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Prueba el token y el número contra la Graph API (solo lectura) y guarda el resultado."""
    setup = load(conn, tenant_id)
    if setup is None:
        raise SetupError("Primero guardá los datos de WhatsApp.")
    token, _ = _secrets(conn, tenant_id)
    try:
        shown = _check(setup, token)
        result: dict[str, Any] = {"ok": True, "detalle": f"Conectado: {shown}."}
    except SetupError as error:
        result = {"ok": False, "detalle": str(error)}
    except httpx.HTTPError as error:
        result = {"ok": False, "detalle": f"Meta no respondió ({type(error).__name__})."}
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update channels set last_test = %s, updated_at = now() where id = %s",
            (Jsonb(result), setup.channel_id),
        )
    return result


def clean_number(value: str) -> str:
    digits = re.sub(r"[\s()+.-]", "", value)
    if not re.fullmatch(r"\d{8,15}", digits):
        raise SetupError("Escribí el número con código de país, por ejemplo +54 9 11 5555-0000.")
    return digits


def send_test(conn: psycopg.Connection, tenant_id: uuid.UUID, number: str) -> None:
    """Un mensaje de prueba a un número. Fuera de la ventana de 24 h, la plantilla
    `hello_world`, que Meta crea en cada cuenta."""
    setup = load(conn, tenant_id)
    if setup is None:
        raise SetupError("Primero guardá los datos de WhatsApp.")
    to = clean_number(number)
    token, _ = _secrets(conn, tenant_id)
    text = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": "Mensaje de prueba: tu asistente ya puede escribir por WhatsApp."},
    }
    with _graph(token) as graph:
        sent = graph.post(f"/{setup.phone_number_id}/messages", json=text)
        code = (sent.json().get("error") or {}).get("code") if sent.content else None
        if code == OUTSIDE_WINDOW:
            template = {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "template",
                "template": {"name": "hello_world", "language": {"code": "en_US"}},
            }
            sent = graph.post(f"/{setup.phone_number_id}/messages", json=template)
    if sent.status_code >= 400:
        raise SetupError(explain(sent))


def activate(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    """Activa el canal: suscribe la app a la cuenta de WhatsApp (sin eso Meta no manda los
    mensajes al webhook) y lo marca activo. Solo con la prueba aprobada."""
    setup = load(conn, tenant_id)
    if setup is None or not (setup.last_test or {}).get("ok"):
        raise SetupError("Primero tiene que pasar la prueba de conexión.")
    token, _ = _secrets(conn, tenant_id)
    if setup.waba_id:
        with _graph(token) as graph:
            done = graph.post(f"/{setup.waba_id}/subscribed_apps")
        if done.status_code >= 400:
            raise SetupError(explain(done))
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update channels set status = 'active', updated_at = now() where id = %s",
            (setup.channel_id,),
        )


def deactivate(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update channels set status = 'inactive', updated_at = now() where kind = 'whatsapp'"
        )


# -- webhook de la empresa ----------------------------------------------------------------------
@dataclass(frozen=True)
class WebhookChannel:
    tenant_id: uuid.UUID
    phone_number_id: str
    status: str
    verify_token: str
    app_secret: str


def channel_for_webhook(conn: psycopg.Connection, key: str) -> WebhookChannel | None:
    """El canal de la URL del webhook. Como el ruteo por número, es una consulta fuera de un
    tenant: todavía no se sabe de qué empresa es."""
    if not key or len(key) > 64:
        return None
    row = conn.execute(
        "select tenant_id, phone_number_id, status, verify_token_enc, app_secret_enc"
        " from channels where webhook_key = %s and kind = 'whatsapp'",
        (key,),
    ).fetchone()
    if row is None or not row[3] or not row[4]:
        return None
    return WebhookChannel(row[0], row[1], row[2], decrypt(row[3]), decrypt(row[4]))
