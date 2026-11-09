"""Deja lista la demo: tenant Acme permanente con sus canales asignados.

Uso: `make whatsapp-setup` (idempotente: se puede correr varias veces, por ejemplo cuando
cambia el token de Meta). Asigna siempre el chat web y, si están en `.env`:

- Cloud API de Meta: WHATSAPP_PHONE_NUMBER_ID y WHATSAPP_ACCESS_TOKEN.
- Twilio: TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN y TWILIO_WHATSAPP_FROM.

Necesita además DATABASE_URL, ENCRYPTION_KEY y una clave de embeddings (VOYAGE o GEMINI).
"""

import os
import sys
import uuid
from collections.abc import Mapping

import psycopg

from app.channels.web.channel import DEMO_ADDRESS
from app.channels.whatsapp.routing import register_whatsapp_channel
from app.config import load_dotenv
from app.db.connection import connect
from app.db.migrate import migrate
from app.demo.acme import ensure_demo_tenant
from app.llm.client import PostgresUsageRecorder
from app.llm.factory import embedder_from_env


def assign_channels(
    conn: psycopg.Connection, tenant_id: uuid.UUID, env: Mapping[str, str]
) -> list[str]:
    """Asigna al tenant el chat web y los canales configurados en `env`; devuelve cuáles."""
    register_whatsapp_channel(conn, tenant_id, phone_number_id=DEMO_ADDRESS, kind="web")
    assigned = [f"chat web {DEMO_ADDRESS}"]
    number, token = env.get("WHATSAPP_PHONE_NUMBER_ID"), env.get("WHATSAPP_ACCESS_TOKEN")
    if number and token:
        register_whatsapp_channel(conn, tenant_id, phone_number_id=number, token=token)
        assigned.append(f"Meta {number}")
    twilio_from = env.get("TWILIO_WHATSAPP_FROM")
    if twilio_from and env.get("TWILIO_ACCOUNT_SID") and env.get("TWILIO_AUTH_TOKEN"):
        register_whatsapp_channel(
            conn, tenant_id, phone_number_id=twilio_from, kind="twilio_whatsapp"
        )
        assigned.append(f"Twilio {twilio_from}")
    return assigned


def main() -> int:
    load_dotenv()
    migrate()
    with connect() as conn:
        tenant_id = ensure_demo_tenant(conn, embedder_from_env(PostgresUsageRecorder(conn)))
        assigned = assign_channels(conn, tenant_id, os.environ)
    print(f"Acme Store lista (tenant {tenant_id}), con {', '.join(assigned)}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
