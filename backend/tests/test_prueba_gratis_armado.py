"""Prueba gratis: armado rápido y que nunca queda trabado (decisión 099).

Visto en producción: con la cuota diaria de embeddings de Gemini agotada, la prueba quedó más
de 7 minutos en "Leyendo las páginas" sin avanzar. El armado guardaba las páginas con un
embedding por página; el 429 hacía fallar el job, la transacción se revertía (también el paso a
"failed") y el reintento volvía a rastrear las 30 páginas.

Ahora: pocas páginas prioritarias en paralelo, embeddings en lote, tiempo máximo, búsqueda por
texto si no hay embeddings (los vectores se completan después), "failed" con un mensaje claro y
un reintento idempotente, y el avance en vivo para la pantalla.
"""

import html
import json
import re
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.companies.agent import build_guardian
from app.db.connection import tenant_scope
from app.jobs.queue import Job
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import Embedder, InputType, VoyageEmbedder
from app.llm.gemini import GeminiEmbedder, GeminiTransport
from app.mailer import Email
from app.main import app
from app.panel.routes import PanelDeps, panel_deps
from app.trial import build, keys, service

REPO = Path(__file__).resolve().parents[2]
SITE = "https://tienda-sol.example"
EMAIL = "duena@tienda-sol.example"


def _product(name: str, price: float, slug: str) -> str:
    data = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": name,
        "image": f"{SITE}/img/{slug}.jpg",
        "offers": {"@type": "Offer", "price": price, "priceCurrency": "ARS"},
    }
    return f'<title>{name}</title><script type="application/ld+json">{json.dumps(data)}</script>'


PAGES = {
    "/": (
        '<title>Tienda Sol</title><meta property="og:site_name" content="Tienda Sol">'
        '<a href="/politicas/devoluciones">Devoluciones</a> <a href="/envios">Envíos</a>'
        ' <a href="/productos/mate">Mate</a> <a href="/productos/termo">Termo</a>'
        "<p>Mates y termos artesanales.</p>"
    ),
    "/politicas/devoluciones": (
        "<title>Devoluciones</title><p>Tenés 30 días desde que recibís tu compra para "
        "devolverla. El reembolso se hace al mismo medio de pago.</p>"
    ),
    "/envios": "<title>Envíos</title><p>Despachamos en 48 horas hábiles a todo el país.</p>",
    "/productos/mate": _product("Mate de calabaza", 12500, "mate") + "<p>Curado a mano.</p>",
    "/productos/termo": _product("Termo acero 1 L", 38900, "termo") + "<p>Termo de acero.</p>",
}


def _site(pages: dict[str, str] | None = None, delay: float = 0.0) -> httpx.Client:
    served = PAGES if pages is None else pages

    def handler(request: httpx.Request) -> httpx.Response:
        if delay and request.url.path != "/":
            time.sleep(delay)
        body = served.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler))


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    def send(self, email: Email) -> None:
        self.sent.append(email)


class _Counting:
    """Cuenta las llamadas al embedder de verdad que envuelve."""

    def __init__(self, inner: Embedder) -> None:
        self.inner = inner
        self.dimensions = inner.dimensions
        self.calls = 0

    def embed(
        self,
        texts: list[str],
        *,
        tenant_id: uuid.UUID,
        input_type: InputType = "document",
        case_id: uuid.UUID | None = None,
    ) -> list[list[float]]:
        self.calls += 1
        return self.inner.embed(texts, tenant_id=tenant_id, input_type=input_type, case_id=case_id)


@pytest.fixture
def embedder(conn: psycopg.Connection) -> _Counting:
    from simulated.voyage import voyage_transport

    return _Counting(
        VoyageEmbedder(
            recorder=PostgresUsageRecorder(conn),
            api_key="sim",
            http_client=httpx.Client(transport=voyage_transport()),
        )
    )


def _gemini(conn: psycopg.Connection, handler: Callable[[httpx.Request], httpx.Response]) -> Any:
    transport = GeminiTransport(
        api_key="clave-de-test",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval=0,
        sleep=lambda seconds: None,
    )
    return GeminiEmbedder(PostgresUsageRecorder(conn), transport)


def _daily_quota(request: httpx.Request) -> httpx.Response:
    """El 429 de Gemini con la cuota diaria del nivel gratuito agotada."""
    return httpx.Response(
        429,
        json={
            "error": {
                "code": 429,
                "status": "RESOURCE_EXHAUSTED",
                "message": "You exceeded your current quota.",
                "details": [
                    {
                        "@type": "type.googleapis.com/google.rpc.QuotaFailure",
                        "violations": [
                            {"quotaId": "EmbedContentRequestsPerDayPerProjectPerModel-FreeTier"}
                        ],
                    }
                ],
            }
        },
    )


def _down(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("caído")


@pytest.fixture
def box(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[_Outbox]:
    monkeypatch.setenv("ALLOW_PRIVATE_SYSTEM_URLS", "1")  # el dominio de prueba no resuelve
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://agente.example")
    service.reset_sandboxes()
    outbox = _Outbox()
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=None,
        guardian=build_guardian,
        embedder=lambda c: None,  # type: ignore[arg-type,return-value]
        kick_worker=lambda: None,
        mailer=lambda: outbox,
    )
    try:
        yield outbox
    finally:
        app.dependency_overrides.clear()
        service.reset_sandboxes()


def _trial(
    conn: psycopg.Connection, box: _Outbox, site: str = SITE, email: str = EMAIL, lang: str = "es"
) -> tuple[str, service.Trial]:
    token = service.start(conn, box, site=site, email=email, language=lang, ip_hash="t")
    code = re.search(r"\b(\d{6})\b", box.sent[-1].body)
    assert code is not None
    trial = service.confirm(conn, token, code.group(1))
    assert trial.status == "building" and trial.tenant_id is not None
    return token, trial


def _job(conn: psycopg.Connection, trial_id: uuid.UUID, phase: str = "") -> Job:
    """El último job de armado encolado para la prueba (de la fase pedida)."""
    rows = conn.execute(
        "select id, payload from jobs where kind = %s and payload->>'trial_id' = %s"
        " order by coalesce((payload->>'attempt')::int, 0) desc,"
        " coalesce((payload->>'round')::int, 0) desc",
        (service.JOB_KIND, str(trial_id)),
    ).fetchall()
    for job_id, payload in rows:
        if (payload.get("phase") or "") == phase:
            return Job(job_id, service.JOB_KIND, payload, attempts=1, max_attempts=3)
    raise AssertionError(f"no hay job {phase!r}: {[r[1] for r in rows]}")


def _chunks(conn: psycopg.Connection, tenant_id: uuid.UUID) -> tuple[int, int]:
    """(fragmentos, fragmentos sin vector)."""
    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "select count(*), count(*) filter (where embedding is null) from chunks"
        ).fetchone()
    assert row is not None
    return row[0], row[1]


def _text_search(conn: psycopg.Connection, tenant_id: uuid.UUID, words: str) -> list[str]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select text from chunks where tsv @@ plainto_tsquery('spanish', %s)", (words,)
        ).fetchall()
    return [r[0] for r in rows]


# -- embeddings no disponibles -----------------------------------------------------------------
@pytest.mark.parametrize("failure", [_daily_quota, _down], ids=["cuota-diaria", "caido"])
def test_sin_embeddings_queda_lista_con_busqueda_por_texto(
    conn: psycopg.Connection, box: _Outbox, failure: Callable[[httpx.Request], httpx.Response]
) -> None:
    token, trial = _trial(conn, box)
    assert trial.tenant_id is not None
    service.run_build(conn, trial.id, embedder=_gemini(conn, failure), http=_site())

    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready", ready
    assert ready.progress["text_only"] is True
    total, without = _chunks(conn, trial.tenant_id)
    assert total > 0 and without == total
    # La búsqueda por texto del RAG híbrido ya encuentra las políticas.
    assert any("30 días" in t for t in _text_search(conn, trial.tenant_id, "devolver compra"))
    assert {p["name"] for p in ready.products} == {"Mate de calabaza", "Termo acero 1 L"}
    # Y queda encolado completar los vectores.
    assert _job(conn, trial.id, "rest").payload["trial_id"] == str(trial.id)


def test_los_vectores_se_completan_despues_sin_duplicar(
    conn: psycopg.Connection, box: _Outbox, embedder: _Counting
) -> None:
    token, trial = _trial(conn, box)
    assert trial.tenant_id is not None
    service.run_build(conn, trial.id, embedder=_gemini(conn, _daily_quota), http=_site())
    total, _ = _chunks(conn, trial.tenant_id)

    rest = _job(conn, trial.id, "rest")
    build.run_rest(conn, rest.payload, embedder=embedder, http=_site())
    build.run_rest(conn, rest.payload, embedder=embedder, http=_site())  # idempotente
    assert _chunks(conn, trial.tenant_id) == (total, 0)
    found = service.find(conn, token)
    assert found is not None and found.progress["text_only"] is False


def test_si_la_cuota_sigue_agotada_vuelve_a_probar_mas_tarde(
    conn: psycopg.Connection, box: _Outbox
) -> None:
    _, trial = _trial(conn, box)
    service.run_build(conn, trial.id, embedder=_gemini(conn, _daily_quota), http=_site())
    rest = _job(conn, trial.id, "rest")
    build.run_rest(conn, rest.payload, embedder=_gemini(conn, _daily_quota), http=_site())
    later = conn.execute(
        "select run_after - now(), payload from jobs where idempotency_key = %s",
        (build.rest_key(trial.id, rest.payload["attempt"], 1),),
    ).fetchone()
    assert later is not None and later[0].total_seconds() > 60 and later[1]["round"] == 1


# -- velocidad ---------------------------------------------------------------------------------
def test_embeddings_en_lote(conn: psycopg.Connection, box: _Outbox, embedder: _Counting) -> None:
    _, trial = _trial(conn, box)
    service.run_build(conn, trial.id, embedder=embedder, http=_site())
    assert embedder.calls == 1  # las 5 páginas y el catálogo, en una sola llamada


def test_con_el_tiempo_agotado_sigue_con_lo_que_haya(
    conn: psycopg.Connection, box: _Outbox, embedder: _Counting, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(build, "CRAWL_SECONDS", 0.5)
    token, _ = _trial(conn, box)
    trial = service.find(conn, token)
    assert trial is not None
    started = time.monotonic()
    service.run_build(conn, trial.id, embedder=embedder, http=_site(delay=30))
    assert time.monotonic() - started < 20  # no espera las páginas lentas (30 s cada una)
    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready"
    assert ready.progress["pages"] == 1  # solo la portada, que respondió a tiempo
    assert ready.progress["times"]["crawl"] < 2
    # Las que no llegaron a tiempo se leen en segundo plano.
    assert len(_job(conn, trial.id, "rest").payload["urls"]) == 4


def _acme_site() -> dict[str, str]:
    """El sitio de la tienda de demo Acme: sus políticas (evals/fixtures) y 30 productos."""
    docs = sorted((REPO / "evals/fixtures/acme/documents").glob("*.md"))
    orders = json.loads((REPO / "mocks/acme-api/data/orders.json").read_text("utf-8"))
    names = sorted(
        {item["name"]: item["unit_price"] for o in orders for item in o["items"]}.items()
    )
    pages: dict[str, str] = {}
    links = []
    for doc in docs:
        text = doc.read_text("utf-8")
        body = "".join(f"<p>{line}</p>" for line in text.splitlines() if line.strip())
        pages[f"/politicas/{doc.stem}"] = f"<title>{doc.stem.replace('_', ' ')}</title>{body}"
        links.append(f'<a href="/politicas/{doc.stem}">{doc.stem}</a>')
    for n in range(30):
        name, price = names[n % len(names)]
        slug = f"p{n}"
        pages[f"/productos/{slug}"] = _product(f"{name} {n}", price, slug) + f"<p>{name}.</p>"
        links.append(f'<a href="/productos/{slug}">{name}</a>')
    pages["/"] = "<title>Acme Store</title><p>Ropa y calzado.</p>" + "".join(links)
    return pages


def test_armado_de_acme_en_menos_de_60_segundos(
    conn: psycopg.Connection, box: _Outbox, embedder: _Counting
) -> None:
    pages = _acme_site()
    token, trial = _trial(conn, box, site="https://acme-store.example", email="a@acme.example")
    assert trial.tenant_id is not None
    started = time.monotonic()
    # Con una demora de red de 0,4 s por página, en serie serían 30 × 0,4 s solo para leer.
    service.run_build(conn, trial.id, embedder=embedder, http=_site(pages, delay=0.4))
    elapsed = time.monotonic() - started
    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready"
    assert elapsed < 60, elapsed
    assert ready.progress["pages"] == build.FIRST_PAGES
    assert len(ready.products) >= 3
    assert {"returns", "shipping"} <= set(ready.progress["policies"])

    # El resto de las 30 páginas, en segundo plano después de "ready".
    rest = _job(conn, trial.id, "rest")
    build.run_rest(conn, rest.payload, embedder=embedder, http=_site(pages))
    done = service.find(conn, token)
    assert done is not None and done.progress["pages"] == build.MAX_PAGES


# -- fallas, reintento y estado ------------------------------------------------------------------
def test_si_el_sitio_no_responde_pasa_a_failed_con_un_mensaje_claro(
    conn: psycopg.Connection, box: _Outbox, embedder: _Counting
) -> None:
    client = TestClient(app)
    token, trial = _trial(conn, box, lang="en")
    down = httpx.Client(transport=httpx.MockTransport(_down))
    service.run_build(conn, trial.id, embedder=embedder, http=down)  # no lanza: no se reintenta

    failed = service.find(conn, token)
    assert failed is not None and failed.status == "failed"
    state = client.get(f"/probar/{token}/estado").json()
    assert state["status"] == "failed"
    assert "tienda-sol.example" in state["message"] and "public" in state["message"]
    page = client.get(f"/probar/{token}").text
    assert "Try again" in page and f"/probar/{token}/reintentar" in page


def test_un_error_inesperado_tambien_termina_en_failed(
    conn: psycopg.Connection, box: _Outbox, embedder: _Counting, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("se rompió algo")

    monkeypatch.setattr(service, "_configure", broken)
    token, trial = _trial(conn, box)
    service.run_build(conn, trial.id, embedder=embedder, http=_site())
    failed = service.find(conn, token)
    assert failed is not None and failed.status == "failed"
    assert failed.progress["error_code"] == "internal"


def test_reintentar_es_idempotente(
    conn: psycopg.Connection, box: _Outbox, embedder: _Counting
) -> None:
    client = TestClient(app)
    token, trial = _trial(conn, box)
    assert trial.tenant_id is not None
    down = httpx.Client(transport=httpx.MockTransport(_down))
    service.run_build(conn, trial.id, embedder=embedder, http=down)
    first = _job(conn, trial.id)

    # Doble clic en "Reintentar": un solo armado nuevo.
    for _ in range(2):
        response = client.post(f"/probar/{token}/reintentar", follow_redirects=False)
        assert response.status_code == 303
    again = service.find(conn, token)
    assert again is not None and again.status == "building"
    retry = _job(conn, trial.id)
    assert retry.id != first.id and retry.payload["attempt"] == 2
    count = conn.execute(
        "select count(*) from jobs where kind = %s and payload->>'trial_id' = %s",
        (service.JOB_KIND, str(trial.id)),
    ).fetchone()
    assert count == (2,)

    # El job viejo ya no hace nada; el nuevo, dos veces, arma una sola vez.
    build.run_job(conn, first, embedder=embedder, http=_site())
    assert (service.find(conn, token) or again).status == "building"
    build.run_job(conn, retry, embedder=embedder, http=_site())
    build.run_job(conn, retry, embedder=embedder, http=_site())
    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready"
    with tenant_scope(conn, trial.tenant_id):
        docs = conn.execute("select count(*) from documents").fetchone()
    assert docs == (6,)  # 5 páginas y el catálogo, sin duplicar


def test_si_sigue_armando_despues_de_2_minutos_se_puede_reintentar(
    conn: psycopg.Connection, box: _Outbox
) -> None:
    client = TestClient(app)
    token, trial = _trial(conn, box)
    assert client.get(f"/probar/{token}/estado").json()["stale"] is False
    client.post(f"/probar/{token}/reintentar")
    assert (service.find(conn, token) or trial).progress["attempt"] == 1  # todavía no

    conn.execute(
        "update trials set progress = progress || jsonb_build_object("
        "'started_at', now() - interval '3 minutes', 'at', now() - interval '3 minutes')"
        " where id = %s",
        (trial.id,),
    )
    state = client.get(f"/probar/{token}/estado").json()
    assert state["status"] == "building" and state["stale"] is True
    client.post(f"/probar/{token}/reintentar")
    assert (service.find(conn, token) or trial).progress["attempt"] == 2


def test_el_estado_muestra_paginas_y_productos_en_vivo(
    conn: psycopg.Connection, box: _Outbox, embedder: _Counting
) -> None:
    client = TestClient(app)
    token, trial = _trial(conn, box, lang="en")
    seen: list[dict[str, Any]] = []
    service.run_build(
        conn,
        trial.id,
        embedder=embedder,
        http=_site(),
        checkpoint=lambda: seen.append(client.get(f"/probar/{token}/estado").json()),
    )
    reading = [s for s in seen if s["status"] == "building" and s["step"] == "crawl"]
    assert reading, seen
    assert any(0 < s["pages_read"] < s["pages_total"] for s in reading)
    assert all(s["current"].startswith(SITE) for s in reading if s["current"])
    last = reading[-1]
    assert {p["name"] for p in last["products"]} == {"Mate de calabaza", "Termo acero 1 L"}
    assert all(p["image"].endswith(".jpg") and p["price"] > 0 for p in last["products"])
    assert {"returns", "shipping"} <= {p["kind"] for p in last["policies"]}
    assert "Returns" in {p["label"] for p in last["policies"]}
    assert [s["step"] for s in seen if s["status"] == "building"][-1] in ("index", "agent")
    assert seen[-1]["status"] == "ready" and seen[-1]["progress_percent"] == 100
    assert all(isinstance(s["elapsed"], int | float) for s in seen)


def test_avisa_por_email_cuando_esta_lista_una_sola_vez(
    conn: psycopg.Connection, box: _Outbox, embedder: _Counting
) -> None:
    token, trial = _trial(conn, box, lang="en")
    job = _job(conn, trial.id)
    sent = len(box.sent)
    build.run_job(conn, job, embedder=embedder, http=_site(), mailer=box)
    build.run_job(conn, job, embedder=embedder, http=_site(), mailer=box)
    assert len(box.sent) == sent + 1
    email = box.sent[-1]
    assert email.to == EMAIL and "ready" in email.subject.lower()
    assert f"https://agente.example/probar/{token}" in email.body


def test_la_pantalla_de_armado_en_los_dos_idiomas(conn: psycopg.Connection, box: _Outbox) -> None:
    client = TestClient(app)
    token, _ = _trial(conn, box, lang="en")
    page = html.unescape(client.get(f"/probar/{token}").text)
    assert "This usually takes under a minute" in page
    assert "we'll also email you when it's ready" in page
    assert 'role="progressbar"' in page and 'name="viewport"' in page
    token_es, _ = _trial(conn, box, site="https://otra.example", email="b@otra.example")
    page_es = client.get(f"/probar/{token_es}").text
    assert "Suele tardar menos de un minuto" in page_es and 'lang="es"' in page_es


def test_el_nombre_de_la_tienda_entra_en_el_widget() -> None:
    """Visto con enterprisedataai.com: el título usa "·" y el nombre salía con 61 caracteres;
    el widget acepta hasta 60 y el armado fallaba al configurar el agente."""
    title = "Enterprise Data AI · AI Agents That Resolve Customer Requests"
    assert service._store_name("", title, "enterprisedataai.com") == "Enterprise Data AI"
    assert service._store_name("", "Tienda • Ofertas", "otra.example") == "Tienda"
    assert len(service._store_name("x" * 79, "", "a.example")) <= 60
    assert len(service._store_name("", "y" * 75, "a.example")) <= 60


# -- la clave de la prueba gratis ----------------------------------------------------------------
def test_usa_la_clave_propia_de_la_prueba_y_nunca_la_de_la_plataforma(
    conn: psycopg.Connection, box: _Outbox, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sin `TRIAL_GEMINI_API_KEY` no hay respaldo con la clave de la plataforma (decisión 100)."""
    keys.reset()
    _, trial = _trial(conn, box)
    assert trial.tenant_id is not None
    used: list[str] = []

    def gemini(request: httpx.Request) -> httpx.Response:
        used.append(request.headers["x-goog-api-key"])
        count = len(json.loads(request.content)["requests"])
        return httpx.Response(
            200,
            json={
                "embeddings": [{"values": [0.1] * 1024}] * count,
                "usageMetadata": {"promptTokenCount": 7},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(gemini))
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-siempre")
    monkeypatch.setenv("TRIAL_GEMINI_API_KEY", "clave-de-la-prueba")
    recorder = PostgresUsageRecorder(conn)
    keys.trial_embedder(recorder, client=http).embed(["hola"], tenant_id=trial.tenant_id)
    monkeypatch.setenv("TRIAL_GEMINI_API_KEY", "")  # vacía: el .env no la vuelve a cargar
    with pytest.raises(keys.TrialBusy):
        keys.trial_embedder(recorder, client=http).embed(["hola"], tenant_id=trial.tenant_id)
    assert used == ["clave-de-la-prueba"]
    with tenant_scope(conn, trial.tenant_id):
        usage = conn.execute(
            "select kind, input_tokens from usage where tenant_id = %s", (trial.tenant_id,)
        ).fetchall()
    assert usage == [("embedding", 7)]  # todo uso queda registrado
