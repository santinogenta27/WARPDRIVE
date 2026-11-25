"""Prueba gratis self-service con la tienda del visitante (decisión 075).

1. `start`: el visitante deja la URL de su tienda y su email; se le manda un código.
2. `confirm`: con el código correcto se crea la empresa de prueba (un tenant común, con su chat y
   "detrás de escena") y se encolan el armado y el borrado a los 7 días.
3. `run_build` (job `trial_build`): lee el sitio, indexa sus páginas, lee sus productos y
   configura el mismo agente que se vende contra la API simulada de Northwind, con sus productos
   y pedidos de ejemplo `DEMO-…`. En menos de un minuto y sin quedar trabado (decisión 099, en
   `build`). Idempotente: cada paso reemplaza lo suyo. `retry` lo vuelve a encolar.
4. `convert`: "Empezar mi piloto" convierte la empresa de prueba en una real, sin los pedidos de
   ejemplo. `run_expire_job` la borra a los 7 días si no se convirtió.
"""

import hashlib
import hmac
import os
import re
import secrets
import uuid
from collections.abc import Callable
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

JOB_KIND = "trial_build"
EXPIRE_JOB = "trial_expire"
MAX_MESSAGES = 30  # por prueba, sumando todas las sesiones del chat
TRIAL_DAYS = 7
CODE_TTL = timedelta(minutes=15)
MAX_CODE_ATTEMPTS = 5
MAX_PER_IP = 3  # pruebas por IP por día
MAX_PRODUCTS = 150  # decisión 101
SUMMARY_AFTER = 5  # mensajes: después, la página muestra el resumen de la prueba
WHATSAPP = "5491128492480"  # el equipo de ventas (una persona)
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
        "service_suggestions": [
            "¿Qué servicios ofrecen?",
            "¿Cuánto cuesta?",
            "¿Cómo los contacto?",
            "¿Cuál es el horario de atención?",
        ],
        "generic": ["Producto de ejemplo A", "Producto de ejemplo B", "Producto de ejemplo C"],
        "contact_subject": "Prueba gratis: quiere hablar · {domain}",
        "contact_body": "Sitio: {site}\nEmail: {email}\nEstado de la prueba: {status}\n",
        "whatsapp": (
            "¡Hola! Acabo de probar el agente de IA con {name} y quiero hablar de un piloto."
        ),
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
            "I'm looking for the {product}",
        ],
        "service_suggestions": [
            "What services do you offer?",
            "How much does it cost?",
            "How can I contact you?",
            "What are your opening hours?",
        ],
        "generic": ["Sample product A", "Sample product B", "Sample product C"],
        "contact_subject": "Free trial: wants to talk · {domain}",
        "contact_body": "Site: {site}\nEmail: {email}\nTrial status: {status}\n",
        "whatsapp": "Hi! I just tried the AI agent with {name} and want to talk about a pilot.",
    },
}


class TrialError(ValueError):
    """Dato inválido o límite alcanzado: el mensaje se le muestra al visitante."""


ERRORS = {
    "es": {
        "site": "La dirección de la tienda no es válida. Escribila como tutienda.com.",
        "not_found": (
            "No encontramos el sitio {domain}. Revisá que esté bien escrito, por ejemplo "
            "tutienda.com."
        ),
        "email": "El email no es válido.",
        "taken": "Ya hay una prueba con ese email o con esa tienda. Si querés seguir, escribinos.",
        "ip": "Ya se pidieron varias pruebas desde tu conexión hoy. Probá de nuevo mañana.",
        "code": "El código no es correcto.",
        "code_expired": "El código venció o se usó demasiadas veces. Pedí una prueba nueva.",
    },
    "en": {
        "site": "The store address isn't valid. Write it like yourstore.com.",
        "not_found": (
            "We couldn't find the site {domain}. Check that it's spelled right, like yourstore.com."
        ),
        "email": "The email isn't valid.",
        "taken": "There's already a trial with that email or store. Write to us to continue.",
        "ip": "Several trials were requested from your connection today. Try again tomorrow.",
        "code": "The code isn't right.",
        "code_expired": "The code expired or was tried too many times. Request a new trial.",
    },
}


def _error(language: str, key: str, **values: str) -> TrialError:
    return TrialError(ERRORS["en" if language == "en" else "es"][key].format(**values))


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
    """(dirección del sitio, dominio sin `www.`) de lo que escribió el visitante. Tolera lo que
    se escribe mal: sin `https://`, `https//`, `htps://`, una coma en lugar del punto, espacios,
    mayúsculas, parámetros de campaña y anclas (decisión 101)."""
    text = raw.strip()
    if len(text) > 300 or re.match(r"(?i)^(mailto|javascript|data|file|ftp):", text):
        raise _error(language, "site")
    text = re.sub(r"(?i)^h?t+p+s?:?/+", "", text)  # cualquier variante de http(s)://
    text = re.sub(r"\s+", "", text)
    head, slash, rest = text.partition("/")
    if "." not in head and head.count(",") == 1:
        head = head.replace(",", ".")
    parts = urlsplit(f"https://{head}{slash}{rest}")
    host = (parts.hostname or "").lower().rstrip(".")
    if not host or "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
        raise _error(language, "site")
    if host.startswith(".") or ".." in host or not re.search(r"\.[a-z]{2,}$", host):
        raise _error(language, "site")
    domain = host.removeprefix("www.")
    path = parts.path or "/"
    # Siempre HTTPS (decisión 076); el rastreo verifica además que la dirección sea pública.
    return f"https://{host}{path}", domain


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
    from app import net

    try:  # una dirección interna no llega ni a guardarse (decisión 076)
        net.check_url(url)
    except net.UnsafeURL as error:
        if "resolver" in str(error):  # el dominio no existe (o está mal escrito)
            raise _error(language, "not_found", domain=domain) from error
        raise _error(language, "site") from error
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
        " progress = jsonb_build_object('step', 'crawl', 'attempt', 1, 'started_at', now(),"
        " 'at', now()) where id = %s",
        (tenant_id, trial.id),
    )
    payload = {"trial_id": str(trial.id)}  # sin tenant_id: el borrado de la empresa no lo toca
    enqueue(
        conn,
        JOB_KIND,
        _build_payload(trial.id, tenant_id, token, 1),
        idempotency_key=f"trial-build-{trial.id}",
        max_attempts=3,
    )
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


def _build_payload(
    trial_id: uuid.UUID, tenant_id: uuid.UUID, token: str, attempt: int
) -> dict[str, Any]:
    """El job de armado lleva su intento y el enlace de la prueba, cifrado con la clave de la
    empresa, para el email de "lista" (en la base solo está el hash del enlace)."""
    from app.crypto import encrypt_for

    return {
        "trial_id": str(trial_id),
        "attempt": attempt,
        "link": encrypt_for(tenant_id, token),
    }


STALE_SECONDS = 120  # "building" por más de esto: la página ofrece reintentar
MAX_ATTEMPTS = 5


def elapsed(conn: psycopg.Connection, trial_id: uuid.UUID) -> float:
    """Segundos desde que empezó el armado en curso."""
    row = conn.execute(
        "select extract(epoch from clock_timestamp()"
        " - coalesce((progress->>'started_at')::timestamptz, created_at))"
        " from trials where id = %s",
        (trial_id,),
    ).fetchone()
    return max(float(row[0]), 0.0) if row and row[0] is not None else 0.0


def can_retry(conn: psycopg.Connection, trial: Trial) -> bool:
    if trial.tenant_id is None or int(trial.progress.get("attempt") or 1) >= MAX_ATTEMPTS:
        return False
    if trial.status == "failed":
        return True
    return trial.status == "building" and elapsed(conn, trial.id) > STALE_SECONDS


def retry(conn: psycopg.Connection, token: str, *, sample: bool = False) -> bool:
    """ "Reintentar": vuelve a encolar el armado si falló o lleva más de 2 minutos. Idempotente:
    un doble clic encola un solo intento nuevo, y el job del intento anterior ya no toca nada.
    `sample`: "Seguir con una prueba de ejemplo" (el sitio no se pudo leer, decisión 101)."""
    found = find(conn, token)
    if found is None:
        return False
    trial = get(conn, found.id, lock=True)
    if trial is None or not can_retry(conn, trial):
        return False
    assert trial.tenant_id is not None
    attempt = int(trial.progress.get("attempt") or 1) + 1
    conn.execute(
        "update trials set status = 'building', progress = jsonb_build_object('step', 'crawl',"
        " 'attempt', %s::int, 'started_at', clock_timestamp(), 'at', clock_timestamp(),"
        " 'sample', %s::boolean, 'operator_emailed', coalesce((progress->>'operator_emailed')"
        "::boolean, false)) where id = %s",
        (attempt, sample, trial.id),
    )
    enqueue(
        conn,
        JOB_KIND,
        _build_payload(trial.id, trial.tenant_id, token, attempt),
        idempotency_key=f"trial-build-{trial.id}-{attempt}",
        max_attempts=2,
    )
    return True


# -- armado -----------------------------------------------------------------------------------
NAME_MAX = 60  # el máximo del nombre del widget


def _store_name(declared: str, title: str, domain: str) -> str:
    """El nombre que declara el sitio (`og:site_name`); si no, la parte del título que se parece
    al dominio ("Ofertas – Tienda Sol" → "Tienda Sol"); si no, la primera. Hasta `NAME_MAX`."""
    return _fit(_name_from(declared, title, domain)) or domain[:NAME_MAX]


def _fit(name: str) -> str:
    return name if len(name) <= NAME_MAX else name[:NAME_MAX].rsplit(" ", 1)[0].strip()


def _name_from(declared: str, title: str, domain: str) -> str:
    separators = r"\s+[|–—\-·•»:]\s+"
    stem = re.sub(r"[^a-z0-9]", "", domain.split(".")[0].lower())

    def best(text: str) -> str:
        """La parte que se parece al dominio ("JOCO Cups | Glass Reusable Cups" → "JOCO
        Cups"); si ninguna, la primera."""
        parts = [p.strip() for p in re.split(separators, " ".join(text.split())) if p.strip()]
        for part in parts:
            if stem and stem in re.sub(r"[^a-z0-9]", "", part.lower()):
                return part[:80]
        return parts[0][:80] if parts else ""

    name = best(declared)
    if 2 <= len(name) <= 80:
        return name
    first = best(title)
    return first if 2 <= len(first) <= 80 else domain


# Qué leer primero de las 30 páginas: las políticas (suelen estar en el pie), después algunos
# productos (sus datos estructurados están en su página) y por último el resto.
_POLICY = re.compile(
    r"polic|envio|env%C3%ADo|shipping|deliver|entrega|devoluc|return|refund|reembols|cambio|"
    r"exchange|faq|pregunta|ayuda|help|termin|terms|garant|warrant|contact",
    re.IGNORECASE,
)
_PRODUCT = re.compile(r"/products?/|/productos?/|/p/|/item/", re.IGNORECASE)
_SKIP = re.compile(r"account|login|cuenta|cart|carrito|checkout|search|buscar|wishlist", re.I)
PRODUCT_PAGES = 8
_GIFT_CARD = re.compile(
    r"gift\s*card|gift\s*certificate|e-?gift|tarjeta\s+de\s+regalo|giftcard|voucher|vale\s+de\s+regalo",
    re.IGNORECASE,
)


def _priority() -> Callable[[str], int]:
    products = 0

    def rank(url: str) -> int:
        nonlocal products
        path = urlsplit(url).path
        if _SKIP.search(path):
            return 4
        if _POLICY.search(path):
            return 0
        if _PRODUCT.search(path):
            products += 1
            return 1 if products <= PRODUCT_PAGES else 3
        return 2

    return rank


def _money(value: Any, currency: str) -> str:
    return f"{currency} {float(value):,.2f}".replace(".00", "")


def product_line(p: dict[str, Any], language: str) -> str:
    """Una línea del catálogo: nombre, precio (rango y oferta), stock, variantes y link."""
    en = language == "en"
    price = _money(p["price"], p["currency"])
    if p.get("price_max"):
        price = f"{'from' if en else 'desde'} {price} {'to' if en else 'hasta'} " + _money(
            p["price_max"], p["currency"]
        )
    if p.get("compare_at"):
        before = _money(p["compare_at"], p["currency"])
        price += f" ({'on sale, was' if en else 'en oferta, antes'} {before})"
    parts = [f"- {p['name']}: {price}"]
    if p.get("category"):
        parts.append(f"{'category' if en else 'categoría'}: {p['category']}")
    if p.get("available") is False:
        parts.append("out of stock" if en else "sin stock")
    if p.get("variants"):
        parts.append(f"{'options' if en else 'opciones'}: {', '.join(p['variants'][:8])}")
    if p.get("description"):
        parts.append(str(p["description"])[:160])
    if p.get("url"):
        parts.append(str(p["url"]))
    return " · ".join(parts)


def _catalog_text(products: list[dict[str, Any]], language: str) -> str:
    """El catálogo que indexa el RAG. Aclara que es lo que leímos del sitio, no todo lo que vende
    la tienda: el agente nunca dice que vende "solo" esto (decisión 101)."""
    en = language == "en"
    title = "# Catalog" if en else "# Catálogo"
    note = (
        f"Products read from the store's public site ({len(products)}). The store may sell more."
        if en
        else f"Productos leídos del sitio público de la tienda ({len(products)}). Puede vender más."
    )
    return "\n".join([title, "", note, "", *(product_line(p, language) for p in products)])


def suggestions_for(
    language: str, products: list[dict[str, Any]], mode: str = "store"
) -> list[str]:
    """Las sugerencias del chat: con el nombre corto de un producto (decisión 101) o, en un
    sitio que no es tienda, sobre sus servicios."""
    texts = TEXTS["en" if language == "en" else "es"]
    if mode == "services" or not products:
        return list(texts["service_suggestions"])
    from app.trial import catalog

    first = products[0]
    short = first.get("short") or catalog.short_name(first["name"])
    return [s.format(product=short) for s in texts["suggestions"]]


def run_build(
    conn: psycopg.Connection,
    trial_id: uuid.UUID,
    *,
    embedder: Embedder,
    http: httpx.Client | None = None,
    checkpoint: Callable[[], None] | None = None,
) -> None:
    """Arma la prueba (ver `build`). Correrlo de nuevo reemplaza lo anterior sin duplicar nada.
    `checkpoint`: se llama en cada paso (el job confirma ahí, así la página ve el avance)."""
    from app.trial import build

    build.run_build(
        conn, trial_id, embedder=embedder, http=http, checkpoint=checkpoint or (lambda: None)
    )


def _configure(
    conn: psycopg.Connection,
    trial: Trial,
    name: str,
    products: list[dict[str, Any]],
    mode: str = "store",
) -> None:
    from app.channels.web import widget
    from app.demo import northwind

    assert trial.tenant_id is not None
    instructions = case_type_texts(trial.language)
    northwind.configure_tools(
        conn,
        trial.tenant_id,
        base_url=base_url(trial.tenant_id),
        instructions=instructions,
        limits=TOOL_LIMITS,
        # Un sitio que no es tienda no tiene pedidos ni productos: sin esos tipos de caso, el
        # agente atiende con las páginas del sitio (decisión 101).
        active=dict.fromkeys(CASE_TYPES, mode != "services"),
    )
    suggestions = suggestions_for(trial.language, products, mode)
    widget.save(
        conn,
        trial.tenant_id,
        name=name,
        accent=widget.DEFAULT_ACCENT,
        domains="",
        suggestions="\n".join(suggestions),
    )


def case_type_texts(language: str) -> dict[str, str]:
    """Las instrucciones de los tipos de caso de la prueba, en el idioma de la empresa."""
    folder = HERE / "case_types" / ("en" if language == "en" else "es")
    return {kind: (folder / f"{kind}.yaml").read_text("utf-8") for kind in CASE_TYPES}


def run_build_job(conn: psycopg.Connection, job: Job) -> None:
    """El job de la cola: confirma en cada paso y avisa por email cuando está lista."""
    from app.mailer import mailer_from_env
    from app.trial import build

    build.run_job(conn, job, mailer=mailer_from_env(), checkpoint=conn.commit)


# -- la tienda de ejemplo ---------------------------------------------------------------------
_SANDBOXES: dict[uuid.UUID, TestClient] = {}


def base_url(tenant_id: uuid.UUID) -> str:
    return f"{BASE_PREFIX}{tenant_id}"


def reset_sandboxes() -> None:
    _SANDBOXES.clear()


def _short_details(p: dict[str, Any], language: str) -> str:
    """La descripción para la API simulada: corta (la búsqueda devuelve varios productos y todo
    llega al agente), con el rango de precios y la oferta, que la API no tiene en otros campos."""
    en = language == "en"
    parts = []
    if p.get("price_max"):
        parts.append(
            f"{'Price from' if en else 'Precio desde'} {_money(p['price'], p['currency'])} "
            f"{'to' if en else 'hasta'} {_money(p['price_max'], p['currency'])}"
        )
    if p.get("compare_at"):
        parts.append(
            f"{'On sale, was' if en else 'En oferta, antes'} "
            + _money(p["compare_at"], p["currency"])
        )
    if p.get("description"):
        parts.append(str(p["description"])[:120])
    return ". ".join(parts) or str(p["name"])


def mock_products(products: list[dict[str, Any]], language: str = "en") -> list[dict[str, Any]]:
    """Los productos de la tienda en el formato de la API simulada. Las opciones (talles o
    colores) van como talles, con stock si la fuente dice que hay; la descripción lleva la
    categoría, el rango de precios y la oferta, para que la búsqueda y el agente los vean."""
    items = []
    for n, p in enumerate(products, start=1):
        options = [str(v)[:24] for v in (p.get("variants") or [])][:8] or ["One size"]
        units = 0 if p.get("available") is False else 10
        details = _short_details(p, language)
        items.append(
            {
                "code": f"P{n:03d}",
                "sku_prefix": "DEMO",
                "name": p["name"],
                "price": p["price"],
                "currency": p["currency"],
                "image": p.get("image") or "",
                "url": p.get("url") or "",
                "category": p.get("category") or "",
                "description": details,
                "sizes": options,
                "colors": ["Standard"],
                "stock": {"Standard": dict.fromkeys(options, units)},
            }
        )
    return items


def sample_orders(
    products: list[dict[str, Any]], language: str, today: date
) -> list[dict[str, Any]]:
    """DEMO-1001 en camino con demora, DEMO-1002 entregado y DEMO-1003 sin despachar."""
    texts = TEXTS["en" if language == "en" else "es"]
    items = mock_products(products, language)
    # Productos distintos entre sí, primero los que tienen stock (decisión 101).
    stocked = [i for i, p in enumerate(products) if p.get("available") is not False]
    picks = (stocked + [i for i in range(len(items)) if i not in stocked])[:3] or [0]

    def line(n: int) -> dict[str, Any]:
        product = items[picks[n % len(picks)]]
        size = product["sizes"][0]
        code = "OS" if size.lower() == "one size" else size.upper()
        return {
            "sku": f"DEMO-{product['code']}-STA-{code}",
            "name": product["name"],
            "size": size,
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
            "currency": items[picks[n % len(picks)]]["currency"],
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


SEARCH_RESULTS = 8  # productos por búsqueda en la tienda de ejemplo
_SEARCH_WORD = re.compile(r"[a-z0-9]+")
_SEARCH_STOP = {"a", "an", "the", "do", "you", "have", "any", "in", "for", "of", "and", "or", "s"}


def _ranked(products: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    """Los productos que mejor coinciden primero: las palabras de la búsqueda en el nombre pesan
    más que en la categoría o la descripción; a igual puntaje, los que tienen stock."""
    words = {w.rstrip("s") for w in _SEARCH_WORD.findall(query.lower())} - _SEARCH_STOP

    def score(product: dict[str, Any]) -> tuple[float, int]:
        def found(text: Any) -> int:
            own = {w.rstrip("s") for w in _SEARCH_WORD.findall(str(text or "").lower())}
            return len(words & own)

        points = 3.0 * found(product.get("name")) + found(product.get("category"))
        points += 0.5 * found(product.get("description"))
        return points, 1 if product.get("in_stock") else 0

    return sorted(products, key=score, reverse=True)


class _Sandbox(TestClient):
    """La API simulada de la prueba con la búsqueda acotada (decisión 101). Con un catálogo real
    ("men" y "wool" están en casi todos los productos de Allbirds) la API devolvía decenas de
    productos y el resultado le llegaba cortado al agente. Acá devuelve los que mejor coinciden y
    cuántos más hay, para que el agente sepa que el catálogo tiene más."""

    trial_products = 0

    # El cliente de Starlette puede venir con su propio httpx (httpx2): tipos genéricos.
    def send(self, request: Any, **kwargs: Any) -> Any:
        response = super().send(request, **kwargs)
        if request.method != "GET" or request.url.path != "/products":
            return response
        if response.status_code != 200:
            return response
        data = response.json()
        found = data.get("products") or []
        if len(found) <= SEARCH_RESULTS:
            return response
        query = request.url.params.get("query", "")
        best = _ranked(found, query)[:SEARCH_RESULTS]
        data = {
            "products": best,
            "total_matches": len(found),
            "note": (
                f"Showing the {SEARCH_RESULTS} best matches of {len(found)}. The store has more "
                "products: search with more specific words to see others."
            ),
        }
        return type(response)(200, json=data, request=request)


def sandbox(conn: psycopg.Connection, tenant_id: uuid.UUID) -> TestClient:
    """La API simulada de la prueba, en memoria y por proceso. Se arma desde la fila de la prueba:
    un reinicio la vuelve a su estado inicial, y también cuando el segundo plano suma productos
    (decisión 101). Un sitio que no es tienda no tiene pedidos de ejemplo."""
    from app.companies.create import chat_slug
    from app.demo import northwind

    trial = for_tenant(conn, tenant_id)
    if trial is None:
        raise RuntimeError("La empresa no tiene una prueba en curso")
    products = trial.products or []
    cached = _SANDBOXES.get(tenant_id)
    if isinstance(cached, _Sandbox) and cached.trial_products == len(products):
        return cached
    api = northwind.api_module()
    public = (os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")
    orders = sample_orders(products, trial.language, api.store_today()) if products else []
    client = _Sandbox(
        api.create_app(
            orders,
            catalog=mock_products(products, trial.language),
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
    client.trial_products = len(products)
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


def whatsapp_url(trial: Trial) -> str:
    """El chat de WhatsApp con el equipo, con un mensaje en el idioma de la prueba que nombra la
    tienda (decisión 101)."""
    from urllib.parse import quote

    text = TEXTS[trial.language]["whatsapp"].format(name=trial.name)
    return f"https://wa.me/{WHATSAPP}?text={quote(text)}"


def summary(conn: psycopg.Connection, trial: Trial) -> dict[str, Any]:
    """El resumen de la prueba, solo con datos reales: las consultas que resolvió el agente (el
    primer mensaje de cada caso no derivado), cuántas derivó y qué acciones hizo el guardián."""
    left = max(MAX_MESSAGES - trial.messages, 0)
    data: dict[str, Any] = {
        "show": trial.messages >= SUMMARY_AFTER or left == 0,
        "messages": trial.messages,
        "left": left,
        "resolved": [],
        "escalated": 0,
        "actions": {"verified": 0, "offer_denied": 0, "refund_pending": 0},
    }
    if trial.tenant_id is None:
        return data
    with tenant_scope(conn, trial.tenant_id):
        cases = conn.execute(
            "select c.id, c.status = 'escalated' or exists (select 1 from audit_events e"
            " where e.case_id = c.id and e.kind = 'escalated'),"
            " (select m.text from case_messages m where m.case_id = c.id and m.role = 'customer'"
            "  order by m.created_at limit 1),"
            " exists (select 1 from case_messages m where m.case_id = c.id and m.role = 'agent')"
            " from cases c order by c.created_at"
        ).fetchall()
        events = conn.execute(
            "select kind, payload from audit_events where kind in ('verification_checked',"
            " 'tool_call')"
        ).fetchall()
    for _, escalated, first, answered in cases:
        if escalated:
            data["escalated"] += 1
        elif answered and first:
            data["resolved"].append(" ".join(str(first).split())[:140])
    actions = data["actions"]
    for kind, payload in events:
        payload = payload or {}
        if kind == "verification_checked" and payload.get("result") == "verified":
            actions["verified"] += 1
        elif kind == "tool_call" and payload.get("tool") == "ofrecer_cupon":
            actions["offer_denied"] += payload.get("status") == "denied"
        elif kind == "tool_call" and payload.get("status") == "approval_required":
            actions["refund_pending"] += payload.get("tool") == "iniciar_reembolso"
    data["resolved"] = data["resolved"][:8]
    return data


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
