"""La prueba gratis usa SOLO `TRIAL_GEMINI_API_KEY`, y esa clave la usa SOLO la prueba.

Decisión 100.

- El chat del agente de prueba, sus embeddings de consulta y el armado van con esa clave, por
  `LLMClient` y con el uso en `usage`.
- Sin la clave, o con su cuota agotada, la prueba no cae en las claves de la plataforma (Groq ni
  `GEMINI_API_KEY`): el visitante ve "la demo está muy ocupada" y su mensaje queda guardado.
- Nada fuera de la prueba la usa: ni las empresas, ni Acme, ni Northwind. Una prueba convertida
  en piloto pasa a la plataforma. La variable se lee en un solo lugar: `app/trial/keys.py`.
"""

import json
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest

from app.companies import agent as company_agent
from app.db.connection import tenant_scope
from app.llm.client import PostgresUsageRecorder
from app.llm.embedder import VoyageEmbedder
from app.mailer import Email
from app.trial import keys, models, service

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
TRIAL_KEY = "clave-de-la-prueba"
SITE = "https://tienda-sol.example"
PAGES = {
    "/": "<title>Tienda Sol</title><a href='/envios'>Envíos</a><p>Mates artesanales.</p>",
    "/envios": "<title>Envíos</title><p>Despachamos en 48 horas hábiles a todo el país.</p>",
}


class _Outbox:
    def __init__(self) -> None:
        self.sent: list[Email] = []

    def send(self, email: Email) -> None:
        self.sent.append(email)


class _Gemini:
    """La API de Gemini simulada: anota la clave de cada pedido."""

    def __init__(self, quota: bool = True) -> None:
        self.keys: list[str] = []
        self.quota = quota

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.keys.append(request.headers["x-goog-api-key"])
        if not self.quota:
            violation = {"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}
            return httpx.Response(
                429, json={"error": {"code": 429, "details": [{"violations": [violation]}]}}
            )
        if request.url.path.endswith(":batchEmbedContents"):
            count = len(json.loads(request.content)["requests"])
            return httpx.Response(
                200,
                json={
                    "embeddings": [{"values": [0.01] * 1024}] * count,
                    "usageMetadata": {"promptTokenCount": 5},
                },
            )
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": "Despachamos en 48 horas hábiles."}]},
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {"promptTokenCount": 40, "candidatesTokenCount": 8},
            },
        )


def _never(*args: Any, **kwargs: Any) -> Any:
    raise AssertionError("la prueba no puede usar los modelos ni los embeddings de la plataforma")


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("ALLOW_PRIVATE_SYSTEM_URLS", "1")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://agente.example")
    # Las claves de la plataforma están cargadas: la prueba igual no las tiene que usar.
    monkeypatch.setenv("GROQ_API_KEY", "clave-de-groq-de-la-plataforma")
    monkeypatch.setenv("GEMINI_API_KEY", "clave-de-gemini-de-la-plataforma")
    monkeypatch.setenv("TRIAL_GEMINI_API_KEY", TRIAL_KEY)
    monkeypatch.setenv("GEMINI_MIN_INTERVAL", "0")
    models.reset()
    service.reset_sandboxes()
    yield
    models.reset()
    service.reset_sandboxes()


def _gemini_http(monkeypatch: pytest.MonkeyPatch, api: _Gemini) -> None:
    monkeypatch.setattr(
        keys, "http_client", lambda *args: httpx.Client(transport=httpx.MockTransport(api))
    )


def _no_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(company_agent, "shared_provider", _never)
    monkeypatch.setattr("app.llm.factory.embedder_from_env", _never)
    monkeypatch.setattr("app.llm.factory.provider_from_env", _never)


def _site() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = PAGES.get(request.url.path)
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _ready_trial(conn: psycopg.Connection, lang: str = "es") -> tuple[str, service.Trial]:
    from simulated.voyage import voyage_transport

    box = _Outbox()
    token = service.start(conn, box, site=SITE, email="a@sol.example", language=lang, ip_hash="k")
    code = re.search(r"\b(\d{6})\b", box.sent[-1].body)
    assert code is not None
    trial = service.confirm(conn, token, code.group(1))
    embedder = VoyageEmbedder(
        PostgresUsageRecorder(conn),
        api_key="sim",
        http_client=httpx.Client(transport=voyage_transport()),
    )
    service.run_build(conn, trial.id, embedder=embedder, http=_site())
    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready" and ready.tenant_id is not None
    return token, ready


def _contact(conn: psycopg.Connection, tenant_id: uuid.UUID) -> uuid.UUID:
    from app.contacts import hash_phone

    with tenant_scope(conn, tenant_id):
        row = conn.execute(
            "insert into contacts (tenant_id, phone_hash) values (%s, %s) returning id",
            (tenant_id, hash_phone(f"web:{uuid.uuid4()}")),
        ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def _messages(
    conn: psycopg.Connection, tenant_id: uuid.UUID, case_id: uuid.UUID
) -> list[tuple[str, str]]:
    with tenant_scope(conn, tenant_id):
        rows = conn.execute(
            "select role, text from case_messages where case_id = %s order by created_at, id",
            (case_id,),
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


# -- el chat de la prueba ----------------------------------------------------------------------
def test_el_chat_de_la_prueba_usa_la_clave_de_la_prueba(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, trial = _ready_trial(conn)
    assert trial.tenant_id is not None
    api = _Gemini()
    _gemini_http(monkeypatch, api)
    _no_platform(monkeypatch)

    agent = company_agent.build_agent(conn, trial.tenant_id)
    result = agent.handle_message(
        trial.tenant_id, _contact(conn, trial.tenant_id), "¿Cuánto tarda el envío?"
    )
    assert "mucha demanda" not in result.message
    # Las búsquedas del chat también: el embedder de consulta del agente es el de la prueba.
    agent._embedder.embed(["envíos"], tenant_id=trial.tenant_id, input_type="query")  # type: ignore[attr-defined]
    assert len(api.keys) >= 2 and set(api.keys) == {TRIAL_KEY}
    with tenant_scope(conn, trial.tenant_id):
        usage = conn.execute(
            "select kind, model from usage where tenant_id = %s and created_at >= %s",
            (trial.tenant_id, trial.created_at),
        ).fetchall()
    llm = [model for kind, model in usage if kind == "llm"]
    assert llm and all(model.startswith("gemini") for model in llm)  # por LLMClient, en usage
    assert ("embedding", "gemini-embedding-2") in usage


@pytest.mark.parametrize("case", ["sin-clave", "cuota-agotada"])
def test_sin_clave_o_sin_cuota_la_demo_esta_ocupada(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    _, trial = _ready_trial(conn, lang="en")
    assert trial.tenant_id is not None
    api = _Gemini(quota=False)
    _gemini_http(monkeypatch, api)
    _no_platform(monkeypatch)  # ni Groq ni GEMINI_API_KEY, tampoco de respaldo
    if case == "sin-clave":
        monkeypatch.setenv("TRIAL_GEMINI_API_KEY", "")  # vacía: el .env no la vuelve a cargar

    agent = company_agent.build_agent(conn, trial.tenant_id)
    result = agent.handle_message(
        trial.tenant_id, _contact(conn, trial.tenant_id), "Where is my order?"
    )
    assert "very busy" in result.message and "few minutes" in result.message
    assert set(api.keys) <= {TRIAL_KEY}
    if case == "sin-clave":
        assert api.keys == []
    # El mensaje no se pierde, y el historial muestra lo mismo que vio el visitante.
    assert _messages(conn, trial.tenant_id, result.case_id) == [
        ("customer", "Where is my order?"),
        ("agent", result.message),
    ]

    # Agotada la cuota, el siguiente mensaje ni la vuelve a pedir por un rato.
    calls = len(api.keys)
    again = company_agent.build_agent(conn, trial.tenant_id).handle_message(
        trial.tenant_id, _contact(conn, trial.tenant_id), "Hello?"
    )
    assert "very busy" in again.message and len(api.keys) == calls


def test_el_armado_sin_la_clave_no_usa_la_de_la_plataforma(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.trial import build

    monkeypatch.setenv("TRIAL_GEMINI_API_KEY", "")  # vacía: el .env no la vuelve a cargar
    api = _Gemini()
    _gemini_http(monkeypatch, api)
    _no_platform(monkeypatch)
    box = _Outbox()
    token = service.start(conn, box, site=SITE, email="b@sol.example", language="es", ip_hash="k")
    code = re.search(r"\b(\d{6})\b", box.sent[-1].body)
    assert code is not None
    trial = service.confirm(conn, token, code.group(1))
    embedder = keys.trial_embedder(PostgresUsageRecorder(conn))
    build.run_build(conn, trial.id, embedder=embedder, http=_site())
    ready = service.find(conn, token)
    assert ready is not None and ready.status == "ready" and ready.progress["text_only"] is True
    assert api.keys == []


def test_un_429_por_minuto_no_deja_la_demo_ocupada(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Visto en el QA: el armado recibió un 429 por minuto y la prueba quedó "ocupada" 5 minutos.
    Solo la cuota diaria la bloquea."""
    _, trial = _ready_trial(conn)
    assert trial.tenant_id is not None

    def per_minute(request: httpx.Request) -> httpx.Response:
        violation = {"quotaId": "EmbedContentRequestsPerMinutePerProjectPerModel-FreeTier"}
        return httpx.Response(
            429, json={"error": {"code": 429, "details": [{"violations": [violation]}]}}
        )

    client = httpx.Client(transport=httpx.MockTransport(per_minute))
    embedder = keys.trial_embedder(PostgresUsageRecorder(conn), client=client)
    with pytest.raises(keys.TrialBusy):
        embedder.embed(["hola"], tenant_id=trial.tenant_id)
    assert not keys.busy()
    daily = keys.trial_embedder(
        PostgresUsageRecorder(conn),
        client=httpx.Client(transport=httpx.MockTransport(_Gemini(False))),
    )
    with pytest.raises(keys.TrialBusy):
        daily.embed(["hola"], tenant_id=trial.tenant_id)
    assert keys.busy()


# -- nada fuera de la prueba usa su clave --------------------------------------------------------
def test_empresas_acme_y_northwind_no_usan_la_clave_de_la_prueba(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.demo import acme, northwind

    def spy(*args: Any, **kwargs: Any) -> str:
        raise AssertionError("solo la prueba gratis puede usar TRIAL_GEMINI_API_KEY")

    platform = object()
    monkeypatch.setattr(keys, "api_key", spy)
    monkeypatch.setattr(company_agent, "shared_provider", lambda: platform)
    monkeypatch.setattr("app.llm.factory.embedder_from_env", lambda recorder: object())
    monkeypatch.setattr("app.llm.factory.provider_from_env", lambda: platform)

    row = conn.execute(
        "insert into tenants (name, settings) values (%s, '{}') returning id",
        (f"Normal {uuid.uuid4()}",),
    ).fetchone()
    assert row is not None
    normal = company_agent.build_agent(conn, row[0])
    assert normal._llm._provider is platform  # type: ignore[attr-defined]
    for built in (
        acme.build_demo_agent(conn, acme.shared_acme()),
        northwind.build_agent(conn, northwind.shared_northwind()),
    ):
        assert built._llm._provider is platform  # type: ignore[attr-defined]


def test_una_prueba_convertida_en_piloto_usa_la_plataforma(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, trial = _ready_trial(conn)
    assert trial.tenant_id is not None
    service.convert(
        conn, trial.id, company="Tienda Sol Piloto", email="c@sol.example", password_hash="x"
    )
    platform = object()
    monkeypatch.setattr(company_agent, "shared_provider", lambda: platform)
    monkeypatch.setattr("app.llm.factory.embedder_from_env", lambda recorder: object())
    assert models.agent_for(conn, trial.tenant_id) is None
    piloto = company_agent.build_agent(conn, trial.tenant_id)
    assert piloto._llm._provider is platform  # type: ignore[attr-defined]


def test_la_variable_se_lee_solo_en_trial_keys() -> None:
    allowed = BACKEND / "app" / "trial" / "keys.py"
    found = [
        path
        for folder in (BACKEND / "app", REPO / "evals", REPO / "mocks")
        for path in folder.rglob("*.py")
        if "TRIAL_GEMINI_API_KEY" in path.read_text("utf-8", errors="ignore")
    ]
    assert found == [allowed]


def test_el_parametro_por_defecto_no_cambia_nada_para_los_demas(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`build_agent(..., trial_agent=...)`: con una función que no arma nada, el agente es el de
    siempre aunque la empresa sea una prueba."""
    _, trial = _ready_trial(conn)
    assert trial.tenant_id is not None
    platform = object()
    monkeypatch.setattr(company_agent, "shared_provider", lambda: platform)
    monkeypatch.setattr("app.llm.factory.embedder_from_env", lambda recorder: object())
    plain = company_agent.build_agent(conn, trial.tenant_id, trial_agent=lambda c, t: None)
    assert plain._llm._provider is platform  # type: ignore[attr-defined]
    assert isinstance(company_agent.build_agent(conn, trial.tenant_id), models.TrialAgent)
