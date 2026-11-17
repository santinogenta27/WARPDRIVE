"""Hito 9, tarea 9 — test de aceptación: producción mínima (decisión 055).

- Exportar los datos de una empresa: un .zip con un JSON por tabla, sin secretos.
- Rotar ENCRYPTION_KEY sin perder lo cifrado: con "nueva,vieja" se lee todo, `rotate_keys`
  vuelve a cifrar con la nueva y después alcanza con la nueva sola.
- Las plantillas legales existen y están marcadas para revisión de un abogado; render.md
  explica cómo rotar cada secreto y los backups.
"""

import io
import json
import uuid
import zipfile
from pathlib import Path

import psycopg
import pytest
from cryptography.fernet import Fernet

from app import crypto, rotate_keys
from app.companies import export
from app.companies.connection import save_access
from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.llm import tenant_llm

DOCS = Path(__file__).resolve().parents[2] / "docs"
SECRET = "clave-secreta-del-sistema-123"


def _company(conn: psycopg.Connection) -> uuid.UUID:
    tenant = create_company(
        conn, "Tienda Norte", "ana@tiendanorte.example", "clave-norte-1"
    ).tenant_id
    save_access(
        conn,
        tenant,
        base_url="https://api.tiendanorte.example",
        auth_kind="bearer",
        secret=SECRET,
    )
    tenant_llm.save_key(conn, tenant, "groq", "gsk_clave-de-la-empresa")
    return tenant


def test_exporta_los_datos_de_una_empresa_sin_secretos(conn: psycopg.Connection) -> None:
    tenant = _company(conn)
    data = export.export_company(conn, tenant, operator="operador@plataforma.example")
    with zipfile.ZipFile(io.BytesIO(data)) as bundle:
        names = set(bundle.namelist())
        everything = "".join(bundle.read(n).decode("utf-8") for n in names)
        tenants = json.loads(bundle.read("tenants.json"))
    assert {"tenants.json", "users.json", "LEEME.txt"} <= names
    assert tenants[0]["name"] == "Tienda Norte"
    assert tenants[0]["settings"]["llm"] == {"provider": "groq"}
    assert SECRET not in everything and "gsk_clave" not in everything
    assert "credentials_enc" not in everything and "password_hash" not in everything
    row = conn.execute(
        "select action from operator_audit where company_id = %s order by created_at desc",
        (tenant,),
    ).fetchone()
    assert row is not None and row[0] == "company_exported"


def test_rotar_la_clave_de_cifrado_no_pierde_nada(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv("ENCRYPTION_KEY", old)
    tenant = _company(conn)
    system_token = crypto.encrypt("token-del-canal")

    monkeypatch.setenv("ENCRYPTION_KEY", f"{new},{old}")
    assert crypto.decrypt(system_token) == "token-del-canal"  # las dos claves leen
    done = rotate_keys.rotate_all(conn, [tenant])  # la base de desarrollo es compartida
    assert done["system_connections.credentials_enc"] >= 1
    assert done["tenants.settings.llm"] >= 1

    monkeypatch.setenv("ENCRYPTION_KEY", new)  # solo la nueva
    with tenant_scope(conn, tenant):
        row = conn.execute(
            "select credentials_enc from system_connections where tenant_id = %s", (tenant,)
        ).fetchone()
    assert row is not None and crypto.decrypt_for(tenant, row[0]) == SECRET
    assert tenant_llm.provider_for(conn, tenant, lambda: None) is not None  # type: ignore[arg-type,return-value]


def test_plantillas_legales_y_guia_de_secretos() -> None:
    for name in ("terminos.md", "privacidad.md"):
        text = (DOCS / "legal" / name).read_text(encoding="utf-8")
        assert "BORRADOR" in text and "abogado" in text
    render = (DOCS / "render.md").read_text(encoding="utf-8")
    assert "Rotar los secretos" in render and "Backups" in render
    for secret in (
        "ENCRYPTION_KEY",
        "PANEL_PASSWORD",
        "GROQ_API_KEY",
        "RESEND_API_KEY",
        "DATABASE_URL",
        "WHATSAPP_APP_SECRET",
    ):
        assert secret in render.split("Rotar los secretos", 1)[1], secret
