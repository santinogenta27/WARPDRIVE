"""Empresa de demo en inglés: Northwind Outfitters (Hito 8, tarea 3; decisión 038).

Una tienda ficticia de ropa outdoor de Portland, Oregon. Tiene su propio tenant, en inglés, con
sus documentos de políticas y su catálogo (`evals/fixtures/northwind/`), clientes de prueba y
una API simulada de pedidos (`mocks/northwind-api/`). Se configura por el mismo camino que
cualquier empresa: conecta su "sistema" (la API simulada), genera las herramientas desde su
OpenAPI, las aprueba con sus límites y activa sus tipos de caso.

Uso: `make demo-setup` (crea o regenera Acme y Northwind) o `python -m app.demo.northwind`.
Con `--reset`, además archiva las conversaciones de prueba de Northwind.
"""

import argparse
import json
import logging
import os
import secrets
import sys
import uuid
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app import i18n
from app.config import REPO_ROOT
from app.contacts import hash_phone
from app.db.connection import tenant_scope
from app.identity.email_code import EMAIL
from app.llm.embedder import Embedder
from app.rag.indexer import index_document

if TYPE_CHECKING:
    from app.agent.loop import Agent

NORTHWIND_DIR = REPO_ROOT / "evals" / "fixtures" / "northwind"
API_DIR = REPO_ROOT / "mocks" / "northwind-api"
DEMO_KEY = "northwind"  # tenants.settings.demo
BASE_URL = "demo:northwind"  # su "sistema": la API simulada en proceso
# Opcional (decisión 043): un email real que reciba el código de los pedidos del guion. Va solo
# en el entorno (Render o .env), nunca en el repo.
DEMO_EMAIL_ENV = "NORTHWIND_DEMO_CUSTOMER_EMAIL"
SCRIPT_ORDERS = ("NW-4521", "NW-4528")  # los pedidos de docs/demo-en.md
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class NorthwindSeed:
    tenant_id: uuid.UUID
    contacts: dict[str, uuid.UUID]  # etiqueta del fixture -> id del contacto
    phones: dict[str, str]


@dataclass(frozen=True)
class Setup:
    tenant_id: uuid.UUID
    created: bool
    panel_user: str
    password: str | None  # solo si se creó o se cambió en esta corrida
    chat_slug: str


def company() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((NORTHWIND_DIR / "company.json").read_text("utf-8"))
    return data


def openapi() -> dict[str, Any]:
    spec: dict[str, Any] = json.loads((API_DIR / "openapi.json").read_text(encoding="utf-8"))
    return spec


def demo_customers(email: str | None) -> list[dict[str, Any]]:
    """La clienta de los pedidos del guion, con `email` como segundo email (decisión 043): el
    código de verificación llega a una casilla real y el de los fixtures sigue andando. Vacío si
    no hay email, si no es un email o si ya es el de un cliente."""
    wanted = (email or "").strip().lower()
    if not wanted:
        return []
    if not EMAIL.fullmatch(wanted):
        logger.warning("%s no es un email: se ignora.", DEMO_EMAIL_ENV)
        return []
    customers = json.loads((API_DIR / "data" / "customers.json").read_text(encoding="utf-8"))
    if any(c["email"].lower() == wanted for c in customers):
        return []
    orders = json.loads((API_DIR / "data" / "orders.json").read_text(encoding="utf-8"))
    owners = {o["customer_phone"] for o in orders if o["id"] in SCRIPT_ORDERS}
    if len(owners) != 1:  # el guion es de una sola clienta
        raise ValueError(f"Los pedidos del guion no son de un solo cliente: {SCRIPT_ORDERS}")
    return [{**c, "email": wanted} for c in customers if c["phone"] in owners]


def sandbox_client(demo_email: str | None = None, today: date | None = None) -> TestClient:
    """La API simulada de Northwind en proceso (los datos viven en memoria). `demo_email`: un
    segundo email para la clienta del guion (ver `demo_customers`). `today`: el día respecto del
    que se calculan las fechas de los pedidos (por defecto, hoy: al arrancar y con cada "Reset
    demo")."""
    api = api_module()
    return TestClient(api.create_app(extra_customers=demo_customers(demo_email), today=today))


def api_module() -> ModuleType:
    """El módulo de la API simulada (`mocks/northwind-api/northwind_api.py`)."""
    mocks = str(API_DIR)
    if mocks not in sys.path:
        sys.path.insert(0, mocks)
    import northwind_api

    return northwind_api


@lru_cache(maxsize=1)
def shared_northwind() -> TestClient:
    """La API simulada que comparten el chat y el panel: lo que se aprueba en el panel se ve
    después en el pedido que consulta el agente. Se arma al primer uso y otra vez después de
    "Reset demo", con el email de `NORTHWIND_DEMO_CUSTOMER_EMAIL` si está definido."""
    return sandbox_client(os.environ.get(DEMO_EMAIL_ENV))


def _settings() -> dict[str, Any]:
    data = company()
    # La vista "detrás de escena" (decisión 041) está apagada por defecto; la demo la prende.
    return {
        "language": data["language"],
        "tone": data["tone"],
        "demo": DEMO_KEY,
        "behind_the_scenes": bool(data.get("behind_the_scenes")),
    }


def configure(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    """Configura Northwind por el flujo de cualquier empresa (decisión 026), igual que Acme:
    conecta la API simulada, genera las herramientas desde el OpenAPI, las aprueba con sus
    límites y activa los tipos de caso. Idempotente: lleva una base que ya tenía Northwind al
    estado de los fixtures."""
    from app.channels.web import widget
    from app.companies import connection as system
    from app.companies import review
    from app.onboarding.toolgen import _snake, build_manifests, operations, store_tools

    data = company()
    spec = openapi()
    system.connect_demo(conn, tenant_id, spec, base_url=BASE_URL)
    conn.execute(
        "update tenants set settings = settings || %s where id = %s",
        (Jsonb(_settings()), tenant_id),
    )
    instructions = {
        kind["name"]: (NORTHWIND_DIR / "case_types" / f"{kind['name']}.yaml").read_text("utf-8")
        for kind in data["case_types"]
    }
    by_tool = {_snake(op.operation_id): op.operation_id for op in operations(spec)}
    picked = {
        name: [
            by_tool[tool]
            for tool in review.parse_instructions(text).get("herramientas") or []
            if tool in by_tool
        ]
        for name, text in instructions.items()
    }
    store_tools(conn, tenant_id, build_manifests(spec, picked))
    with tenant_scope(conn, tenant_id):
        for name, text in instructions.items():
            conn.execute(
                "insert into case_types (tenant_id, name, instructions, active)"
                " values (%s, %s, %s, false) on conflict (tenant_id, name) do nothing",
                (tenant_id, name, text),
            )
    limits: dict[str, dict[str, Any]] = data.get("tool_limits") or {}
    for tool in review.tools(conn, tenant_id):
        mine = limits.get(tool.name, {})
        review.set_tool(
            conn,
            tenant_id,
            tool.id,
            approved=True,
            risk=mine.get("risk", review.UNSET),
            max_amount=mine.get("max_amount"),
            approval_over=mine.get("approval_over"),
            amount_from=mine.get("amount_from"),
            alternatives=mine.get("alternatives"),
            retention=mine.get("retention", []),
        )
    active = {kind["name"]: bool(kind["active"]) for kind in data["case_types"]}
    for kind in review.case_types(conn, tenant_id):
        if kind.name in instructions:
            review.set_case_type(
                conn,
                tenant_id,
                kind.id,
                active=active[kind.name],
                instructions=instructions[kind.name],
            )
    # Ventas cruzadas (decisión 061): las reglas de los fixtures, sin duplicar al regenerar.
    from app.sales import upsell

    for rule in upsell.load(conn, tenant_id):
        upsell.remove(conn, tenant_id, rule.id)
    for rule in data.get("upsell_rules") or []:
        upsell.add(
            conn,
            tenant_id,
            rule["product"],
            list(rule["case_types"]),
            upsell.parse_discount(str(rule["max_discount"])),
        )
    brand = data.get("widget") or {}
    current = widget.load(conn, tenant_id)
    widget.save(
        conn,
        tenant_id,
        name=data["name"],
        accent=str(brand.get("accent") or widget.DEFAULT_ACCENT),
        domains="\n".join(current.domains),
        suggestions="\n".join(brand.get("suggestions") or []),
    )


def index_documents(conn: psycopg.Connection, tenant_id: uuid.UUID, embedder: Embedder) -> int:
    """Indexa las políticas y el catálogo. Antes borra los que ya estaban (regenerar no
    duplica)."""
    paths: list[Path] = sorted((NORTHWIND_DIR / "documents").glob("*.md"))
    with tenant_scope(conn, tenant_id):
        conn.execute("delete from chunks where tenant_id = %s", (tenant_id,))
        conn.execute("delete from documents where tenant_id = %s", (tenant_id,))
    for path in paths:
        text = path.read_text(encoding="utf-8")
        first = text.strip().splitlines()[0]
        title = first.lstrip("# ").strip() if first.startswith("#") else path.stem
        index_document(conn, tenant_id, embedder, title=title, source=path.stem, text=text)
    return len(paths)


def _new_tenant(conn: psycopg.Connection) -> uuid.UUID:
    row = conn.execute(
        "insert into tenants (name, settings) values (%s, %s) returning id",
        (company()["name"], Jsonb(_settings())),
    ).fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    return tenant_id


def seed_northwind(conn: psycopg.Connection, embedder: Embedder) -> NorthwindSeed:
    """Un tenant Northwind nuevo con sus contactos de prueba (para tests y evals)."""
    tenant_id = _new_tenant(conn)
    configure(conn, tenant_id)
    contacts: dict[str, uuid.UUID] = {}
    with tenant_scope(conn, tenant_id):
        for contact in company()["contacts"]:
            found = conn.execute(
                "insert into contacts (tenant_id, phone_hash, verified_until) values"
                " (%s, %s, case when %s then now() + interval '30 days' end) returning id",
                (tenant_id, hash_phone(contact["phone"]), contact["verified"]),
            ).fetchone()
            assert found is not None
            contacts[contact["label"]] = found[0]
    index_documents(conn, tenant_id, embedder)
    phones = {c["label"]: c["phone"] for c in company()["contacts"]}
    return NorthwindSeed(tenant_id=tenant_id, contacts=contacts, phones=phones)


def find_tenant(conn: psycopg.Connection) -> uuid.UUID | None:
    row = conn.execute(
        "select id from tenants where settings ->> 'demo' = %s and status = 'active'", (DEMO_KEY,)
    ).fetchone()
    return row[0] if row else None


def ensure_northwind(
    conn: psycopg.Connection, embedder: Embedder, *, password: str | None = None
) -> Setup:
    """Crea Northwind una sola vez o, si ya existe, lo regenera: configuración, documentos,
    widget y chat. El usuario del panel conserva su clave salvo que se pase otra."""
    from app.channels.whatsapp.routing import register_whatsapp_channel
    from app.companies.create import chat_address
    from app.panel import auth

    data = company()
    tenant_id = find_tenant(conn)
    created = tenant_id is None
    if tenant_id is None:
        tenant_id = _new_tenant(conn)
    configure(conn, tenant_id)
    index_documents(conn, tenant_id, embedder)
    register_whatsapp_channel(
        conn, tenant_id, phone_number_id=chat_address(data["chat_slug"]), kind="web"
    )
    email = str(data["panel_user"])
    with tenant_scope(conn, tenant_id):
        row = conn.execute("select id from users where email = %s", (email,)).fetchone()
        new_password = password or (None if row else secrets.token_urlsafe(12))
        if row is None:
            conn.execute(
                "insert into users (tenant_id, email, role, password_hash)"
                " values (%s, %s, 'owner', %s)",
                (tenant_id, email, auth.hash_password(str(new_password))),
            )
        elif new_password:
            conn.execute(
                "update users set password_hash = %s where id = %s",
                (auth.hash_password(new_password), row[0]),
            )
    return Setup(tenant_id, created, email, new_password, str(data["chat_slug"]))


def reset(conn: psycopg.Connection, tenant_id: uuid.UUID) -> int:
    """Archiva las conversaciones de prueba (la auditoría queda íntegra). Los pedidos de la API
    simulada vuelven a su estado inicial cuando se reinicia la API, o desde el panel."""
    from app.cases.archive import archive_cases

    archived = archive_cases(conn, tenant_id, None)
    shared_northwind.cache_clear()
    return archived


def build_agent(conn: psycopg.Connection, client: TestClient) -> "Agent":
    """Agente de Northwind con los modelos del entorno y su API simulada."""
    from app.agent.loop import Agent
    from app.llm.client import LLMClient, PostgresUsageRecorder
    from app.llm.factory import embedder_from_env, provider_from_env
    from app.policy.guardian import Guardian
    from app.tools.runtime import ToolRuntime

    recorder = PostgresUsageRecorder(conn)
    llm = LLMClient(provider=provider_from_env(), recorder=recorder)
    return Agent(conn, llm, embedder_from_env(recorder), Guardian(conn, ToolRuntime(client)))


def main(argv: list[str] | None = None) -> int:
    from app.config import load_dotenv
    from app.db.connection import connect
    from app.db.migrate import migrate
    from app.llm.client import PostgresUsageRecorder
    from app.llm.factory import embedder_from_env

    parser = argparse.ArgumentParser(description="Crea o regenera Northwind Outfitters")
    parser.add_argument("--reset", action="store_true", help="archiva las conversaciones")
    args = parser.parse_args(argv)
    load_dotenv()
    migrate()
    with connect() as conn:
        setup = ensure_northwind(
            conn,
            embedder_from_env(PostgresUsageRecorder(conn)),
            password=os.environ.get("NORTHWIND_PASSWORD") or None,
        )
        archived = reset(conn, setup.tenant_id) if args.reset else 0
    verb = "creada" if setup.created else "regenerada"
    print(f"Northwind Outfitters {verb} (tenant {setup.tenant_id}, idioma: {i18n.NAMES['en']}).")
    print(f"  Chat: /c/{setup.chat_slug} · Panel: {setup.panel_user}")
    if setup.password:
        print(f"  Clave del panel: {setup.password} (guardala: no se vuelve a mostrar)")
    if args.reset:
        print(f"  {archived} conversaciones archivadas.")
    if demo_customers(os.environ.get(DEMO_EMAIL_ENV)):
        # Sin mostrar el email: la salida puede quedar en un log.
        print(
            f"  Los pedidos del guion ({', '.join(SCRIPT_ORDERS)}) responden también al email de"
            f" {DEMO_EMAIL_ENV} (en la API que arranque con esa variable)."
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
