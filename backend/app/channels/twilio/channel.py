"""Canal twilio_whatsapp: WhatsApp por Twilio (la prueba "Try out WhatsApp" o un número propio).

Recibe: Twilio hace un POST de formulario firmado con X-Twilio-Signature (HMAC-SHA1 del auth
token sobre la URL más los parámetros ordenados, en base64). Envía: POST a Messages.json con
autenticación básica (account SID y auth token). Sin SDK: son dos llamadas HTTP (decisión 017).
"""

import base64
import hashlib
import hmac
import os
import re
import time
from collections.abc import Mapping
from urllib.parse import parse_qsl

import httpx

from app.channels.base import InboundMessage, InvalidSignature, SendError

JOB_KIND = "twilio_whatsapp_message"
API = "https://api.twilio.com/2010-04-01"


def twilio_signature(url: str, params: list[tuple[str, str]], auth_token: str) -> str:
    data = url + "".join(key + value for key, value in sorted(params))
    digest = hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _digits(address: str) -> str:
    return re.sub(r"\D", "", address)


class TwilioWhatsApp:
    kind = "twilio_whatsapp"

    def __init__(
        self,
        *,
        account_sid: str,
        auth_token: str,
        from_address: str,  # "whatsapp:+14155238886"
        http: httpx.Client | None = None,
    ) -> None:
        self.account_sid = account_sid
        self._auth_token = auth_token
        self.from_address = from_address
        self._http = http or httpx.Client(timeout=30)

    def receive(self, url: str, body: bytes, headers: Mapping[str, str]) -> list[InboundMessage]:
        params = parse_qsl(body.decode("utf-8"), keep_blank_values=True)
        signature = headers.get("x-twilio-signature") or ""
        expected = twilio_signature(url, params, self._auth_token)
        if not self._auth_token or not hmac.compare_digest(expected, signature):
            raise InvalidSignature
        form = dict(params)
        sender = form.get("WaId") or _digits(form.get("From", ""))
        if not form.get("MessageSid") or not form.get("To") or not sender:
            return []
        media = int(form.get("NumMedia") or 0)
        return [
            InboundMessage(
                phone_number_id=form["To"],  # "whatsapp:+1...": rutea al tenant (channels)
                wamid=form["MessageSid"],
                sender=sender,  # wa_id, igual que en Meta: el mismo cliente es el mismo contacto
                timestamp=int(time.time()),  # Twilio no manda la hora del mensaje
                type=form.get("MessageType") or ("media" if media else "text"),
                text=form.get("Body", ""),
                name=form.get("ProfileName", ""),
            )
        ]

    def send(self, *, to: str, body: str) -> str:
        response = self._http.post(
            f"{API}/Accounts/{self.account_sid}/Messages.json",
            data={"From": self.from_address, "To": f"whatsapp:+{_digits(to)}", "Body": body},
            auth=(self.account_sid, self._auth_token),
        )
        if response.status_code >= 400:
            try:
                error = response.json()
            except ValueError:
                error = {}
            raise SendError(
                f"Twilio respondió {response.status_code}: {error.get('message', response.text)}",
                code=error.get("code"),
                # 4xx (salvo 429): número fuera del sandbox, cuerpo inválido, credenciales.
                permanent=response.status_code < 500 and response.status_code != 429,
            )
        return str(response.json()["sid"])


def twilio_from_env() -> TwilioWhatsApp | None:
    """Canal con TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN y TWILIO_WHATSAPP_FROM; None si falta uno."""
    sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    token = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_address = os.environ.get("TWILIO_WHATSAPP_FROM", "")
    if not (sid and token and from_address):
        return None
    return TwilioWhatsApp(account_sid=sid, auth_token=token, from_address=from_address)
