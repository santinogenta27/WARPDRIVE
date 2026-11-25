"""Armado rápido de la prueba gratis (decisión 099).

Desde los ojos del visitante, el agente tiene que estar listo en menos de un minuto, siempre:

1. Lee primero pocas páginas prioritarias (la portada, las políticas y algunos productos, hasta
   `FIRST_PAGES`), en paralelo y con timeout por página. Pasado `CRAWL_SECONDS` sigue con lo que
   haya leído.
2. Indexa esas páginas y el catálogo con una sola llamada de embeddings. Si no hay embeddings
   (cuota agotada o caída), las indexa igual: la búsqueda por texto (tsvector) del RAG híbrido ya
   las encuentra, y los vectores se completan después.
3. Configura el agente y la prueba queda "ready". El resto de las páginas (hasta `MAX_PAGES`) se
   lee en segundo plano, en otro job de la misma cola (`phase: rest`).

Si algo falla de verdad, la prueba pasa a "failed" con un código que la página traduce en un
mensaje claro, y el visitante puede reintentar. Nunca queda en "building" para siempre.

El avance se guarda en `trials.progress` y, en el job, se confirma en cada paso (`checkpoint`):
así la página lo muestra en vivo. Cada job lleva el número de intento: el de un intento viejo no
toca nada. Todo es idempotente: cada página reemplaza lo suyo.

El contenido de las páginas es dato: solo se guarda su texto (sin tarjetas ni DNI) y los campos
de producto que lee `catalog`.
"""

import html
import logging
import os
import re
import time
import uuid
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
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
from app.onboarding.ingest import (
    SITE_MAX_BYTES,
    SKIP_EXTENSIONS,
    USER_AGENT,
    remove_source,
    source_key,
)
from app.rag.indexer import chunk_text, vector_literal
from app.trial import catalog, service

logger = logging.getLogger(__name__)

FIRST_PAGES = 8  # con estas el agente ya queda listo
MAX_PAGES = 30  # con las de segundo plano
POLICY_FIRST = 4  # de las primeras: políticas…
PRODUCT_FIRST = 3  # …y productos (el resto, la portada y otras)
WORKERS = 6
PAGE_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
CRAWL_SECONDS = 20.0  # para leer las primeras páginas
BUILD_SECONDS = 45.0  # todo el armado; los embeddings se saltean si no queda tiempo
EMBED_MIN_SECONDS = 8.0
REST_SECONDS = 120.0
REST_RETRY = timedelta(minutes=30)  # si siguen sin haber embeddings
REST_FIRST_WAIT = timedelta(seconds=75)
REST_ROUNDS = 8
EMBED_BATCH = 100
MAX_LIVE_PRODUCTS = 12
BLOCKED = {401, 403, 429, 503}
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


class SiteError(Exception):
    """El sitio no se pudo leer. `code` elige el mensaje para el visitante."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


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


def _unsafe(error: net.UnsafeURL) -> SiteError:
    """Un dominio que no resuelve no se pudo abrir; uno interno no es público."""
    message = str(error)
    return SiteError("unreachable" if "resolver" in message else "unsafe", message)


def _hosts(host: str) -> set[str]:
    bare = host.removeprefix("www.")
    return {bare, f"www.{bare}"}


def fetch(client: httpx.Client, url: str, hosts: set[str]) -> tuple[Page | None, int]:
    """Una página y su estado HTTP. Sigue redirecciones solo dentro del sitio."""
    current = url
    for _ in range(4):
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
        markup = response.text
        parser = extract.HtmlText()
        parser.feed(markup)
        title = " ".join(html.unescape(parser.title).split()) or current
        return (
            Page(
                url=current,
                title=title,
                text=parser.text(),
                links=tuple(parser.links),
                products=tuple(catalog.extract(markup, current)),
                declared=catalog.site_name(markup),
            ),
            200,
        )
    return None, 0


class Crawler:
    """Lee páginas del sitio en paralelo, sin salir de su dominio, con tiempo máximo."""

    def __init__(self, start: str, http: httpx.Client | None = None) -> None:
        parts = urlsplit(start.strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise SiteError("unsafe", "la dirección no es válida")
        if not net.allow_private():
            if parts.scheme != "https":
                raise SiteError("unsafe", "la dirección tiene que ser HTTPS")
            try:  # nunca contra una dirección interna (decisión 076)
                net.public_address(parts.hostname)
            except net.UnsafeURL as error:
                raise _unsafe(error) from error
        self.start = page_url(start.strip())
        self.hosts = _hosts(parts.hostname.lower())
        self.client = http or net.safe_client(
            max_bytes=SITE_MAX_BYTES, timeout=PAGE_TIMEOUT, headers={"User-Agent": USER_AGENT}
        )
        self.seen: set[str] = {self.start}
        self.queue: list[str] = []  # en el orden en que aparecen
        self.taken = {"policy": 0, "product": 0, "other": 0}
        self.covered: set[str] = set()  # tipos de política ya elegidos

    def add_links(self, page: Page) -> None:
        for link in page.links:
            found = page_url(urljoin(page.url, link))
            parts = urlsplit(found)
            if (
                parts.scheme in ("http", "https")
                and (parts.hostname or "").lower() in self.hosts
                and found not in self.seen
                and kind(found) != "skip"
            ):
                self.seen.add(found)
                self.queue.append(found)

    def home(self) -> Page:
        try:
            page, status = fetch(self.client, self.start, self.hosts)
        except net.UnsafeURL as error:
            raise _unsafe(error) from error
        except (httpx.HTTPError, net.ResponseTooLarge) as error:
            raise SiteError("unreachable", str(error)[:200]) from error
        if page is None:
            code = "blocked" if status in BLOCKED else "unreachable"
            raise SiteError(code, f"HTTP {status}")
        self.seen.add(page.url)
        self.add_links(page)
        return page

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
    ) -> list[Page]:
        """Hasta `limit` páginas de la cola, en tandas paralelas, hasta `deadline`. Una página que
        falla se saltea; las que no llegan a tiempo se abandonan."""
        pages: list[Page] = []
        pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="trial-crawl")
        try:
            while len(pages) < limit and time.monotonic() < deadline:
                batch = self.pick(limit - len(pages))
                if not batch:
                    break
                on_start(len(pages) + len(batch) + min(len(self.queue), limit))
                futures: dict[Future[tuple[Page | None, int]], str] = {
                    pool.submit(fetch, self.client, url, self.hosts): url for url in batch
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


def _live(products: list[catalog.Product]) -> list[dict[str, Any]]:
    kept = [p for p in catalog.unique(products) if not service._GIFT_CARD.search(p.name)]
    return [
        {"name": p.name, "price": p.price, "currency": p.currency, "image": p.image}
        for p in kept[:MAX_LIVE_PRODUCTS]
    ]


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
            pass
        return
    if mailer is not None and link:
        _notify_ready(conn, trial_id, mailer, link)
        checkpoint()


def _build(
    conn: psycopg.Connection,
    trial: service.Trial,
    progress: Progress,
    embedder: Embedder,
    http: httpx.Client | None,
    attempt: int,
) -> None:
    assert trial.tenant_id is not None
    tenant_id = trial.tenant_id
    started = time.monotonic()
    times: dict[str, float] = {}
    found: list[catalog.Product] = []
    policies: list[str] = []
    state = {"read": 0, "total": FIRST_PAGES}

    def seen(page: Page) -> None:
        state["read"] += 1
        found.extend(page.products)
        detected = policy_kind(page.url, page.title)
        if detected and detected not in policies:
            policies.append(detected)
        progress.update(
            step="crawl",
            pages_read=state["read"],
            pages_total=max(state["total"], state["read"]),
            current=page.url,
            found=_live(found),
            policies=policies,
        )

    def planned(total: int) -> None:
        state["total"] = min(FIRST_PAGES, max(total, state["read"]))

    progress.update(
        step="crawl", pages_read=0, pages_total=FIRST_PAGES, current=trial.site_url,
        found=[], policies=[], times={}, error=None, error_code=None,
    )  # fmt: skip
    crawler = Crawler(trial.site_url, http)
    home = crawler.home()
    planned(1 + len(crawler.queue))
    seen(home)
    pages = [home]
    pages += crawler.read(
        FIRST_PAGES - 1, started + CRAWL_SECONDS, seen, on_start=lambda n: planned(1 + n)
    )
    readable = [p for p in pages if p.text.strip()]
    if not readable:
        raise SiteError("empty", "no se encontró texto para leer")
    times["crawl"] = round(time.monotonic() - started, 1)
    progress.update(step="index", times=times, current="", pages_total=state["read"])

    # Los productos: los de las páginas, sin tarjetas de regalo; si no hay, de ejemplo.
    kept = [p for p in found if not service._GIFT_CARD.search(p.name)]
    products = [p.__dict__ for p in catalog.unique(kept, service.MAX_PRODUCTS)]
    generic = not products
    if generic:
        currency = "USD" if trial.language == "en" else "ARS"
        products = [
            {"name": n, "price": float(10 * (i + 2)), "currency": currency, "image": "", "url": ""}
            for i, n in enumerate(service.TEXTS[trial.language]["generic"])
        ]
    name = service._store_name(home.declared, home.title, trial.domain)
    step = time.monotonic()
    docs = [doc_of(p) for p in readable]
    docs.append(
        Doc(
            title="Catalog" if trial.language == "en" else "Catálogo",
            source=service.CATALOG_SOURCE,
            text=service._catalog_text(products, trial.language),
        )
    )
    embed = time.monotonic() < started + BUILD_SECONDS - EMBED_MIN_SECONDS
    with conn.transaction():
        conn.execute("update trials set products = %s where id = %s", (Jsonb(products), trial.id))
        _site_source(conn, tenant_id, trial.site_url)
        vectors = index(conn, tenant_id, embedder, docs, embed=embed)
        indexed = page_count(conn, tenant_id)
    times["index"] = round(time.monotonic() - step, 1)
    progress.update(step="agent", times=times, name=name, pages=indexed)

    step = time.monotonic()
    with conn.transaction():
        service._configure(conn, trial, name, products)
    times["agent"] = round(time.monotonic() - step, 1)
    leftover = crawler.queue[: MAX_PAGES * 2]
    if leftover or not vectors:
        payload = {
            "trial_id": str(trial.id),
            "phase": "rest",
            "attempt": attempt,
            "round": 0,
            "urls": leftover,
        }
        enqueue(
            conn,
            service.JOB_KIND,
            payload,
            idempotency_key=rest_key(trial.id, attempt, 0),
            # Sin vectores (p. ej. un 429 por minuto): después de que se renueve la ventana.
            run_after=None if vectors else datetime.now(UTC) + REST_FIRST_WAIT,
        )
    progress.update(
        "ready",
        step="done",
        times=times,
        pages=indexed,
        products=len(products),
        generic=generic,
        text_only=not vectors,
        background="pending" if leftover or not vectors else "done",
        found=_live(found),
    )
    service._SANDBOXES.pop(tenant_id, None)


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


def rest_key(trial_id: uuid.UUID, attempt: int, round_: int) -> str:
    return f"trial-rest-{trial_id}-{attempt}-{round_}"


# -- segundo plano -------------------------------------------------------------------------------
def run_rest(
    conn: psycopg.Connection,
    payload: dict[str, Any],
    *,
    embedder: Embedder,
    http: httpx.Client | None = None,
    checkpoint: Callable[[], None] = _noop,
) -> None:
    """Con la prueba ya lista: lee el resto de las páginas (hasta `MAX_PAGES`) y completa los
    vectores que falten. Si siguen sin haber embeddings, vuelve a probar más tarde."""
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
    urls = [str(u) for u in payload.get("urls") or []]
    try:
        if urls:
            room = MAX_PAGES - page_count(conn, tenant_id)
            pages: list[Page] = []
            if room > 0:
                crawler = Crawler(trial.site_url, http)
                crawler.seen.update(urls)
                crawler.queue = urls
                pages = crawler.read(room, time.monotonic() + REST_SECONDS, lambda page: None)
            docs = [doc_of(p) for p in pages if p.text.strip()]
            with conn.transaction():
                index(conn, tenant_id, embedder, docs)
            progress.update(pages=page_count(conn, tenant_id))
        complete = backfill(conn, tenant_id, embedder)
        if not complete and round_ < REST_ROUNDS:
            later = {**payload, "round": round_ + 1, "urls": []}
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


# -- aviso por email -----------------------------------------------------------------------------
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
    """El job `trial_build`: el armado (con el aviso por email) o, con `phase: rest`, el
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
