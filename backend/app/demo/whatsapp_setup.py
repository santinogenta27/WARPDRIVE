"""Deja lista la demo por WhatsApp: tenant Acme permanente con el número de prueba asignado.

Uso: `make whatsapp-setup` (idempotente: se puede correr varias veces).
Necesita DATABASE_URL, WHATSAPP_PHONE_NUMBER_ID y una clave de embeddings (VOYAGE o GEMINI).
"""

import os
import sys

from psycopg.types.json import Jsonb

from app.config import load_dotenv
from app.db.connection import connect
from app.db.migrate import migrate
from app.demo.acme import ensure_demo_tenant
from app.llm.client import PostgresUsageRecorder
from app.llm.factory import embedder_from_env


def main() -> int:
    load_dotenv()
    number = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    if not number:
        print("Falta WHATSAPP_PHONE_NUMBER_ID en .env")
        return 1
    migrate()
    with connect() as conn:
        tenant_id = ensure_demo_tenant(conn, embedder_from_env(PostgresUsageRecorder(conn)))
        conn.execute(
            "update tenants set settings = settings || %s where id = %s",
            (Jsonb({"whatsapp": {"phone_number_id": number}}), tenant_id),
        )
    print(f"Acme Store lista (tenant {tenant_id}), con el número {number} asignado.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
