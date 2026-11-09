"""Deja lista la demo por WhatsApp: tenant Acme permanente con el número de prueba asignado.

Uso: `make whatsapp-setup` (idempotente: se puede correr varias veces, por ejemplo cuando
cambia el token de Meta). Necesita DATABASE_URL, WHATSAPP_PHONE_NUMBER_ID,
WHATSAPP_ACCESS_TOKEN, ENCRYPTION_KEY y una clave de embeddings (VOYAGE o GEMINI).
"""

import os
import sys

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
    if not number or not token:
        print("Faltan WHATSAPP_PHONE_NUMBER_ID o WHATSAPP_ACCESS_TOKEN en .env")
        return 1
    migrate()
    with connect() as conn:
        tenant_id = ensure_demo_tenant(conn, embedder_from_env(PostgresUsageRecorder(conn)))
        register_whatsapp_channel(conn, tenant_id, phone_number_id=number, token=token)
    print(f"Acme Store lista (tenant {tenant_id}), con el número {number} asignado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
