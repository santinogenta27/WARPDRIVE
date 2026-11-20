"""Prueba gratis self-service con la tienda del visitante (decisión 075).

1. `start`: el visitante deja la URL de su tienda y su email; se le manda un código.
2. `confirm`: con el código correcto se crea la empresa de prueba (un tenant común, con su chat y
   "detrás de escena") y se encolan el armado y el borrado a los 7 días.
3. `run_build` (job `trial_build`): rastrea el sitio, indexa sus páginas, lee sus productos y
   configura el mismo agente que se vende contra la API simulada de Northwind, con sus productos
   y pedidos de ejemplo `DEMO-…`. Idempotente: cada paso reemplaza lo suyo.
4. `convert`: "Empezar mi piloto" convierte la empresa de prueba en una real, sin los pedidos de
   ejemplo. `run_expire_job` la borra a los 7 días si no se convirtió.
"""

import hashlib
import hmac
import os
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
import psycopg
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb

from app.companies.create import EMAIL, Company, slugify
from app.db.connection import tenant_scope
from app.jobs.queue import Job, enqueue
from app.llm.embedder import Embedder
from app.mailer import Email, Mailer
from app.trial import catalog

JOB_KIND = "trial_build"
EXPIRE_JOB = "trial_expire"
MAX_MESSAGES = 30  # por prueba, sumando todas las sesiones del chat
TRIAL_DAYS = 7
CODE_TTL = timedelta(minutes=15)
MAX_CODE_ATTEMPTS = 5
MAX_PER_IP = 3  # pruebas por IP por día
MAX_PRODUCTS = 12
SAMPLE_PHONE = "+10000001001"  # el cliente de los pedidos de ejemplo (el visitante)
BASE_PREFIX = "demo:trial:"
CATALOG_SOURCE = "catalogo_de_la_prueba"
CASE_TYPES = ("where_is_my_order", "refund", "shopping", "change_shipping_address")
# Reglas de la prueba, en código por el guardián: cupón hasta 10 % (si no, envío express) y todo
# reembolso pide aprobación, sea cual sea la moneda de la tienda.
TOOL_LIMITS: dict[str, dict[str, Any]] = {
    "iniciar_reembolso": {"risk": "medium", "approval_over": 0, "retention": []},
    "ofrecer_cupon": {
        "max_amount": 10,
        "amount_from": "input.percent",
        "alternatives": ["ofrecer_envio_express"],
    },
}
HERE = Path(__file__).resolve().parent
ACTIVE = {"building", "ready"}
TEXTS: dict[str, dict[str, Any]] = {
    "es": {
        "subject": "Tu código para probar el asistente: {code}",
        "body": (
            "Tu código es {code}. Vence en 15 minutos.\n\nCon él armamos la prueba con tu tienda "
            "({site}). Si no la pediste, ignorá este email."
        ),
        "customer": "Cliente de ejemplo",
        "address": "Av. Siempreviva 742, Ciudad de ejemplo",
        "carrier": "Correo de ejemplo",
        "delay": "Demora en el centro de distribución del correo",
        "payment": "Tarjeta terminada en 4242",
        "suggestions": [
            "¿Dónde está mi pedido DEMO-1001?",
            "Quiero devolver el pedido DEMO-1002",
            "¿Me hacen un 50 % de descuento?",
            "Busco {product}",
        ],
        "generic": ["Producto de ejemplo A", "Producto de ejemplo B", "Producto de ejemplo C"],
        "contact_subject": "Prueba gratis: quiere hablar · {domain}",
        "contact_body": "Sitio: {site}\nEmail: {email}\nEstado de la prueba: {status}\n",
    },
    "en": {
        "subject": "Your code to try the assistant: {code}",
        "body": (
            "Your code is {code}. It expires in 15 minutes.\n\nWe'll use it to set up the trial "
            "with your store ({site}). If you didn't ask for it, ignore this email."
        ),
        "customer": "Sample customer",
        "address": "742 Evergreen Terrace, Sample City",
        "carrier": "Sample Carrier",
        "delay": "Delay at the carrier's sorting center",
        "payment": "Card ending in 4242",
        "suggestions": [
            "Where is my order DEMO-1001?",
            "I want to return order DEMO-1002",
            "Can I get a 50% discount?",
            "I'm looking for {product}",
        ],
        "generic": ["Sample product A", "Sample product B", "Sample product C"],
        "contact_subject": "Free trial: wants to talk · {domain}",
        "contact_body": "Site: {site}\nEmail: {email}\nTrial status: {status}\n",
    },
}


class TrialError(ValueError):
    """Dato inválido o límite alcanzado: el mensaje se le muestra al visitante."""


ERRORS = {
    "es": {
        "site": "La dirección de la tienda no es válida. Escribila como tutienda.com.",
        "email": "El email no es válido.",
        "taken": "Ya hay una prueba con ese email o con esa tienda. Si querés seguir, escribinos.",
        "ip": "Ya se pidieron varias pruebas desde tu conexión hoy. Probá de nuevo mañana.",
        "code": "El código no es correcto.",
        "code_expired": "El código venció o se usó demasiadas veces. Pedí una prueba nueva.",
    },
    "en": {
        "site": "The store address isn't valid. Write it like yourstore.com.",
        "email": "The email isn't valid.",
        "taken": "There's already a trial with that email or store. Write to us to continue.",
        "ip": "Several trials were requested from your connection today. Try again tomorrow.",
        "code": "The code isn't right.",
        "code_expired": "The code expired or was tried too many times. Request a new trial.",
    },
}


def _error(language: str, key: str) -> TrialError:
    return TrialError(ERRORS["en" if language == "en" else "es"][key])


@dataclass(frozen=True)
class Trial:
    id: uuid.UUID
    tenant_id: uuid.UUID | None
    site_url: str
    domain: str
    email: str
    language: str
    status: str
    progress: dict[str, Any]
    products: list[dict[str, Any]]
    messages: int
    pilot_requested_at: datetime | None
    contact_requested_at: datetime | None
    converted_at: datetime | None
    created_at: datetime

    @property
    def name(self) -> str:
        return str(self.progress.get("name") or self.domain)


@dataclass(frozen=True)
class View:
    trial: Trial
    chat_slug: str | None
    orders: list[dict[str, Any]]


_COLUMNS = (
    "id, company_id, site_url, domain, email, language, status, progress, products, messages,"
    " pilot_requested_at, contact_requested_at, converted_at, created_at"
)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _row(row: tuple[Any, ...] | None) -> Trial | None:
    return Trial(*row) if row is not None else None


def find(conn: psycopg.Connection, token: str) -> Trial | None:
    return _row(
        conn.execute(
            f"select {_COLUMNS} from trials where token_hash = %s", (_hash(token),)
        ).fetchone()
    )


def get(conn: psycopg.Connection, trial_id: uuid.UUID, *, lock: bool = False) -> Trial | None:
    query = f"select {_COLUMNS} from trials where id = %s" + (" for update" if lock else "")
    return _row(conn.execute(query, (trial_id,)).fetchone())


def for_tenant(conn: psycopg.Connection, tenant_id: uuid.UUID) -> Trial | None:
    """La prueba en curso de esa empresa (None si no es una prueba o ya se convirtió)."""
    return _row(
        conn.execute(
            f"select {_COLUMNS} from trials where company_id = %s and status = any(%s)",
            (tenant_id, list(ACTIVE)),
        ).fetchone()
    )


def normalize_site(raw: str, language: str = "es") -> tuple[str, str]:
    """(dirección del sitio, dominio sin `www.`) de lo que escribió el visitante."""
    text = raw.strip()
    if text and "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme not in ("http", "https")
        or "." not in host
        or len(text) > 300
        or not re.fullmatch(r"[a-z0-9.-]+", host)
    ):
        raise _error(language, "site")
    domain = host.removeprefix("www.")
    return f"{parts.scheme}://{parts.netloc.lower()}{parts.path or '/'}", domain


def cost_cap() -> Decimal:
    return Decimal(os.environ.get("TRIAL_COST_CAP_USD") or "0.50")


def start(
    conn: psycopg.Connection,
    mailer: Mailer,
    *,
    site: str,
    email: str,
    language: str,
    ip_hash: str,
) -> str:
    """Guarda la prueba pedida y manda el código. Devuelve el token del enlace de la prueba."""
    language = "en" if language == "en" else "es"
    url, domain = normalize_site(site, language)
    email = email.strip().lower()
    if not EMAIL.match(email) or len(email) > 200:
        raise _error(language, "email")
    recent = conn.execute(
        "select count(*) from trials where ip_hash = %s and created_at > now() - interval '1 day'",
        (ip_hash,),
    ).fetchone()
    if recent is not None and recent[0] >= MAX_PER_IP:
        raise _error(language, "ip")
    token = secrets.token_urlsafe(24)
    code = f"{secrets.randbelow(10**6):06d}"
    row = conn.execute(
        "insert into trials (site_url, domain, email, language, token_hash, code_hash,"
        " code_expires_at, ip_hash) values (%s, %s, %s, %s, %s, %s, now() + %s, %s)"
        " on conflict do nothing returning id",
        (url, domain, email, language, _hash(token), "", CODE_TTL, ip_hash),
    ).fetchone()
    if row is None:  # una prueba por email y por dominio (índices únicos)
        raise _error(language, "taken")
    conn.execute(
        "update trials set code_hash = %s where id = %s", (_code_hash(row[0], code), row[0])
    )
    texts = TEXTS[language]
    mailer.send(
        Email(
            to=email,
            subject=texts["subject"].format(code=code),
            body=texts["body"].format(code=code, site=url),
        )
    )
    return token


def _code_hash(trial_id: uuid.UUID, code: str) -> str:
    return _hash(f"{trial_id}:{code}")


def _new_tenant(conn: psycopg.Connection, trial: Trial) -> uuid.UUID:
    from app.channels.whatsapp.routing import register_whatsapp_channel
    from app.companies.create import chat_address
    from app.llm import spending

    tone = "english" if trial.language == "en" else "rioplatense"
    settings = {
        "tone": tone,
        "language": trial.language,
        "trial": str(trial.id),
        "behind_the_scenes": True,
    }
    name = f"Prueba · {trial.domain}"[:80]
    taken = conn.execute(
        "select 1 from tenants where lower(name) = %s and status <> 'deleted'", (name.lower(),)
    ).fetchone()
    if taken:
        name = f"{name[:70]} · {secrets.token_hex(3)}"
    row = conn.execute(
        "insert into tenants (name, settings) values (%s, %s) returning id",
        (name, Jsonb(settings)),
    ).fetchone()
    assert row is not None
    tenant_id: uuid.UUID = row[0]
    slug = f"prueba-{slugify(trial.domain)[:30]}-{secrets.token_hex(4)}"
    register_whatsapp_channel(conn, tenant_id, phone_number_id=chat_address(slug), kind="web")
    spending.set_budget(conn, tenant_id, cost_cap())
    return tenant_id


def confirm(conn: psycopg.Connection, token: str, code: str) -> Trial:
    """Con el código correcto crea la empresa de prueba y encola el armado y el borrado."""
    found = find(conn, token)
    if found is None:
        raise TrialError("La prueba no existe.")
    trial = get(conn, found.id, lock=True)
    assert trial is not None
    if trial.status != "code_sent":
        return trial  # ya confirmada (doble clic o recarga)
    row = conn.execute(
        "select code_hash, code_attempts, code_expires_at < now() from trials where id = %s",
        (trial.id,),
    ).fetchone()
    assert row is not None
    stored, attempts, expired = row
    if expired or attempts >= MAX_CODE_ATTEMPTS:
        raise _error(trial.language, "code_expired")
    if not hmac.compare_digest(stored or "", _code_hash(trial.id, code.strip())):
        conn.execute(
            "update trials set code_attempts = code_attempts + 1 where id = %s", (trial.id,)
        )
        raise _error(trial.language, "code")
    tenant_id = _new_tenant(conn, trial)
    conn.execute(
        "update trials set company_id = %s, status = 'building', code_hash = null,"
        " progress = %s where id = %s",
        (tenant_id, Jsonb({"step": "crawl"}), trial.id),
    )
    payload = {"trial_id": str(trial.id)}  # sin tenant_id: el borrado de la empresa no lo toca
    enqueue(conn, JOB_KIND, payload, idempotency_key=f"trial-build-{trial.id}", max_attempts=3)
    enqueue(
        conn,
        EXPIRE_JOB,
        payload,
        idempotency_key=f"trial-expire-{trial.id}",
        run_after=datetime.now(UTC) + timedelta(days=TRIAL_DAYS),
    )
    confirmed = get(conn, trial.id)
    assert confirmed is not None
    return confirmed


# -- armado -----------------------------------------------------------------------------------
def _store_name(title: str, domain: str) -> str:
    first = re.split(r"\s+[|–—-]\s+", title.strip())[0].strip()
    return first[:80] if 2 <= len(first) <= 80 else domain


def _progress(conn: psycopg.Connection, trial_id: uuid.UUID, **values: Any) -> None:
    conn.execute(
        "update trials set progress = progress || %s where id = %s", (Jsonb(values), trial_id)
    )


def _catalog_text(products: list[dict[str, Any]], language: str) -> str:
    title = "# Catalog" if language == "en" else "# Catálogo"
    lines = [
        f"- {p['name']}: {p['currency']} {p['price']:g}" + (f" ({p['url']})" if p["url"] else "")
        for p in products
    ]
    return "\n".join([title, "", *lines])


def run_build(
    conn: psycopg.Connection,
    trial_id: uuid.UUID,
    *,
    embedder: Embedder,
    http: httpx.Client | None = None,
) -> None:
    """Arma la prueba. Correrlo de nuevo reemplaza lo anterior sin duplicar nada."""
    from app.onboarding.ingest import crawl, index_document, remove_source, save_site

    trial = get(conn, trial_id)
    if trial is None or trial.status not in ACTIVE or trial.tenant_id is None:
        return
    tenant_id = trial.tenant_id
    found: list[catalog.Product] = []

    def on_page(url: str, markup: str) -> None:
        found.extend(catalog.extract(markup, url))

    try:
        pages = crawl(trial.site_url, http, on_page=on_page)
    except Exception as error:  # noqa: BLE001 — se informa en la página, sin reintentar
        conn.execute(
            "update trials set status = 'failed', progress = progress || %s where id = %s",
            (Jsonb({"error": str(error)[:300]}), trial_id),
        )
        return
    save_site(conn, tenant_id, trial.site_url, pages, embedder)
    name = _store_name(pages[0].title, trial.domain)
    _progress(conn, trial_id, step="products", pages=len(pages), name=name)
    products = [p.__dict__ for p in catalog.unique(found, MAX_PRODUCTS)]
    generic = not products
    if generic:
        currency = "USD" if trial.language == "en" else "ARS"
        products = [
            {"name": n, "price": float(10 * (i + 2)), "currency": currency, "image": "", "url": ""}
            for i, n in enumerate(TEXTS[trial.language]["generic"])
        ]
    conn.execute("update trials set products = %s where id = %s", (Jsonb(products), trial_id))
    remove_source(conn, tenant_id, CATALOG_SOURCE)
    index_document(
        conn,
        tenant_id,
        embedder,
        title="Catalog" if trial.language == "en" else "Catálogo",
        source=CATALOG_SOURCE,
        text=_catalog_text(products, trial.language),
    )
    _configure(conn, trial, name, products)
    conn.execute(
        "update trials set status = 'ready', progress = progress || %s where id = %s",
        (Jsonb({"step": "done", "products": len(products), "generic": generic}), trial_id),
    )
    _SANDBOXES.pop(tenant_id, None)


def _configure(
    conn: psycopg.Connection, trial: Trial, name: str, products: list[dict[str, Any]]
) -> None:
    from app.channels.web import widget
    from app.demo import northwind

    assert trial.tenant_id is not None
    instructions = {
        kind: (HERE / "refund.yaml").read_text("utf-8")
        if kind == "refund"
        else northwind.case_type_text(kind)
        for kind in CASE_TYPES
    }
    northwind.configure_tools(
        conn,
        trial.tenant_id,
        base_url=base_url(trial.tenant_id),
        instructions=instructions,
        limits=TOOL_LIMITS,
        active=dict.fromkeys(CASE_TYPES, True),
    )
    first = products[0]["name"] if products else ""
    suggestions = [s.format(product=first) for s in TEXTS[trial.language]["suggestions"]]
    widget.save(
        conn,
        trial.tenant_id,
        name=name,
        accent=widget.DEFAULT_ACCENT,
        domains="",
        suggestions="\n".join(suggestions),
    )


def run_build_job(conn: psycopg.Connection, job: Job) -> None:
    from app.llm.client import PostgresUsageRecorder
    from app.llm.factory import embedder_from_env

    embedder = embedder_from_env(PostgresUsageRecorder(conn))
    run_build(conn, uuid.UUID(str(job.payload["trial_id"])), embedder=embedder)


# -- la tienda de ejemplo ---------------------------------------------------------------------
_SANDBOXES: dict[uuid.UUID, TestClient] = {}


def base_url(tenant_id: uuid.UUID) -> str:
    return f"{BASE_PREFIX}{tenant_id}"


def reset_sandboxes() -> None:
    _SANDBOXES.clear()


def mock_products(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Los productos de la tienda en el formato de la API simulada (un talle, con stock)."""
    return [
        {
            "code": f"P{n:02d}",
            "sku_prefix": "DEMO",
            "name": p["name"],
            "price": p["price"],
            "currency": p["currency"],
            "image": p.get("image") or "",
            "url": p.get("url") or "",
            "category": "",
            "description": p["name"],
            "sizes": ["One size"],
            "colors": ["Standard"],
            "stock": {"Standard": {"One size": 10}},
        }
        for n, p in enumerate(products, start=1)
    ]


def sample_orders(
    products: list[dict[str, Any]], language: str, today: date
) -> list[dict[str, Any]]:
    """DEMO-1001 en camino con demora, DEMO-1002 entregado y DEMO-1003 sin despachar."""
    texts = TEXTS["en" if language == "en" else "es"]
    items = mock_products(products)

    def line(n: int) -> dict[str, Any]:
        product = items[n % len(items)]
        return {
            "sku": f"DEMO-{product['code']}-STA-OS",
            "name": product["name"],
            "size": "One size",
            "color": "Standard",
            "quantity": 1,
            "unit_price": product["price"],
        }

    def moment(days: int) -> str:
        return f"{(today - timedelta(days=days)).isoformat()}T11:00:00-03:00"

    def order(n: int, status: str, created: int, **dates: Any) -> dict[str, Any]:
        item = line(n)
        return {
            "id": f"DEMO-{1001 + n}",
            "customer_phone": SAMPLE_PHONE,
            "customer_name": texts["customer"],
            "payment_method": texts["payment"],
            "status": status,
            "items": [item],
            "total": item["unit_price"],
            "currency": items[n % len(items)]["currency"],
            "shipping_address": texts["address"],
            "created_at": moment(created),
            "shipped_at": None,
            "delivered_at": None,
            "estimated_delivery": None,
            "carrier": None,
            "tracking_number": None,
            "delay": None,
            **dates,
        }

    return [
        order(
            0,
            "shipped",
            6,
            shipped_at=moment(5),
            estimated_delivery=(today - timedelta(days=1)).isoformat(),
            carrier=texts["carrier"],
            tracking_number="DEMO00000001001",
            delay={
                "reason": texts["delay"],
                "new_estimated_delivery": (today + timedelta(days=2)).isoformat(),
            },
        ),
        order(
            1,
            "delivered",
            12,
            shipped_at=moment(11),
            delivered_at=moment(8),
            estimated_delivery=(today - timedelta(days=8)).isoformat(),
            carrier=texts["carrier"],
            tracking_number="DEMO00000001002",
        ),
        order(2, "paid", 1),
    ]


def sandbox(conn: psycopg.Connection, tenant_id: uuid.UUID) -> TestClient:
    """La API simulada de la prueba, en memoria y por proceso. Se arma desde la fila de la prueba:
    un reinicio la vuelve a su estado inicial."""
    cached = _SANDBOXES.get(tenant_id)
    if cached is not None:
        return cached
    from app.companies.create import chat_slug
    from app.demo import northwind

    trial = for_tenant(conn, tenant_id)
    if trial is None:
        raise RuntimeError("La empresa no tiene una prueba en curso")
    api = northwind.api_module()
    products = trial.products or []
    public = (os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")
    client = TestClient(
        api.create_app(
            sample_orders(products, trial.language, api.store_today()),
            catalog=mock_products(products),
            customers=[
                {
                    "id": "C-1001",
                    "name": TEXTS[trial.language]["customer"],
                    "email": trial.email,
                    "phone": SAMPLE_PHONE,
                }
            ],
            order_prefix="DEMO",
            currency=products[0]["currency"] if products else "USD",
            checkout_base=f"{public}/demo/{chat_slug(conn, tenant_id)}",
        )
    )
    _SANDBOXES[tenant_id] = client
    return client


def sandboxes(conn: psycopg.Connection, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Para `runtime_for`: la API simulada de la prueba por su URL. Solo se arma si la conexión
    de la empresa apunta ahí."""
    return {base_url(tenant_id): lambda: sandbox(conn, tenant_id)}


def view(conn: psycopg.Connection, trial: Trial) -> View:
    from app.companies.create import chat_slug

    if trial.tenant_id is None or trial.status not in ACTIVE:
        return View(trial, None, [])
    slug = chat_slug(conn, trial.tenant_id)
    orders: list[dict[str, Any]] = []
    if trial.status == "ready":
        found = sandbox(conn, trial.tenant_id).get(
            "/orders", params={"customer_phone": SAMPLE_PHONE}
        )
        orders = found.json().get("orders", []) if found.status_code == 200 else []
    return View(trial, slug, orders)


# -- límites del chat -------------------------------------------------------------------------
def chat_allowance(conn: psycopg.Connection, tenant_id: uuid.UUID) -> int | None:
    """Mensajes que le quedan a la prueba (0 si pasó el tope de costo). None: no es una prueba."""
    from app.llm import spending

    trial = for_tenant(conn, tenant_id)
    if trial is None:
        return None
    if spending.over(conn, tenant_id):
        return 0
    return max(MAX_MESSAGES - trial.messages, 0)


def count_message(conn: psycopg.Connection, tenant_id: uuid.UUID) -> None:
    conn.execute(
        "update trials set messages = messages + 1 where company_id = %s and status = any(%s)",
        (tenant_id, list(ACTIVE)),
    )


# -- mini panel, piloto y contacto ------------------------------------------------------------
def pending_approvals(conn: psycopg.Connection, tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    from app.panel.labels import action_label

    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select a.id, t.name, a.params from approvals a join tools t on t.id = a.tool_id"
            " where a.status = 'pending' order by a.created_at"
        ).fetchall()
    return [
        {"id": str(approval_id), "tool": tool, "label": action_label(tool, params)}
        for approval_id, tool, params in rows
    ]


def request_pilot(conn: psycopg.Connection, trial_id: uuid.UUID) -> None:
    conn.execute(
        "update trials set pilot_requested_at = coalesce(pilot_requested_at, now()) where id = %s",
        (trial_id,),
    )


def contact_operator(conn: psycopg.Connection, mailer: Mailer, trial: Trial) -> bool:
    """Avisa al operador por email, una sola vez por prueba. Devuelve si mandó el aviso."""
    from app.panel import auth

    row = conn.execute(
        "update trials set contact_requested_at = now()"
        " where id = %s and contact_requested_at is null returning id",
        (trial.id,),
    ).fetchone()
    operator = auth.operator_email()
    if row is None or not operator:
        return False
    texts = TEXTS[trial.language]
    mailer.send(
        Email(
            to=operator,
            subject=texts["contact_subject"].format(domain=trial.domain),
            body=texts["contact_body"].format(
                site=trial.site_url, email=trial.email, status=trial.status
            ),
        )
    )
    return True


def convert(
    conn: psycopg.Connection,
    trial_id: uuid.UUID,
    *,
    company: str,
    email: str,
    password_hash: str,
) -> Company:
    """Convierte la empresa de prueba en una real: el nombre elegido, su usuario admin, sin la
    marca ni los límites de la prueba y sin la tienda de ejemplo. Quedan las páginas del sitio y
    el catálogo."""
    from app.cases.archive import archive_cases
    from app.companies.create import CompanyError, chat_slug
    from app.panel import auth

    trial = get(conn, trial_id, lock=True)
    if trial is None or trial.status not in ACTIVE or trial.tenant_id is None:
        raise CompanyError("La prueba ya no está disponible. Registrate de nuevo.")
    tenant_id = trial.tenant_id
    name, email = company.strip(), email.strip().lower()
    if not 2 <= len(name) <= 80:
        raise CompanyError("El nombre de la empresa tiene que tener entre 2 y 80 caracteres.")
    taken = conn.execute(
        "select 1 from tenants where lower(name) = %s and status <> 'deleted' and id <> %s",
        (name.lower(), tenant_id),
    ).fetchone()
    if taken:
        raise CompanyError("Ya hay una empresa con ese nombre.")
    used = conn.execute(
        "select 1 from users where lower(email) = %s and password_hash is not null", (email,)
    ).fetchone()
    if used or email == auth.operator_email():
        raise CompanyError("Ese email ya tiene un usuario.")
    with tenant_scope(conn, tenant_id):
        conn.execute(
            "update tenants set name = %s, settings = (settings - 'trial' - 'behind_the_scenes'"
            " - 'monthly_budget_usd') || %s where id = %s",
            (name, Jsonb({"from_trial": str(trial.id)}), tenant_id),
        )
        user = conn.execute(
            "insert into users (tenant_id, email, role, password_hash)"
            " values (%s, %s, 'owner', %s) returning id",
            (tenant_id, email, password_hash),
        ).fetchone()
        assert user is not None
        # Sin los pedidos de ejemplo: se apagan las herramientas de la API simulada.
        conn.execute("update tools set approved = false where tenant_id = %s", (tenant_id,))
        conn.execute("update case_types set active = false where tenant_id = %s", (tenant_id,))
    # Y se desconecta (con la conexión dueña, como el borrado de empresas).
    conn.execute("delete from system_connections where tenant_id = %s", (tenant_id,))
    archive_cases(conn, tenant_id, None)
    conn.execute(
        "update trials set status = 'converted', converted_at = now(),"
        " pilot_requested_at = coalesce(pilot_requested_at, now()) where id = %s",
        (trial.id,),
    )
    _SANDBOXES.pop(tenant_id, None)
    return Company(tenant_id, user[0], name, chat_slug(conn, tenant_id) or "")


# -- borrado a los 7 días ---------------------------------------------------------------------
def expire(conn: psycopg.Connection, trial_id: uuid.UUID) -> bool:
    """Borra la empresa de prueba y todos sus datos si no pasó a piloto. Idempotente: una prueba
    ya convertida o ya vencida no se toca. Devuelve si borró algo."""
    from app.companies import delete as removal

    trial = get(conn, trial_id, lock=True)
    if trial is None or trial.status in ("converted", "expired"):
        return False
    if trial.tenant_id is not None:
        found = removal.company(conn, trial.tenant_id)
        if found is not None:
            removal.delete_company(
                conn, trial.tenant_id, operator="prueba-gratis", confirm_name=found[0]
            )
        _SANDBOXES.pop(trial.tenant_id, None)
    conn.execute("update trials set status = 'expired' where id = %s", (trial.id,))
    return True


def run_expire_job(conn: psycopg.Connection, job: Job) -> None:
    expire(conn, uuid.UUID(str(job.payload["trial_id"])))


def operator_rows(conn: psycopg.Connection) -> list[Trial]:
    rows = conn.execute(
        f"select {_COLUMNS} from trials order by created_at desc limit 200"
    ).fetchall()
    return [Trial(*row) for row in rows]
