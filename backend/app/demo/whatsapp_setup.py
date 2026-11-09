"""Deja lista la demo por WhatsApp: tenant Acme permanente con sus números asignados.

Uso: `make whatsapp-setup` (idempotente: se puede correr varias veces, por ejemplo cuando
cambia el token de Meta). Asigna los canales que estén configurados en `.env`:

- Cloud API de Meta: WHATSAPP_PHONE_NUMBER_ID y WHATSAPP_ACCESS_TOKEN.
- Twilio: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN y TWILIO_WHATSAPP_FROM.

Necesita además DATABASE_URL, ENCRYPTION_KEY y una clave de embeddings (VOYAGE o GEMINI).
"""

import os
import sys

from app.channels.twilio.channel import twilio_from_env
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.config import load_dotenv
from app.db.connection import connect
from app.db.migrate import migrate
from app.demo.acme import ensure_demo_tenant
from app.llm.client import PostgresUsageRecorder
from app.llm.factory import embedder_from_env


def main() -> int:
    load_dotenv()
    number = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    token = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    twilio = twilio_from_env()
    if not (number and token) and twilio is None:
        print("Falta un canal en .env: las variables WHATSAPP_* (Meta) o TWILIO_* (Twilio).")
        return 1
    migrate()
    assigned: list[str] = []
    with connect() as conn:
        tenant_id = ensure_demo_tenant(conn, embedder_from_env(PostgresUsageRecorder(conn)))
        if number and token:
            register_whatsapp_channel(conn, tenant_id, phone_number_id=number, token=token)
            assigned.append(f"Meta {number}")
        if twilio is not None:
            register_whatsapp_channel(
                conn, tenant_id, phone_number_id=twilio.from_address, kind="twilio_whatsapp"
            )
            assigned.append(f"Twilio {twilio.from_address}")
    print(f"Acme Store lista (tenant {tenant_id}), con {' y '.join(assigned)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
