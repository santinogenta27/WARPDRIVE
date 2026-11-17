"""Exportar los datos de una empresa (decisión 055): un .zip con un JSON por tabla.

Usa el mismo catálogo que el borrado (decisión 034): toda tabla con el id de la empresa entra
sola. Quedan afuera los secretos (credenciales y keys cifradas, claves del panel) y lo que no
sirve fuera del sistema (embeddings y el logo). Los hashes de la auditoría van, para poder
verificar la cadena.
"""

import io
import json
import uuid
import zipfile
from datetime import UTC, datetime
from typing import Any

import psycopg
from psycopg import sql

from app.companies.delete import _references, company, record

# Columnas que nunca salen en una exportación.
HIDDEN = {"embedding", "tsv", "password_hash", "credentials_enc", "token_enc", "logo", "code_hash"}
HIDDEN_SUFFIXES = ("_enc",)


def _visible(column: str) -> bool:
    return column not in HIDDEN and not column.endswith(HIDDEN_SUFFIXES)


def _settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    clean = dict(settings or {})
    if isinstance(clean.get("llm"), dict):
        clean["llm"] = {"provider": clean["llm"].get("provider")}  # la key cifrada no sale
    return clean


def export_company(conn: psycopg.Connection, tenant_id: uuid.UUID, *, operator: str) -> bytes:
    """El .zip con los datos de la empresa. Deja constancia en `operator_audit`."""
    found = company(conn, tenant_id)
    if found is None:
        raise ValueError("Esa empresa no existe.")
    buffer = io.BytesIO()
    counts: dict[str, int] = {}
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
        row = conn.execute(
            "select id, name, plan, status, created_at, settings from tenants where id = %s",
            (tenant_id,),
        ).fetchone()
        assert row is not None
        tenant = dict(zip(("id", "name", "plan", "status", "created_at"), row[:5], strict=True))
        tenant["settings"] = _settings(row[5])
        bundle.writestr("tenants.json", json.dumps([tenant], ensure_ascii=False, default=str))
        for table, column in sorted(_references(conn)):
            names = [
                str(r[0])
                for r in conn.execute(
                    "select column_name from information_schema.columns"
                    " where table_schema = 'public' and table_name = %s order by ordinal_position",
                    (table,),
                ).fetchall()
            ]
            shown = [n for n in names if _visible(n)]
            cursor = conn.execute(
                sql.SQL("select {} from {} where {} = %s").format(
                    sql.SQL(", ").join(sql.Identifier(n) for n in shown),
                    sql.Identifier(table),
                    sql.Identifier(column),
                ),
                (tenant_id,),
            )
            rows = [dict(zip(shown, values, strict=True)) for values in cursor.fetchall()]
            if rows:
                counts[table] = len(rows)
                bundle.writestr(
                    f"{table}.json", json.dumps(rows, ensure_ascii=False, default=str, indent=1)
                )
        bundle.writestr(
            "LEEME.txt",
            f"Datos de {found[0]} exportados el {datetime.now(UTC):%Y-%m-%d %H:%M} UTC.\n"
            "Un archivo JSON por tabla. No incluye credenciales, claves ni embeddings.\n",
        )
    record(conn, operator, "company_exported", tenant_id, found[0], {"filas": counts})
    return buffer.getvalue()
