"""Armado rápido de la prueba gratis (decisiones 099 y 101).

Desde los ojos del visitante, el agente tiene que estar listo en menos de un minuto, siempre:

1. Abre el sitio (`sources.open_site`), siguiendo redirecciones. Si no existe, está caído, es lento,
   tiene contraseña o bloquea bots, la prueba pasa a "failed" con un mensaje claro.
2. En paralelo: el catálogo por la primera fuente pública que dé productos (`sources`, sin base de
   datos) y las páginas prioritarias del sitio (portada, políticas, algunos productos; hasta
   `FIRST_PAGES`), con timeout por página y tiempo máximo.
3. Si no hubo datos estructurados, el último recurso: el modelo de la prueba lee hasta 20 páginas
   de producto y solo se acepta lo que aparece literalmente en ellas.
4. Indexa páginas y catálogo con una sola llamada de embeddings (sin embeddings, por texto),
   configura el agente y la prueba queda "ready": como tienda, como sitio de servicios (sin
   pedidos de ejemplo) o, si el visitante lo eligió, como prueba de ejemplo.
5. El resto (páginas y productos del sitemap) se lee en segundo plano (`phase: rest`).

El avance se guarda en `trials.progress` y se confirma en cada paso (`checkpoint`). Cada job lleva
su intento: el de un intento viejo no toca nada. Todo es idempotente: cada paso reemplaza lo suyo.
El contenido de las páginas es dato: solo se guarda su texto (sin tarjetas ni DNI) y los campos de
producto.
"""

import html
import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

import httpx
import psycopg
from psycopg.types.json import Jsonb

from app import net
from app.db.connection import tenant_scope
from app.jobs.queue import Job, enqueue
from app.llm.embedder import Embedder
from app.mailer import Email, Mailer
from app.onboarding import extract, sensitive
from app.onboarding.ingest import SITE_MAX_BYTES, SKIP_EXTENSIONS, remove_source, source_key
from app.rag.indexer import chunk_text, vector_literal
from app.trial import catalog, service, sources
from app.trial.sources import SiteError

logger = logging.getLogger(__name__)

# Un User-Agent honesto (decisión 101): quiénes somos y dónde leer más.
USER_AGENT = "Mozilla/5.0 (compatible; EnterpriseDataAIBot/1.0; +https://enterprisedataai.com)"
FIRST_PAGES = 8  # con estas el agente ya queda listo
MAX_PAGES = 30  # con las de segundo plano
POLICY_FIRST = 4  # de las primeras: políticas…
PRODUCT_FIRST = 3  # …y productos (el resto, la portada y otras)
EXTRA_POLICIES = 4  # las que publica el sitemap o Shopify, si no aparecieron en el rastreo
WORKERS = 4
PAGE_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
CATALOG_MAX_BYTES = 8 * 1024 * 1024  # /products.json de 250 productos pesa 1-2 MB
CRAWL_SECONDS = 20.0  # para leer las primeras páginas
CATALOG_SECONDS = 25.0  # para leer el catálogo (en paralelo con el rastreo)
LLM_SECONDS = 40.0  # el último recurso no empieza después de esto
BUILD_SECONDS = 45.0  # todo el armado; los embeddings se saltean si no queda tiempo
EMBED_MIN_SECONDS = 8.0
REST_SLICE_SECONDS = 10.0  # cada parte del segundo plano (la cola tiene un solo hilo)
REST_CONTINUE_WAIT = timedelta(seconds=3)  # antes, lo que esté en cola (otro armado)
REST_RETRY = timedelta(minutes=30)  # si siguen sin haber embeddings
REST_FIRST_WAIT = timedelta(seconds=75)
REST_ROUNDS = 8
EMBED_BATCH = 100
MAX_LIVE_PRODUCTS = 12
LLM_MODEL = "claude-haiku-5-5"  # por la clave de la prueba se mapea a un Flash de Gemini
# Qué política es cada página, por su dirección o su título (lo que muestra la pantalla).
POLICY_KINDS = (
    ("shipping", re.compile(r"env[ií]o|shipping|deliver|entrega|despacho", re.I)),
    ("returns", re.compile(r"devoluc|return|refund|reembols|cambios?\b|exchange", re.I)),
    ("warranty", re.compile(r"garant|warrant", re.I)),
    ("payments", re.compile(r"pagos?\b|payment|cuotas|financ", re.I)),
    ("faq", re.compile(r"faq|preguntas|ayuda|help", re.I)),
    ("privacy", re.compile(r"privac", re.I)),
    ("terms", re.compile(r"t[eé]rminos|terms|condiciones", re.I)),
    ("contact", re.compile(r"contact", re.I)),
)
# La prueba de ejemplo (el sitio no se pudo leer): políticas de ejemplo con el nombre de la tienda.
SAMPLE_POLICIES = {
    "en": (
        "# Store policies (sample)\n\nShipping: orders ship within 2 business days and arrive in "
        "3 to 5 business days. Returns: you have 30 days from delivery to return an item in its "
        "original condition; the refund goes to the original payment method. Exchanges for "
        "another size or color are free."
    ),
    "es": (
        "# Políticas de la tienda (ejemplo)\n\nEnvíos: despachamos en 2 días hábiles y llegan en "
        "3 a 5 días hábiles. Devoluciones: tenés 30 días desde la entrega para devolver un "
        "producto sin uso; el reembolso se hace al mismo medio de pago. Los cambios de talle o "
        "color no tienen costo."
    ),
}


class Superseded(Exception):  # noqa: N818 — no es un error: hay un intento más nuevo
    """Otro intento reemplazó a este: no se toca nada más."""


@dataclass(frozen=True)
class Page:
    url: str
    title: str
    text: str
    links: tuple[str, ...]
    products: tuple[catalog.Product, ...]
    declared: str  # el nombre de la tienda que declara la página
    markup: str = field(default="", repr=False, compare=False)


@dataclass(frozen=True)
class Doc:
    title: str
    source: str
    text: str


def _noop() -> None:
    return None


# -- rastreo -----------------------------------------------------------------------------------
def page_url(url: str) -> str:
    """Sin fragmento ni parámetros: la misma página con otro ancla no se vuelve a leer."""
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", "", ""))


def kind(url: str) -> str:
    path = urlsplit(url).path
    if path.lower().endswith(SKIP_EXTENSIONS) or service._SKIP.search(path):
        return "skip"
    if service._POLICY.search(path):
        return "policy"
    if service._PRODUCT.search(path):
        return "product"
    return "other"


def policy_kind(url: str, title: str = "") -> str | None:
    text = f"{unquote(urlsplit(url).path)} {title}"
    return next((name for name, pattern in POLICY_KINDS if pattern.search(text)), None)


def _hosts(host: str) -> set[str]:
    return sources.hosts_of(host)


def page_of(url: str, markup: str) -> Page:
    parser = extract.HtmlText()
    try:
        parser.feed(markup)
    except Exception:  # noqa: BLE001 — HTML roto: se lee lo que se pudo
        pass
    title = " ".join(html.unescape(parser.title).split()) or url
    return Page(
        url=url,
        title=title,
        text=parser.text(),
        links=tuple(parser.links),
        products=tuple(catalog.extract(markup, url)),
        declared=catalog.site_name(markup),
        markup=markup,
    )


def fetch(
    client: httpx.Client,
    url: str,
    hosts: set[str],
    pace: Callable[[], None] = _noop,
) -> tuple[Page | None, int]:
    """Una página y su estado HTTP. Sigue redirecciones solo dentro del sitio."""
    current = url
    for _ in range(4):
        pace()
        response = client.get(current)
        if response.is_redirect:
            target = page_url(urljoin(current, response.headers.get("location", "")))
            if (urlsplit(target).hostname or "").lower() not in hosts:
                return None, response.status_code
            current = target
            continue
        content_type = response.headers.get("content-type", "text/html")
        if response.status_code != 200 or "html" not in content_type:
            return None, response.status_code
        return page_of(current, response.text), 200
    return None, 0


def safe_client() -> httpx.Client:
    return net.safe_client(
        max_bytes=max(SITE_MAX_BYTES, CATALOG_MAX_BYTES),
        timeout=PAGE_TIMEOUT,
        headers={"User-Agent": USER_AGENT},
    )


class Crawler:
    """Lee páginas del sitio en paralelo, sin salir de su dominio, con tiempo máximo, respetando
    `robots.txt` y el ritmo de pedidos de la prueba."""

    def __init__(
        self,
        start: str,
        http: httpx.Client | None = None,
        *,
        robots: sources.Robots | None = None,
        pace: Callable[[], None] = _noop,
    ) -> None:
        parts = urlsplit(start.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise SiteError("unsafe", "la dirección no es válida")
        self.start = page_url(start.strip())
        self.hosts = _hosts(parts.hostname.lower())
        self.client = http or safe_client()
        self.robots = robots or sources.Robots()
        self.pace = pace
        self.seen: set[str] = {self.start}
        self.queue: list[str] = []  # en el orden en que aparecen
        self.taken = {"policy": 0, "product": 0, "other": 0}
        self.covered: set[str] = set()  # tipos de política ya elegidos

    def add(self, url: str, *, first: bool = False) -> None:
        found = page_url(url)
        parts = urlsplit(found)
        if (
            parts.scheme in ("http", "https")
            and (parts.hostname or "").lower() in self.hosts
            and found not in self.seen
            and kind(found) != "skip"
            and self.robots.allowed(found)
        ):
            self.seen.add(found)
            if first:
                self.queue.insert(0, found)
            else:
                self.queue.append(found)

    def add_links(self, page: Page) -> None:
        for link in page.links:
            self.add(urljoin(page.url, link))

    def take_home(self, page: Page) -> None:
        self.seen.add(page.url)
        self.add_links(page)

    def pick(self, count: int) -> list[str]:
        """Las próximas páginas: políticas de tipos distintos, algunos productos y el resto."""
        chosen: list[str] = []

        def take(accept: Callable[[str], bool]) -> None:
            for url in list(self.queue):
                if len(chosen) >= count:
                    return
                if accept(url):
                    chosen.append(url)
                    self.queue.remove(url)
                    self.taken[kind(url)] += 1
                    if kind(url) == "policy":
                        self.covered.add(policy_kind(url) or url)

        take(
            lambda u: (
                kind(u) == "policy"
                and self.taken["policy"] < POLICY_FIRST
                and (policy_kind(u) or u) not in self.covered
            )
        )
        take(lambda u: kind(u) == "policy" and self.taken["policy"] < POLICY_FIRST)
        take(lambda u: kind(u) == "product" and self.taken["product"] < PRODUCT_FIRST)
        take(lambda u: kind(u) == "other")
        take(lambda u: True)
        return chosen

    def read(
        self,
        limit: int,
        deadline: float,
        on_page: Callable[[Page], None],
        on_start: Callable[[int], None] = lambda total: None,
        batch: list[str] | None = None,
    ) -> list[Page]:
        """Hasta `limit` páginas de la cola (o de `batch`), en tandas paralelas, hasta `deadline`.
        Una página que falla se saltea; las que no llegan a tiempo se abandonan."""
        pages: list[Page] = []
        pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="trial-crawl")
        try:
            while len(pages) < limit and time.monotonic() < deadline:
                chosen = batch[:limit] if batch is not None else self.pick(limit - len(pages))
                batch = [] if batch is not None else None
                if not chosen:
                    break
                on_start(len(pages) + len(chosen) + min(len(self.queue), limit))
                futures: dict[Future[tuple[Page | None, int]], str] = {
                    pool.submit(fetch, self.client, url, self.hosts, self.pace): url
                    for url in chosen
                }
                pending = set(futures)
                while pending:
                    left = deadline - time.monotonic()
                    if left <= 0:
                        break
                    done, pending = wait(pending, timeout=left, return_when=FIRST_COMPLETED)
                    for future in done:
                        try:
                            page, _ = future.result()
                        except Exception:  # noqa: BLE001 — una página rota no corta el armado
                            page = None
                        if page is None or page.url in {p.url for p in pages}:
                            continue
                        pages.append(page)
                        self.seen.add(page.url)
                        self.add_links(page)
                        on_page(page)
                if pending:  # se acabó el tiempo: esas páginas quedan para el segundo plano
                    self.queue[:0] = [futures[f] for f in pending]
                    break
        finally:
            pool.shutdown(wait=False, cancel_futures=True)
        return pages


# -- indexado ------------------------------------------------------------------------------------
def doc_of(page: Page) -> Doc:
    text, _ = sensitive.redact(page.text)
    title, _ = sensitive.redact(page.title)
    return Doc(title=title, source=source_key(page.url), text=text)


def index(
    conn: psycopg.Connection,
    tenant_id: uuid.UUID,
    embedder: Embedder,
    docs: list[Doc],
    *,
    embed: bool = True,
) -> bool:
    """Indexa los documentos con UNA llamada de embeddings (en lotes de `EMBED_BATCH`). Sin
    embeddings, los fragmentos quedan solo con tsvector. Reemplaza los de igual `source`.
    Devuelve si quedaron con vectores."""
    items = [(doc, [f"{doc.title}\n\n{c}" for c in chunk_text(doc.text)]) for doc in docs]
    items = [(doc, contents) for doc, contents in items if contents]
    flat = [content for _, contents in items for content in contents]
    vectors = _embed(conn, tenant_id, embedder, flat) if embed and flat else None
    position = 0
    with tenant_scope(conn, tenant_id):
        for doc, contents in items:
            remove_source(conn, tenant_id, doc.source)
            row = conn.execute(
                "insert into documents (tenant_id, title, source, text) values (%s, %s, %s, %s)"
                " returning id",
                (tenant_id, doc.title, doc.source, doc.text),
            ).fetchone()
            assert row is not None
            rows = [
                (
                    tenant_id,
                    row[0],
                    content,
                    vector_literal(vectors[position + n]) if vectors else None,
                    doc.source,
                )
                for n, content in enumerate(contents)
            ]
            with conn.cursor() as cursor:
                cursor.executemany(
                    "insert into chunks (tenant_id, document_id, text, embedding, source)"
                    " values (%s, %s, %s, %s::vector, %s)",
                    rows,
                )
            position += len(contents)
    return vectors is not None or not flat


def _embed(
    conn: psycopg.Connection, tenant_id: uuid.UUID, embedder: Embedder, texts: list[str]
) -> list[list[float]] | None:
    """Los vectores, o None si el embedder no está disponible (cuota, caída, timeout)."""
    vectors: list[list[float]] = []
    try:
        for start in range(0, len(texts), EMBED_BATCH):
            batch = texts[start : start + EMBED_BATCH]
            with conn.transaction():  # el registro de uso, si falla, no arrastra al resto
                found = embedder.embed(batch, tenant_id=tenant_id, input_type="document")
            if len(found) != len(batch):
                return None
            vectors.extend(found)
    except Exception as error:  # noqa: BLE001 — sin embeddings la prueba sigue por texto
        logger.warning("Prueba gratis sin embeddings: %s", str(error)[:200])
        return None
    return vectors


def backfill(conn: psycopg.Connection, tenant_id: uuid.UUID, embedder: Embedder) -> bool:
    """Completa los vectores que faltan. Devuelve si quedaron todos."""
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select id, text from chunks where embedding is null order by id"
        ).fetchall()
    for start in range(0, len(rows), EMBED_BATCH):
        batch = rows[start : start + EMBED_BATCH]
        vectors = _embed(conn, tenant_id, embedder, [text for _, text in batch])
        if vectors is None:
            return False
        with tenant_scope(conn, tenant_id), conn.cursor() as cursor:
            cursor.executemany(
                "update chunks set embedding = %s::vector where id = %s and embedding is null",
                [
                    (vector_literal(v), chunk_id)
                    for (chunk_id, _), v in zip(batch, vectors, strict=True)
                ],
            )
    return True


def page_count(conn: psycopg.Connection, tenant_id: uuid.UUID) -> int:
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select count(*) from documents where source is distinct from %s",
            (service.CATALOG_SOURCE,),
        ).fetchone()
    return int(row[0]) if row else 0


def catalog_doc(products: list[dict[str, Any]], language: str) -> Doc:
    return Doc(
        title="Catalog" if language == "en" else "Catálogo",
        source=service.CATALOG_SOURCE,
        text=service._catalog_text(products, language),
    )


# -- avance --------------------------------------------------------------------------------------
class Progress:
    """Escribe el avance de un intento. Si otro intento lo reemplazó, `Superseded`."""

    def __init__(
        self,
        conn: psycopg.Connection,
        trial_id: uuid.UUID,
        attempt: int | None,
        checkpoint: Callable[[], None],
    ) -> None:
        self.conn = conn
        self.trial_id = trial_id
        self.attempt = attempt
        self.checkpoint = checkpoint

    def update(self, status: str | None = None, **values: Any) -> None:
        sql = "update trials set progress = progress || %s || jsonb_build_object('at', now())"
        params: list[Any] = [Jsonb(values)]
        if status is not None:
            sql += ", status = %s"
            params.append(status)
        sql += " where id = %s"
        params.append(self.trial_id)
        if self.attempt is not None:
            sql += " and coalesce((progress->>'attempt')::int, 1) = %s"
            params.append(self.attempt)
        if self.conn.execute(sql + " returning 1", params).fetchone() is None:
            raise Superseded
        self.checkpoint()


def _live(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {"name": p["name"], "price": p["price"], "currency": p["currency"], "image": p["image"]}
        for p in products[:MAX_LIVE_PRODUCTS]
    ]


def _dicts(products: list[catalog.Product]) -> list[dict[str, Any]]:
    return [p.as_dict() for p in products]


# -- el modelo como último recurso ---------------------------------------------------------------
def _model_asker(
    conn: psycopg.Connection, tenant_id: uuid.UUID
) -> Callable[[str], tuple[str, int]]:
    """Una llamada al modelo de la prueba (su propia clave), por `LLMClient` y con el uso en
    `usage`. Devuelve (respuesta, tokens)."""
    from app.llm.client import LLMClient, PostgresUsageRecorder
    from app.trial import keys

    llm = LLMClient(provider=keys.TrialProvider(), recorder=PostgresUsageRecorder(conn))

    def ask(prompt: str) -> tuple[str, int]:
        with conn.transaction():  # el registro de uso, si falla, no arrastra al resto
            response = llm.complete(
                tenant_id=tenant_id,
                model=LLM_MODEL,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                role="catalogo_prueba",
            )
        usage = response.usage
        return response.text, int(usage.input_tokens or 0) + int(usage.output_tokens or 0)

    return ask


# -- el armado -----------------------------------------------------------------------------------
def run_build(
    conn: psycopg.Connection,
    trial_id: uuid.UUID,
    *,
    embedder: Embedder,
    http: httpx.Client | None = None,
    checkpoint: Callable[[], None] = _noop,
    mailer: Mailer | None = None,
    link: str | None = None,
    attempt: int | None = None,
) -> None:
    """Arma la prueba en menos de un minuto. Nunca lanza por el sitio ni por los embeddings: la
    prueba termina "ready" o "failed". `attempt`: el intento del job (None: el actual)."""
    trial = service.get(conn, trial_id)
    if trial is None or trial.status not in service.ACTIVE or trial.tenant_id is None:
        return
    current = int(trial.progress.get("attempt") or 1)
    if attempt is not None and attempt != current:
        return  # un job de un intento anterior
    progress = Progress(conn, trial_id, attempt, checkpoint)
    try:
        _build(conn, trial, progress, embedder, http, current)
    except Superseded:
        return
    except Exception as error:  # noqa: BLE001 — se informa en la página y se puede reintentar
        code = error.code if isinstance(error, SiteError) else "internal"
        if code == "internal":
            logger.exception("El armado de la prueba %s falló", trial_id)
        if conn.info.transaction_status == psycopg.pq.TransactionStatus.INERROR:
            conn.rollback()
        try:
            progress.update("failed", error=str(error)[:300], error_code=code)
        except Superseded:
            return
        if mailer is not None:
            _notify_operator(conn, trial_id, mailer, link)
            checkpoint()
        return
    if mailer is not None:
        if link:
            _notify_ready(conn, trial_id, mailer, link)
        _notify_operator(conn, trial_id, mailer, link)
        checkpoint()


@dataclass
class _State:
    read: int = 0
    total: int = FIRST_PAGES
    policies: list[str] = field(default_factory=list)
    found: list[dict[str, Any]] = field(default_factory=list)
    from_pages: list[catalog.Product] = field(default_factory=list)


def _build(
    conn: psycopg.Connection,
    trial: service.Trial,
    progress: Progress,
    embedder: Embedder,
    http: httpx.Client | None,
    attempt: int,
) -> None:
    assert trial.tenant_id is not None
    started = time.monotonic()
    times: dict[str, float] = {}
    state = _State()
    progress.update(
        step="crawl", pages_read=0, pages_total=FIRST_PAGES, current=trial.site_url,
        found=[], policies=[], times={}, error=None, error_code=None,
    )  # fmt: skip
    if trial.progress.get("sample"):
        _build_sample(conn, trial, progress, embedder, started)
        return

    def seen(page: Page) -> None:
        state.read += 1
        detected = policy_kind(page.url, page.title)
        if detected and detected not in state.policies:
            state.policies.append(detected)
        if page.products:  # mientras llega el catálogo, los de las páginas
            state.from_pages.extend(page.products)
            state.found = _dicts(catalog.select(state.from_pages, MAX_LIVE_PRODUCTS))
        progress.update(
            step="crawl",
            pages_read=state.read,
            pages_total=max(state.total, state.read),
            current=page.url,
            found=_live(state.found),
            policies=state.policies,
        )

    def planned(total: int) -> None:
        state.total = min(FIRST_PAGES, max(total, state.read))

    client = http or safe_client()
    pace = sources.Pace()
    # 1. El sitio. Una portada que bloquea bots no corta todavía: el catálogo puede estar abierto.
    site: sources.Site | None = None
    blocked: SiteError | None = None
    try:
        site = sources.open_site(client, trial.site_url)
    except SiteError as error:
        if error.code != "blocked":
            raise
        blocked = error
    origin = site.origin if site else sources.origin_of(trial.site_url)
    robots = sources.robots_for(client, origin)

    # 2. El catálogo, en otro hilo (sin base de datos), mientras se leen las páginas.
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="trial-catalog")
    future = pool.submit(
        sources.read_catalog,
        client,
        trial.site_url,
        site=site,
        deadline=started + CATALOG_SECONDS,
        robots=robots,
        pace=pace,
    )
    pages: list[Page] = []
    crawler: Crawler | None = None
    try:
        if site is not None:
            crawler = Crawler(site.url, client, robots=robots, pace=pace)
            home = page_of(page_url(site.url), site.markup)
            if site.platform == "shopify":
                for name in reversed(sources.SHOPIFY_POLICIES):
                    crawler.add(f"{origin}/policies/{name}", first=True)
            crawler.take_home(home)
            planned(1 + len(crawler.queue))
            seen(home)
            pages = [home]
            pages += crawler.read(
                FIRST_PAGES - 1, started + CRAWL_SECONDS, seen, on_start=lambda n: planned(1 + n)
            )
        try:
            found = future.result(
                timeout=max(1.0, started + CATALOG_SECONDS + 5 - time.monotonic())
            )
        except FutureTimeout:
            found = sources.Catalog(platform=site.platform if site else "other")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    # Las políticas que publica el sitemap (o Shopify) y no aparecieron en el rastreo.
    if crawler is not None:
        extra = [page_url(u) for u in found.policy_urls if page_url(u) not in crawler.seen]
        extra = [u for u in extra if policy_kind(u) not in state.policies][:EXTRA_POLICIES]
        for url in extra:
            crawler.seen.add(url)
        if extra and time.monotonic() < started + CRAWL_SECONDS + 6:
            pages += crawler.read(len(extra), started + CRAWL_SECONDS + 6, seen, batch=extra)

    # 3. Los productos: del catálogo; si no, de las páginas leídas; si no, el modelo validado.
    discarded = dict(found.discarded)
    products = list(found.products)
    source = found.source
    if not products:
        from_pages = [p for page in pages for p in page.products]
        products = catalog.select(from_pages, service.MAX_PRODUCTS, discarded)
        source = "structured_data" if products else "none"
    llm = sources.LlmResult()
    if not products and time.monotonic() < started + LLM_SECONDS:
        candidates = list(found.unstructured)
        known = {u for u, _ in candidates}
        candidates += [
            (p.url, p.markup)
            for p in pages
            if kind(p.url) == "product" and not p.products and p.url not in known
        ]
        if candidates:
            llm = sources.llm_products(candidates, _model_asker(conn, trial.tenant_id))
            for reason, count in llm.discarded.items():
                discarded[reason] = discarded.get(reason, 0) + count
            products = catalog.select(llm.products, service.MAX_PRODUCTS, discarded)
            source = "llm_validado" if products else source

    readable = [p for p in pages if p.text.strip()]
    if not products and not readable:
        if blocked is not None:
            raise blocked
        raise SiteError("js_only", "sin texto ni productos para leer")
    mode = "store" if products else "services"
    items = _dicts(products)
    state.found = items
    times["crawl"] = round(time.monotonic() - started, 1)
    progress.update(
        step="index", times=times, current="", pages_total=state.read, found=_live(items)
    )

    home_page = pages[0] if pages else None
    name = found.store_name or service._store_name(
        home_page.declared if home_page else "",
        home_page.title if home_page else "",
        trial.domain,
    )
    name = service._fit(name) or trial.domain[: service.NAME_MAX]
    step = time.monotonic()
    docs = [doc_of(p) for p in readable]
    if found.llms_text:
        text, _ = sensitive.redact(found.llms_text)
        docs.append(Doc(title="llms.txt", source=source_key(f"{origin}/llms.txt"), text=text))
    if items:
        docs.append(catalog_doc(items, trial.language))
    embed = time.monotonic() < started + BUILD_SECONDS - EMBED_MIN_SECONDS
    with conn.transaction():
        conn.execute("update trials set products = %s where id = %s", (Jsonb(items), trial.id))
        _site_source(conn, trial.tenant_id, trial.site_url)
        with tenant_scope(conn, trial.tenant_id):  # sin el catálogo de un armado anterior
            if not items:
                remove_source(conn, trial.tenant_id, service.CATALOG_SOURCE)
        vectors = index(conn, trial.tenant_id, embedder, docs, embed=embed)
        indexed = page_count(conn, trial.tenant_id)
    times["index"] = round(time.monotonic() - step, 1)
    progress.update(step="agent", times=times, name=name, pages=indexed)

    step = time.monotonic()
    with conn.transaction():
        service._configure(conn, trial, name, items, mode)
    times["agent"] = round(time.monotonic() - step, 1)
    leftover = crawler.queue[: MAX_PAGES * 2] if crawler else []
    pending = found.pending if source == found.source else []
    background = bool(leftover or pending or not vectors)
    if background:
        enqueue(
            conn,
            service.JOB_KIND,
            {
                "trial_id": str(trial.id),
                "phase": "rest",
                "attempt": attempt,
                "round": 0,
                "urls": leftover,
                "products": pending,
            },
            idempotency_key=rest_key(trial.id, attempt, 0),
            # Sin vectores (p. ej. un 429 por minuto): después de que se renueve la ventana.
            run_after=datetime.now(UTC) + (REST_CONTINUE_WAIT if vectors else REST_FIRST_WAIT),
        )
    quality = {
        "platform": found.platform,
        "source": source,
        "by_source": _by_source(items),
        "found": len(items),
        "discarded": discarded,
        "pending": len(pending),
        "policies": state.policies,
        "pages": state.read,
        "llm_calls": llm.calls,
        "llm_tokens": llm.tokens,
        "seconds": round(time.monotonic() - started, 1),
        "blocked_home": blocked is not None,
        "requests": found.requests,
        "http": found.statuses,
    }
    progress.update(
        "ready",
        step="done",
        times=times,
        pages=indexed,
        products=len(items),
        generic=False,
        mode=mode,
        quality=quality,
        text_only=not vectors,
        background="pending" if background else "done",
        found=_live(items),
    )
    service._SANDBOXES.pop(trial.tenant_id, None)


def _by_source(items: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        key = str(item.get("source") or "structured_data")
        counts[key] = counts.get(key, 0) + 1
    return counts


def _build_sample(
    conn: psycopg.Connection,
    trial: service.Trial,
    progress: Progress,
    embedder: Embedder,
    started: float,
) -> None:
    """La prueba de ejemplo: el sitio no se pudo leer y el visitante eligió seguir igual."""
    assert trial.tenant_id is not None
    currency = "USD" if trial.language == "en" else "ARS"
    items = [
        {
            **catalog.Product(
                name=n, price=float(10 * (i + 2)), currency=currency, image="", url="",
                source="sample",
            ).as_dict(),
        }
        for i, n in enumerate(service.TEXTS[trial.language]["generic"])
    ]  # fmt: skip
    name = service._fit(trial.domain.split(".")[0].replace("-", " ").title()) or trial.domain
    docs = [
        Doc(
            title="Policies" if trial.language == "en" else "Políticas",
            source="politicas_de_ejemplo",
            text=SAMPLE_POLICIES[trial.language],
        ),
        catalog_doc(items, trial.language),
    ]
    with conn.transaction():
        conn.execute("update trials set products = %s where id = %s", (Jsonb(items), trial.id))
        vectors = index(conn, trial.tenant_id, embedder, docs)
        indexed = page_count(conn, trial.tenant_id)
    with conn.transaction():
        service._configure(conn, trial, name, items, "sample")
    progress.update(
        "ready",
        step="done",
        name=name,
        pages=indexed,
        products=len(items),
        generic=True,
        mode="sample",
        quality={
            "platform": "unknown",
            "source": "sample",
            "by_source": {"sample": len(items)},
            "found": 0,
            "discarded": {},
            "pending": 0,
            "policies": [],
            "pages": 0,
            "llm_calls": 0,
            "llm_tokens": 0,
            "seconds": round(time.monotonic() - started, 1),
        },
        text_only=not vectors,
        background="done",
        found=_live(items),
    )
    service._SANDBOXES.pop(trial.tenant_id, None)


def _site_source(conn: psycopg.Connection, tenant_id: uuid.UUID, url: str) -> None:
    """El sitio como fuente de la empresa, como lo deja el rastreo del onboarding."""
    import hashlib

    digest = hashlib.sha256(url.encode()).hexdigest()
    with tenant_scope(conn, tenant_id):
        conn.execute("delete from sources where content_hash = %s", (digest,))
        conn.execute(
            "insert into sources (tenant_id, kind, file_path, status, content_hash)"
            " values (%s, 'url', %s, 'done', %s)",
            (tenant_id, url, digest),
        )


def rest_key(trial_id: uuid.UUID, attempt: int, round_: int, part: int = 0) -> str:
    return f"trial-rest-{trial_id}-{attempt}-{round_}" + (f"-{part}" if part else "")


# -- segundo plano -------------------------------------------------------------------------------
def run_rest(
    conn: psycopg.Connection,
    payload: dict[str, Any],
    *,
    embedder: Embedder,
    http: httpx.Client | None = None,
    checkpoint: Callable[[], None] = _noop,
) -> None:
    """Con la prueba ya lista: suma los productos que quedaron del sitemap (hasta 150), lee el
    resto de las páginas (hasta `MAX_PAGES`) y completa los vectores que falten. Idempotente.

    Trabaja de a tramos de `REST_SLICE_SECONDS` y encola lo que queda como otra parte: la cola
    tiene un solo hilo y el armado de otro visitante no puede esperar minutos detrás de este
    segundo plano (visto en el QA: 58 s de espera para un armado de 15 s). Si siguen sin haber
    embeddings, vuelve a probar más tarde."""
    trial_id = uuid.UUID(str(payload["trial_id"]))
    trial = service.get(conn, trial_id)
    if trial is None or trial.status != "ready" or trial.tenant_id is None:
        return
    attempt = int(payload.get("attempt") or 1)
    if attempt != int(trial.progress.get("attempt") or 1):
        return
    tenant_id = trial.tenant_id
    progress = Progress(conn, trial_id, attempt, checkpoint)
    round_ = int(payload.get("round") or 0)
    part = int(payload.get("part") or 0)
    urls = [str(u) for u in payload.get("urls") or []]
    product_urls = [str(u) for u in payload.get("products") or []]
    client = http or safe_client()
    deadline = time.monotonic() + REST_SLICE_SECONDS
    try:
        if product_urls:
            product_urls = _more_products(
                conn, trial, progress, embedder, client, product_urls, deadline
            )
        if urls and not product_urls and time.monotonic() < deadline:
            room = MAX_PAGES - page_count(conn, tenant_id)
            pages: list[Page] = []
            if room > 0:
                origin = sources.origin_of(trial.site_url)
                crawler = Crawler(
                    trial.site_url,
                    client,
                    robots=sources.robots_for(client, origin),
                    pace=sources.Pace(),
                )
                crawler.seen.update(urls)
                crawler.queue = urls
                pages = crawler.read(room, deadline, lambda page: None)
                urls = crawler.queue[: MAX_PAGES * 2] if len(pages) < room else []
            else:
                urls = []
            docs = [doc_of(p) for p in pages if p.text.strip()]
            with conn.transaction():
                index(conn, tenant_id, embedder, docs)
            progress.update(pages=page_count(conn, tenant_id))
        if product_urls or urls:  # queda trabajo: otra parte, después de lo que esté en cola
            following = {**payload, "part": part + 1, "urls": urls, "products": product_urls}
            enqueue(
                conn,
                service.JOB_KIND,
                following,
                idempotency_key=rest_key(trial_id, attempt, round_, part + 1),
                run_after=datetime.now(UTC) + REST_CONTINUE_WAIT,
            )
            progress.update(background="pending")
            return
        complete = backfill(conn, tenant_id, embedder)
        if not complete and round_ < REST_ROUNDS:
            later = {**payload, "round": round_ + 1, "part": 0, "urls": [], "products": []}
            enqueue(
                conn,
                service.JOB_KIND,
                later,
                idempotency_key=rest_key(trial_id, attempt, round_ + 1),
                run_after=datetime.now(UTC) + REST_RETRY,
            )
        progress.update(text_only=not complete, background="done" if complete else "pending")
    except Superseded:
        return
    except SiteError as error:  # la prueba ya está lista: solo se anota
        logger.warning("Prueba %s: no se pudo leer el resto del sitio: %s", trial_id, error)


def _more_products(
    conn: psycopg.Connection,
    trial: service.Trial,
    progress: Progress,
    embedder: Embedder,
    client: httpx.Client,
    urls: list[str],
    deadline: float,
) -> list[str]:
    """Los productos del sitemap que no entraron en el armado: se suman al catálogo (sin repetir)
    y el agente los usa apenas quedan indexados. Devuelve las URLs que no llegó a leer."""
    assert trial.tenant_id is not None
    current = list(trial.products or [])
    room = service.MAX_PRODUCTS - len(current)
    if room <= 0:
        return []
    found, left = sources.read_products(client, trial.site_url, urls, deadline)
    discarded: dict[str, int] = {}
    known = {str(p["name"]).lower() for p in current}
    fresh = [
        p.as_dict()
        for p in catalog.select(found, service.MAX_PRODUCTS, discarded)
        if p.name.lower() not in known
    ][:room]
    if not fresh:
        return left if len(current) < service.MAX_PRODUCTS else []
    items = current + fresh
    with conn.transaction():
        conn.execute("update trials set products = %s where id = %s", (Jsonb(items), trial.id))
        index(conn, trial.tenant_id, embedder, [catalog_doc(items, trial.language)])
    quality = dict(trial.progress.get("quality") or {})
    quality.update(found=len(items), by_source=_by_source(items), pending=len(left))
    old = dict(quality.get("discarded") or {})
    for reason, count in discarded.items():
        old[reason] = old.get(reason, 0) + count
    quality["discarded"] = old
    progress.update(products=len(items), quality=quality, found=_live(items))
    service._SANDBOXES.pop(trial.tenant_id, None)
    return left if len(items) < service.MAX_PRODUCTS else []


# -- avisos por email ----------------------------------------------------------------------------
READY_EMAIL = {
    "es": {
        "subject": "Tu asistente de prueba para {name} está listo",
        "body": (
            "Tu asistente de prueba para {name} ya está listo.\n\nEntrá acá para probarlo: "
            "{link}\n\nUsa las políticas de tu sitio y una tienda de ejemplo con tus productos. "
            "No toca tu tienda real y se borra solo a los 7 días."
        ),
    },
    "en": {
        "subject": "Your trial assistant for {name} is ready",
        "body": (
            "Your trial assistant for {name} is ready.\n\nOpen it here to try it: {link}\n\n"
            "It uses your site's policies and a sample store with your products. It doesn't "
            "touch your real store and deletes itself after 7 days."
        ),
    },
}


def trial_link(token: str) -> str:
    public = (os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")
    return f"{public}/probar/{token}"


def _notify_ready(
    conn: psycopg.Connection, trial_id: uuid.UUID, mailer: Mailer, token: str
) -> None:
    """Un solo aviso por prueba, aunque el armado corra de nuevo."""
    row = conn.execute(
        "update trials set progress = progress || '{\"emailed\": true}'"
        " where id = %s and status = 'ready'"
        " and not coalesce((progress->>'emailed')::boolean, false)"
        " returning email, language, coalesce(progress->>'name', domain)",
        (trial_id,),
    ).fetchone()
    if row is None:
        return
    email, language, name = row
    texts = READY_EMAIL["en" if language == "en" else "es"]
    try:
        mailer.send(
            Email(
                to=email,
                subject=texts["subject"].format(name=name),
                body=texts["body"].format(name=name, link=trial_link(token)),
            )
        )
    except Exception:  # noqa: BLE001 — la prueba ya está lista: el aviso no la tira abajo
        logger.exception("No se pudo avisar por email que la prueba %s está lista", trial_id)


LANGUAGES = {"en": "English (/try)", "es": "Español (/probar)"}
STATUSES = {"ready": "lista", "failed": "no se pudo armar"}


def _counts(values: dict[str, Any]) -> str:
    return ", ".join(f"{k}: {v}" for k, v in sorted(values.items())) or "ninguno"


def operator_report(trial: service.Trial, link: str | None) -> tuple[str, str]:
    """(asunto, cuerpo) del aviso al operador, con el informe de calidad del armado."""
    progress = trial.progress
    quality = progress.get("quality") or {}
    public = (os.environ.get("PUBLIC_BASE_URL") or "http://localhost:8000").rstrip("/")
    status = STATUSES.get(trial.status, trial.status)
    if trial.status == "failed":
        status += f" ({progress.get('error_code') or 'internal'})"
    subject = f"Prueba gratis armada: {trial.name} · {trial.domain}"
    lines = [
        "Alguien armó una prueba gratis.",
        "",
        f"Tienda: {trial.name} ({trial.site_url})",
        f"Email: {trial.email}",
        f"Idioma: {LANGUAGES.get(trial.language, trial.language)}",
        f"Estado: {status}",
        f"Productos encontrados: {len(trial.products or [])}",
        f"Link de la prueba: {trial_link(link) if link else '(no disponible)'}",
        f"Panel: {public}/panel/operator/trials",
        "",
        "Informe de calidad",
        f"- Plataforma: {quality.get('platform', '-')}",
        f"- Fuente usada: {quality.get('source', '-')}",
        f"- Productos por fuente: {_counts(quality.get('by_source') or {})}",
        f"- Productos descartados: {_counts(quality.get('discarded') or {})}",
        f"- Productos pendientes (segundo plano): {quality.get('pending', 0)}",
        f"- Políticas encontradas: {', '.join(quality.get('policies') or []) or 'ninguna'}",
        f"- Páginas leídas: {quality.get('pages', progress.get('pages', 0))}",
        f"- Llamadas al modelo: {quality.get('llm_calls', 0)}"
        f" ({quality.get('llm_tokens', 0)} tokens)",
        f"- Segundos de armado: {quality.get('seconds', '-')}",
        f"- Pedidos al sitio (fuentes de catálogo): {quality.get('requests', 0)}"
        f" · estados HTTP: {_counts(quality.get('http') or {})}",
    ]
    if trial.status == "failed":
        lines.append(f"- Error: {progress.get('error') or '-'}")
    return subject, "\n".join(lines) + "\n"


def _notify_operator(
    conn: psycopg.Connection, trial_id: uuid.UUID, mailer: Mailer, link: str | None
) -> None:
    """Un solo aviso al operador por prueba (lista o fallida), aunque el armado corra de nuevo."""
    from app.panel import auth

    operator = auth.operator_email()
    if not operator:
        return
    row = conn.execute(
        "update trials set progress = progress || '{\"operator_emailed\": true}'"
        " where id = %s and status in ('ready', 'failed')"
        " and not coalesce((progress->>'operator_emailed')::boolean, false) returning id",
        (trial_id,),
    ).fetchone()
    trial = service.get(conn, trial_id)
    if row is None or trial is None:
        return
    subject, body = operator_report(trial, link)
    try:
        mailer.send(Email(to=operator, subject=subject, body=body))
    except Exception:  # noqa: BLE001 — el aviso no tira abajo la prueba
        logger.exception("No se pudo avisar al operador de la prueba %s", trial_id)


# -- el job --------------------------------------------------------------------------------------
def run_job(
    conn: psycopg.Connection,
    job: Job,
    *,
    embedder: Embedder | None = None,
    http: httpx.Client | None = None,
    mailer: Mailer | None = None,
    checkpoint: Callable[[], None] = _noop,
) -> None:
    """El job `trial_build`: el armado (con los avisos por email) o, con `phase: rest`, el
    segundo plano. Sin embedder, el de la prueba gratis (su propia clave de Gemini)."""
    from app.crypto import decrypt_for
    from app.llm.client import PostgresUsageRecorder
    from app.trial import keys

    payload = job.payload
    if embedder is None:
        embedder = keys.trial_embedder(PostgresUsageRecorder(conn))
    if payload.get("phase") == "rest":
        run_rest(conn, payload, embedder=embedder, http=http, checkpoint=checkpoint)
        return
    trial_id = uuid.UUID(str(payload["trial_id"]))
    link = None
    trial = service.get(conn, trial_id)
    if payload.get("link") and trial is not None and trial.tenant_id is not None:
        try:
            link = decrypt_for(trial.tenant_id, str(payload["link"]))
        except Exception:  # noqa: BLE001 — sin enlace no hay aviso, pero se arma igual
            logger.warning("No se pudo leer el enlace de la prueba %s", trial_id)
    attempt = payload.get("attempt")
    run_build(
        conn,
        trial_id,
        embedder=embedder,
        http=http,
        checkpoint=checkpoint,
        mailer=mailer,
        link=link,
        attempt=int(attempt) if attempt is not None else None,
    )
