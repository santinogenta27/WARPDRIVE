"""Cliente de la WhatsApp Cloud API (Graph API de Meta) para enviar mensajes."""

import re
from typing import Any

import httpx

_URL = re.compile(r"\bhttps?://\S", re.I)
GRAPH_URL = "https://graph.facebook.com"
GRAPH_VERSION = "v27.0"  # la más reciente según el changelog de la Graph API (noviembre de 2026)
NOT_IN_ALLOWED_LIST = 131030
OUTSIDE_WINDOW = 131047  # pasaron más de 24 h desde el último mensaje del cliente


class WhatsAppError(RuntimeError):
    def __init__(self, status: int, code: int | None, message: str) -> None:
        super().__init__(f"WhatsApp rechazó el envío ({status}, código {code}): {message}")
        self.status = status
        self.code = code


class WhatsAppClient:
    def __init__(self, http: httpx.Client | None = None, version: str = GRAPH_VERSION) -> None:
        self._http = http or httpx.Client(timeout=15.0)
        self._version = version

    def send_text(self, *, token: str, phone_number_id: str, to: str, body: str) -> str:
        """Envía un mensaje libre (dentro de la ventana de 24 h). Devuelve el wamid. Con un link,
        WhatsApp muestra su vista previa (decisión 103)."""
        preview = bool(_URL.search(body))
        message = {"type": "text", "text": {"preview_url": preview, "body": body}}
        return self._send(token, phone_number_id, to, message)

    def send_template(
        self,
        *,
        token: str,
        phone_number_id: str,
        to: str,
        name: str,
        language: str,
        params: list[str],
    ) -> str:
        """Envía una plantilla aprobada por Meta (fuera de la ventana de 24 h)."""
        message = {
            "type": "template",
            "template": {
                "name": name,
                "language": {"code": language},
                "components": [
                    {
                        "type": "body",
                        "parameters": [{"type": "text", "text": p} for p in params],
                    }
                ],
            },
        }
        return self._send(token, phone_number_id, to, message)

    def typing(self, *, token: str, phone_number_id: str, message_id: str) -> None:
        """Marca el mensaje del cliente como leído y muestra "escribiendo…" (hasta 25 s o hasta
        la respuesta). Lo usa el modo supervisado mientras la respuesta espera (decisión 072)."""
        response = self._http.post(
            f"{GRAPH_URL}/{self._version}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "status": "read",
                "message_id": message_id,
                "typing_indicator": {"type": "text"},
            },
        )
        if response.status_code >= 400:
            data: dict[str, Any] = response.json() if response.content else {}
            error = data.get("error") or {}
            raise WhatsAppError(response.status_code, error.get("code"), str(error.get("message")))

    def _send(self, token: str, phone_number_id: str, to: str, message: dict[str, Any]) -> str:
        try:
            return self._post(token, phone_number_id, to, message)
        except WhatsAppError as error:
            # Celulares de Argentina: WhatsApp informa 549XXXXXXXXXX, pero la lista de números
            # permitidos del número de prueba los guarda sin el 9. Se reintenta una vez sin él.
            if error.code == NOT_IN_ALLOWED_LIST and to.startswith("549"):
                return self._post(token, phone_number_id, "54" + to[3:], message)
            raise

    def _post(self, token: str, phone_number_id: str, to: str, message: dict[str, Any]) -> str:
        response = self._http.post(
            f"{GRAPH_URL}/{self._version}/{phone_number_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to,
                **message,
            },
        )
        data: dict[str, Any] = response.json() if response.content else {}
        if response.status_code >= 400:
            error = data.get("error") or {}
            raise WhatsAppError(response.status_code, error.get("code"), str(error.get("message")))
        return str(data["messages"][0]["id"])
