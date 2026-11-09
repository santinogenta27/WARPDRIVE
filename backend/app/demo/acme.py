"""Carga de la empresa de demo Acme desde evals/fixtures/acme/ (datos ficticios)."""

import json
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.config import REPO_ROOT
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.llm.embedder import Embedder
from app.rag.indexer import index_document

ACME_DIR = REPO_ROOT / "evals" / "fixtures" / "acme"


@dataclass(frozen=True)
class AcmeSeed:
    tenant_id: uuid.UUID
    contacts: dict[str, uuid.UUID]  # etiqueta del fixture -> id del contacto
    phones: dict[str, str]


def _company() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ACME_DIR / "company.json").read_text(encoding="utf-8"))
    return data


def _settings(company: dict[str, Any]) -> dict[str, Any]:
    """Tono y búsqueda del cliente por email (verificación por código) del tenant."""
    return {"tone": company["tone"], "identity_lookup": company["identity_lookup"]}


def seed_acme(conn: psycopg.Connection, embedder: Embedder) -> AcmeSeed:
    """Crea el tenant Acme con sus tipos de caso, herramientas aprobadas, contactos y documentos."""
    company = _company()
    row = conn.execute(
        "insert into tenants (name, settings) values (%s, %s) returning id",
        (company["name"], Jsonb(_settings(company))),
    ).fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    contacts: dict[str, uuid.UUID] = {}
    with tenant_scope(conn, tenant_id):
        for case_type in company["case_types"]:
            path = ACME_DIR / "case_types" / f"{case_type['name']}.yaml"
            conn.execute(
                "insert into case_types (tenant_id, name, instructions, active)"
                " values (%s, %s, %s, %s)",
                (
                    tenant_id,
                    case_type["name"],
                    path.read_text(encoding="utf-8"),
                    case_type["active"],
                ),
            )
        for path in sorted((ACME_DIR / "tools").glob("*.json")):
            manifest = json.loads(path.read_text(encoding="utf-8"))
            conn.execute(
                "insert into tools (tenant_id, name, input_schema, manifest, risk, max_amount,"
                " approved) values (%s, %s, %s, %s, %s, %s, true)",
                (
                    tenant_id,
                    manifest["name"],
                    Jsonb(manifest["input_schema"]),
                    Jsonb(manifest),
                    manifest["risk"],
                    manifest["max_amount"],
                ),
            )
        for contact in company["contacts"]:
            found = conn.execute(
                "insert into contacts (tenant_id, phone_hash, verified_until) values"
                " (%s, %s, case when %s then now() + interval '30 days' end) returning id",
                (tenant_id, hash_phone(contact["phone"]), contact["verified"]),
            ).fetchone()
            assert found is not None
            contacts[contact["label"]] = found[0]
    index_acme_documents(conn, tenant_id, embedder)
    phones = {c["label"]: c["phone"] for c in company["contacts"]}
    return AcmeSeed(tenant_id=tenant_id, contacts=contacts, phones=phones)


def find_demo_tenant(conn: psycopg.Connection) -> uuid.UUID | None:
    """Tenant Acme permanente de la demo (marcado con settings.demo = 'acme')."""
    row = conn.execute("select id from tenants where settings ->> 'demo' = 'acme'").fetchone()
    return row[0] if row else None


def ensure_demo_tenant(conn: psycopg.Connection, embedder: Embedder) -> uuid.UUID:
    """Crea una sola vez el tenant Acme permanente de la demo. Si ya existe, actualiza su
    configuración (tono y búsqueda por email) desde el fixture."""
    tenant_id = find_demo_tenant(conn)
    if tenant_id is None:
        tenant_id = seed_acme(conn, embedder).tenant_id
    conn.execute(
        "update tenants set settings = settings || %s where id = %s",
        (Jsonb({**_settings(_company()), "demo": "acme"}), tenant_id),
    )
    return tenant_id


def acme_sandbox_client() -> TestClient:
    """API de Acme simulada en proceso, como en los evals (los datos viven en memoria)."""
    mocks = str(REPO_ROOT / "mocks" / "acme-api")
    if mocks not in sys.path:
        sys.path.insert(0, mocks)
    from acme_api import create_app

    return TestClient(create_app())


def _title(text: str, fallback: str) -> str:
    first = text.strip().splitlines()[0] if text.strip() else ""
    return first.lstrip("# ").strip() if first.startswith("#") else fallback


def index_acme_documents(conn: psycopg.Connection, tenant_id: uuid.UUID, embedder: Embedder) -> int:
    """Indexa los documentos de políticas de Acme. Devuelve cuántos indexó."""
    paths: list[Path] = sorted((ACME_DIR / "documents").glob("*.md"))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        index_document(
            conn, tenant_id, embedder, title=_title(text, path.stem), source=path.stem, text=text
        )
    return len(paths)
