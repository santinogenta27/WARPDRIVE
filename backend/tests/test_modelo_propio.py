"""Key propia completa y vista de gasto — test de aceptación (decisión 102).

- Settings → Usage & model: proveedor y modelo de una lista corta, "Test key" con errores en
  palabras simples, la key enmascarada (nunca en el HTML), "Replace" y "Remove".
- El agente usa el modelo elegido; solo los admins ven y cambian esto; cada cambio queda en
  `audit_events` sin la key.
- Settings → Usage: los totales salen de `usage` y coinciden; CSV; "price unknown";
  aislamiento entre empresas; aviso al 80 % y derivación al 100 % con key propia y con la de la
  plataforma.
"""

import csv
import html
import io
import json
import logging
import re
import uuid
from collections.abc import Iterator
from contextlib import nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from simulated.stack import build_simulated_stack

from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.llm import pricing, spending, tenant_llm
from app.llm.client import AnthropicProvider, LLMClient, PostgresUsageRecorder
from app.llm.types import LLMResponse, TokenUsage
from app.main import app
from app.panel import auth
from app.panel.routes import PanelDeps, panel_deps

NORTE, NORTE_PASSWORD = "ana@tiendanorte.example", "clave-de-tienda-norte"
SUR, SUR_PASSWORD = "beto@tiendasur.example", "clave-de-tienda-sur"
TEAM, TEAM_PASSWORD = "carla@tiendanorte.example", "clave-de-carla-123"
VALID = "sk-ant-api03-clave-valida-de-norte-a1b2"
INVALID = "sk-ant-api03-clave-invalida-9999"
NO_CREDIT = "sk-ant-api03-sin-saldo-7777"
NO_MODEL = "sk-ant-api03-sin-modelo-5555"


class _Fake:
    """El proveedor simulado: responde según la key, como lo harían los de verdad."""

    calls: list[str] = []

    def __init__(self, provider: str, key: str) -> None:
        self.provider = provider
        self.key = key

    def create(self, *, model: str, **_: Any) -> LLMResponse:
        _Fake.calls.append(model)
        request = httpx.Request("POST", "https://api.example/v1/messages")
        if self.key == INVALID:
            body = {"error": {"type": "authentication_error", "message": "invalid x-api-key"}}
            raise httpx.HTTPStatusError(
                "401", request=request, response=httpx.Response(401, json=body, request=request)
            )
        if self.key == NO_CREDIT:
            body = {"error": {"message": "Your credit balance is too low to access the API."}}
            raise httpx.HTTPStatusError(
                "400", request=request, response=httpx.Response(400, json=body, request=request)
            )
        if self.key == NO_MODEL:
            body = {"error": {"type": "not_found_error", "message": f"model: {model}"}}
            raise httpx.HTTPStatusError(
                "404", request=request, response=httpx.Response(404, json=body, request=request)
            )
        return LLMResponse(text="OK", model=model, stop_reason="end_turn", usage=TokenUsage(8, 2))


@pytest.fixture(autouse=True)
def _fake_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    _Fake.calls = []
    tenant_llm.reset_tested()
    monkeypatch.setattr(
        tenant_llm, "_inner", lambda provider, key, models, quick=False: _Fake(provider, key)
    )


@pytest.fixture
def panel(conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch) -> Iterator[uuid.UUID]:
    monkeypatch.setenv("PANEL_EMAIL", "operador@plataforma.example")
    monkeypatch.setenv("PANEL_PASSWORD", "clave-del-operador")
    monkeypatch.setattr(auth, "DEFAULT_LANGUAGE", "en")  # el panel del Hito 13, en inglés
    auth._failures.clear()
    stack = build_simulated_stack(conn)
    company = create_company(conn, "Tienda Norte", NORTE, NORTE_PASSWORD)
    with tenant_scope(conn, company.tenant_id):
        conn.execute(
            "insert into users (tenant_id, email, role, password_hash)"
            " values (%s, %s, 'agent', %s)",
            (company.tenant_id, TEAM, auth.hash_password(TEAM_PASSWORD)),
        )
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: nullcontext(conn),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    try:
        yield company.tenant_id
    finally:
        app.dependency_overrides.clear()


def _login(email: str = NORTE, password: str = NORTE_PASSWORD) -> TestClient:
    client = TestClient(app)
    response = client.post(
        "/panel/login", data={"email": email, "password": password}, follow_redirects=False
    )
    assert response.status_code == 303, response.text
    return client


def _csrf(client: TestClient, path: str = "/panel/settings/usage") -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
    assert match is not None
    return match.group(1)


def _page(client: TestClient, path: str) -> str:
    return html.unescape(client.get(path).text)


def _save(client: TestClient, key: str, model: str = "claude-haiku-5-5") -> str:
    return html.unescape(
        client.post(
            "/panel/settings/usage/key",
            data={"csrf": _csrf(client), "provider": "anthropic", "model": model, "key": key},
            follow_redirects=True,
        ).text
    )


def _audit(conn: psycopg.Connection, tenant: uuid.UUID) -> list[tuple[str, dict[str, Any]]]:
    with tenant_scope(conn, tenant):
        rows = conn.execute(
            "select kind, payload from audit_events where case_id is null and kind like 'llm_%'"
            " order by created_at, seq"
        ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _settings_text(conn: psycopg.Connection, tenant: uuid.UUID) -> str:
    with tenant_scope(conn, tenant):
        row = conn.execute("select settings::text from tenants where id = %s", (tenant,)).fetchone()
    assert row is not None
    return str(row[0])


# -- 1. la key ----------------------------------------------------------------------------------
def test_guarda_una_key_valida_y_rechaza_una_invalida(
    conn: psycopg.Connection, panel: uuid.UUID, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = _login()
    page = _page(client, "/panel/settings/usage")
    for expected in ("Claude Sonnet", "Claude Haiku", "GPT-5 mini", "Gemini", "Test key"):
        assert expected in page, expected

    rejected = _save(client, INVALID)
    assert "This key isn't valid" in rejected
    assert tenant_llm.own_provider(conn, panel) is None  # no se guarda una key que no pasó

    saved = _save(client, VALID)
    assert "Connected" in saved
    own = tenant_llm.own_key(conn, panel)
    assert own is not None and own.provider == "anthropic" and own.model == "claude-haiku-5-5"
    page = _page(client, "/panel/settings/usage")
    assert "sk-…a1b2" in page and "Replace" in page and "Remove" in page
    # La key nunca sale: ni en el HTML, ni en la base sin cifrar, ni en la auditoría, ni en logs.
    for leak in (page, saved, _settings_text(conn, panel), json.dumps(_audit(conn, panel))):
        assert VALID not in leak and INVALID not in leak
    assert VALID not in caplog.text and INVALID not in caplog.text
    kinds = [k for k, _ in _audit(conn, panel)]
    assert kinds == ["llm_key_saved"]
    assert _audit(conn, panel)[0][1]["by"] == NORTE


def test_test_key_explica_el_error_en_palabras_simples(
    conn: psycopg.Connection, panel: uuid.UUID
) -> None:
    client = _login()
    expected = {
        VALID: (True, "Connected"),
        INVALID: (False, "This key isn't valid"),
        NO_CREDIT: (False, "no credit"),
        NO_MODEL: (False, "doesn't have access to Claude Haiku"),
    }
    for key, (ok, words) in expected.items():
        response = client.post(
            "/panel/settings/usage/test",
            data={"csrf": _csrf(client), "provider": "anthropic", "model": "claude-haiku-5-5",
                  "key": key},
            headers={"Accept": "application/json"},
        )  # fmt: skip
        data = response.json()
        assert data["ok"] is ok and words in data["message"], (key, data)
        assert key not in response.text
    assert tenant_llm.own_provider(conn, panel) is None  # probar no guarda
    # La prueba pasa por LLMClient: el uso queda registrado (la de la key válida).
    with tenant_scope(conn, panel):
        used = conn.execute("select model from usage").fetchall()
    assert used == [("byok/anthropic/claude-haiku-5-5",)]


def test_quitar_la_key_vuelve_a_la_plataforma(conn: psycopg.Connection, panel: uuid.UUID) -> None:
    client = _login()
    _save(client, VALID)
    assert spending.budget(conn, panel).own_key is True
    response = client.post(
        "/panel/settings/usage/remove", data={"csrf": _csrf(client)}, follow_redirects=True
    )
    assert response.status_code == 200
    assert tenant_llm.own_provider(conn, panel) is None
    limits = spending.budget(conn, panel)
    assert limits.own_key is False and limits.cap_usd is not None  # los límites de la plataforma
    assert "Included in your plan" in _page(client, "/panel/settings/spending")
    assert [k for k, _ in _audit(conn, panel)] == ["llm_key_saved", "llm_key_removed"]


def test_el_agente_usa_el_modelo_elegido(conn: psycopg.Connection) -> None:
    company = create_company(conn, "Tienda Norte", NORTE, NORTE_PASSWORD)
    tenant = company.tenant_id
    shared = AnthropicProvider()
    assert tenant_llm.provider_for(conn, tenant, lambda: shared) is shared
    tenant_llm.save_key(conn, tenant, "anthropic", VALID, "claude-haiku-5-5")
    llm = LLMClient(
        provider=tenant_llm.provider_for(conn, tenant, lambda: shared),
        recorder=PostgresUsageRecorder(conn),
    )
    # El agente pide su modelo de siempre; con la key propia responde el elegido.
    llm.complete(tenant_id=tenant, model="claude-sonnet-5-5", messages=[], max_tokens=10)
    assert _Fake.calls == ["claude-haiku-5-5"]
    with tenant_scope(conn, tenant):
        row = conn.execute("select model, cost_usd from usage").fetchone()
    assert row is not None and row[0] == "byok/anthropic/claude-haiku-5-5"
    expected = pricing.cost_usd("claude-haiku-5-5", TokenUsage(8, 2))
    assert Decimal(row[1]) == expected
    # Cambiar de modelo con la key guardada (probada antes).
    assert tenant_llm.set_model(conn, tenant, "claude-sonnet-5-5").ok
    tenant_llm.provider_for(conn, tenant, lambda: shared).create(
        model="claude-sonnet-5-5", messages=[], max_tokens=10
    )
    assert _Fake.calls[-1] == "claude-sonnet-5-5"


def test_un_no_administrador_no_ve_ni_cambia_la_key(
    conn: psycopg.Connection, panel: uuid.UUID
) -> None:
    admin = _login()
    _save(admin, VALID)
    team = _login(TEAM, TEAM_PASSWORD)
    for path in ("/panel/settings/usage", "/panel/settings/spending"):
        response = team.get(path)
        assert response.status_code == 403 and "a1b2" not in response.text
    csrf = _csrf(team, "/panel/home")
    for path, data in (
        ("/panel/settings/usage/key", {"provider": "anthropic", "model": "x", "key": VALID}),
        ("/panel/settings/usage/remove", {}),
        ("/panel/settings/usage/cap", {"cap": "1"}),
    ):
        response = team.post(path, data={"csrf": csrf, **data})
        assert response.status_code == 403
    assert tenant_llm.own_provider(conn, panel) == "anthropic"


# -- 2. el gasto ----------------------------------------------------------------------------------
def _usage(
    conn: psycopg.Connection,
    tenant: uuid.UUID,
    model: str,
    cost: str,
    *,
    case: uuid.UUID | None = None,
    when: datetime | None = None,
    tokens: tuple[int, int] = (100, 20),
) -> None:
    with tenant_scope(conn, tenant):
        conn.execute(
            "insert into usage (tenant_id, case_id, kind, model, input_tokens, output_tokens,"
            " cost_usd, created_at) values (%s, %s, 'llm', %s, %s, %s, %s, %s)",
            (tenant, case, model, *tokens, Decimal(cost), when or datetime.now(UTC)),
        )


def _case(conn: psycopg.Connection, tenant: uuid.UUID, resolved: bool) -> uuid.UUID:
    with tenant_scope(conn, tenant):
        contact = conn.execute(
            "insert into contacts (tenant_id, phone_hash) values (%s, %s) returning id",
            (tenant, uuid.uuid4().hex),
        ).fetchone()
        assert contact is not None
        row = conn.execute(
            "insert into cases (tenant_id, contact_id, status, resolved_by, closed_at)"
            " values (%s, %s, %s, %s, %s) returning id",
            (
                tenant,
                contact[0],
                "resolved" if resolved else "open",
                "agent" if resolved else None,
                datetime.now(UTC) if resolved else None,
            ),
        ).fetchone()
    assert row is not None
    return row[0]


def test_los_totales_coinciden_con_usage_y_el_csv(
    conn: psycopg.Connection, panel: uuid.UUID
) -> None:
    norte = panel
    sur = create_company(conn, "Tienda Sur", SUR, SUR_PASSWORD).tenant_id
    cheap, pricey = _case(conn, norte, resolved=True), _case(conn, norte, resolved=False)
    _usage(conn, norte, "claude-sonnet-5-5", "0.10", case=cheap)
    _usage(conn, norte, "claude-sonnet-5-5", "0.90", case=pricey, tokens=(3000, 400))
    _usage(conn, norte, "claude-haiku-5-5", "0.02", case=pricey)
    _usage(conn, norte, "modelo-misterioso", "0", case=cheap)  # sin precio en la tabla
    _usage(conn, norte, "claude-sonnet-5-5", "5.00", when=datetime.now(UTC) - timedelta(days=62))
    _usage(conn, sur, "claude-sonnet-5-5", "7.77")  # otra empresa: nunca aparece

    today = datetime.now(UTC).date()
    report = spending.month_report(conn, norte, today.year, today.month, today=today)
    with tenant_scope(conn, norte):
        row = conn.execute(
            "select sum(cost_usd), sum(input_tokens), sum(output_tokens) from usage"
            " where model <> 'modelo-misterioso' and created_at > now() - interval '20 days'"
        ).fetchone()
    assert row is not None
    assert report.total_usd == Decimal(row[0]) == Decimal("1.02")
    assert (
        report.input_tokens == 100 + 3000 + 100 + 100 and report.output_tokens == 20 + 400 + 20 + 20
    )
    assert report.unknown_calls == 1
    assert [c.case_id for c in report.top_cases][:2] == [pricey, cheap]
    assert report.per_conversation == Decimal("0.51")  # 1,02 entre 2 conversaciones
    assert report.per_resolved == Decimal("1.02")  # 1,02 entre 1 caso resuelto
    assert sum(d.usd for d in report.days) == report.total_usd

    client = _login()
    page = _page(client, "/panel/settings/spending")
    for expected in (
        "USD 1.02",
        "price unknown",
        "estimate",
        f"/panel/casos/{pricey}",
        "Included in your plan",
        "Spent this month",
        "Projected",
    ):
        assert expected in page, expected
    assert "7.77" not in page  # la otra empresa

    exported = client.get("/panel/settings/spending.csv")
    assert exported.status_code == 200 and "text/csv" in exported.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(exported.text)))
    assert len(rows) == 4  # las del mes de Norte
    assert {r["model"] for r in rows} == {
        "claude-sonnet-5-5",
        "claude-haiku-5-5",
        "modelo-misterioso",
    }
    assert next(r for r in rows if r["model"] == "modelo-misterioso")["cost_usd"] == "price unknown"
    assert sum(Decimal(r["cost_usd"]) for r in rows if r["cost_usd"] != "price unknown") == Decimal(
        "1.02"
    )
    assert "7.77" not in exported.text


def test_dice_de_quien_es_el_gasto_y_el_tope_con_key_propia(
    conn: psycopg.Connection, panel: uuid.UUID
) -> None:
    client = _login()
    _save(client, VALID)
    response = client.post(
        "/panel/settings/usage/cap",
        data={"csrf": _csrf(client), "cap": "10"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert spending.budget(conn, panel).cap_usd == Decimal(10)
    _usage(conn, panel, "byok/anthropic/claude-haiku-5-5", "8.50")
    page = _page(client, "/panel/settings/spending")
    assert "Billed to your Anthropic account" in page
    assert "85%" in page and "warn" in page  # el aviso al 80 %
    assert spending.ratio(spending.month_spend(conn, panel), spending.budget(conn, panel)) >= 0.8
    assert not spending.over(conn, panel)
    _usage(conn, panel, "byok/anthropic/claude-haiku-5-5", "1.50")
    assert spending.over(conn, panel)  # al 100 %: el agente deriva sin llamar al modelo
    assert "llm_cap_changed" in [k for k, _ in _audit(conn, panel)]


@pytest.mark.parametrize("own", [True, False], ids=["key-propia", "plataforma"])
def test_aviso_al_80_y_derivacion_al_100(conn: psycopg.Connection, own: bool) -> None:
    stack = build_simulated_stack(conn)
    tenant = stack.seed.tenant_id
    if own:
        tenant_llm.save_key(conn, tenant, "anthropic", VALID, "claude-haiku-5-5")
    spending.set_budget(conn, tenant, Decimal("1"))
    assert spending.budget(conn, tenant).own_key is own
    _usage(conn, tenant, "claude-haiku-5-5", "0.85")
    alerts = spending.check_turn(conn, tenant, turn_cost=Decimal("0.85"), turn_calls=1)
    assert [a.split()[1] for a in alerts] == ["gasto_80"]
    _usage(conn, tenant, "claude-haiku-5-5", "0.20")
    with tenant_scope(conn, tenant):
        before = conn.execute("select count(*) from usage").fetchone()
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    with tenant_scope(conn, tenant):
        after = conn.execute("select count(*) from usage").fetchone()
    assert result.action == "derivar" and before == after  # sin llamar al modelo


def test_aislamiento_entre_empresas(conn: psycopg.Connection, panel: uuid.UUID) -> None:
    sur = create_company(conn, "Tienda Sur", SUR, SUR_PASSWORD).tenant_id
    tenant_llm.save_key(conn, sur, "anthropic", "sk-ant-de-sur-zz99", "claude-sonnet-5-5")
    _usage(conn, sur, "byok/anthropic/claude-sonnet-5-5", "3.33")
    client = _login()  # Norte
    usage = _page(client, "/panel/settings/usage")
    spend = _page(client, "/panel/settings/spending")
    assert "zz99" not in usage and "3.33" not in spend
    assert tenant_llm.own_provider(conn, panel) is None
    today = datetime.now(UTC).date()
    assert spending.month_report(conn, panel, today.year, today.month).total_usd == 0


# -- 3. los precios --------------------------------------------------------------------------------
def test_tabla_de_precios_con_fecha_y_fuente() -> None:
    for row in pricing.TABLE:
        assert row.source and row.checked, row.model
    # Todos los modelos de la lista y los livianos tienen precio, también como key propia.
    for provider, models in tenant_llm.CATALOG.items():
        for model in [*(m.id for m in models), tenant_llm.LIGHT[provider]]:
            assert model in pricing.PRICES
            assert tenant_llm.billed_model(provider, model) in pricing.PRICES
    # Sonnet 5.5: la lectura de caché es 0,10 (estaba mal en 0,20).
    assert pricing.PRICES["claude-sonnet-5-5"].cache_read == Decimal("0.10")
    # Gemini 3.8 Flash sube el 1/1/2027: el costo usa la tarifa del día.
    usage = TokenUsage(1_000_000, 0)
    model = "byok/gemini/gemini-3.8-flash"
    assert pricing.cost_usd(model, usage, on=date(2026, 12, 31)) == Decimal("0.75")
    assert pricing.cost_usd(model, usage, on=date(2027, 1, 1)) == Decimal("1.5")
    assert not pricing.known("modelo-misterioso")


def test_un_proveedor_ocupado_no_parece_una_key_mala() -> None:
    """Visto en el QA con Gemini real: un 503 se mostraba como "rechazó la llamada"."""
    request = httpx.Request("POST", "https://api.example/v1")
    busy = httpx.HTTPStatusError(
        "503", request=request, response=httpx.Response(503, text="overloaded", request=request)
    )
    found = tenant_llm.explain(busy, "gemini", "gemini-3.8-flash")
    assert found.code == "busy" and "again in a minute" in found.message
    assert tenant_llm.explain(httpx.ConnectError("x"), "gemini", "gemini-3.8-flash").code == (
        "unreachable"
    )
