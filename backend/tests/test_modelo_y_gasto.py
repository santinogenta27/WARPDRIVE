"""Hito 9, tarea 2 — test de aceptación: API key por empresa y tope de gasto (decisión 049).

- Cada empresa puede cargar su propia API key (Anthropic, OpenAI, Groq o Gemini), guardada
  cifrada. Con su key, el agente usa ese proveedor; sin key, usa la de la plataforma con límites.
- Tope de gasto mensual: aviso al 80 %; al 100 %, el mensaje se deriva a una persona sin llamar
  al modelo, con el motivo en la fila Why.
- La página de configuración muestra el gasto del mes y nunca la key.
"""

import uuid
from decimal import Decimal

import psycopg
import pytest
from simulated.stack import SimulatedStack, build_simulated_stack

from app.companies.create import create_company
from app.db.connection import tenant_scope
from app.demo import behind
from app.llm import spending, tenant_llm
from app.llm.client import AnthropicProvider
from app.llm.groq import GroqProvider

NORTE, NORTE_PASSWORD = "ana@tiendanorte.example", "clave-de-tienda-norte"
KEY = "gsk_clave-de-prueba-de-tienda-norte-123"


def _settings(conn: psycopg.Connection, tenant_id: uuid.UUID) -> str:
    with tenant_scope(conn, tenant_id):
        row = conn.execute("select settings::text from tenants where id = %s", (tenant_id,))
        found = row.fetchone()
    assert found is not None
    return str(found[0])


def _spend(conn: psycopg.Connection, tenant_id: uuid.UUID, usd: str, calls: int = 1) -> None:
    with tenant_scope(conn, tenant_id):
        for _ in range(calls):
            conn.execute(
                "insert into usage (tenant_id, kind, model, input_tokens, output_tokens,"
                " cost_usd) values (%s, 'llm', 'claude-sonnet-5-5', 1, 1, %s)",
                (tenant_id, Decimal(usd) / calls),
            )


# -- 1. la key de cada empresa, cifrada -------------------------------------------------------
def test_la_key_se_guarda_cifrada_y_el_agente_usa_ese_proveedor(
    conn: psycopg.Connection,
) -> None:
    company = create_company(conn, "Tienda Norte", NORTE, NORTE_PASSWORD)
    tenant = company.tenant_id
    shared = AnthropicProvider()
    assert tenant_llm.provider_for(conn, tenant, lambda: shared) is shared

    tenant_llm.save_key(conn, tenant, "groq", KEY)
    assert KEY not in _settings(conn, tenant)
    assert tenant_llm.own_provider(conn, tenant) == "groq"
    provider = tenant_llm.provider_for(conn, tenant, lambda: shared)
    # Con el modelo elegido (el recomendado, si no eligió) y el uso a nombre de la key propia.
    assert isinstance(provider, tenant_llm.TenantProvider)
    assert isinstance(provider.inner, GroqProvider)
    assert provider.inner._transport._api_key == KEY
    assert provider.model == tenant_llm.default_model("groq")

    for name in tenant_llm.PROVIDERS:
        assert tenant_llm.make_provider(name, "k-123") is not None

    with pytest.raises(ValueError):
        tenant_llm.save_key(conn, tenant, "otro", KEY)
    tenant_llm.clear_key(conn, tenant)
    assert tenant_llm.own_provider(conn, tenant) is None


# -- 2. el tope de gasto ----------------------------------------------------------------------
def test_sin_key_propia_hay_limites_de_la_plataforma(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SHARED_MONTHLY_USD", "5")
    monkeypatch.setenv("SHARED_MONTHLY_CALLS", "3")
    company = create_company(conn, "Tienda Norte", NORTE, NORTE_PASSWORD)
    tenant = company.tenant_id
    found = spending.budget(conn, tenant)
    assert (found.cap_usd, found.cap_calls, found.own_key) == (Decimal(5), 3, False)
    _spend(conn, tenant, "0", calls=3)  # modelos gratuitos: cuenta la cantidad de llamadas
    assert spending.over(conn, tenant)

    tenant_llm.save_key(conn, tenant, "anthropic", "sk-ant-123")
    assert spending.budget(conn, tenant).cap_calls is None  # con su key paga ella
    assert not spending.over(conn, tenant)
    spending.set_budget(conn, tenant, Decimal("10"))
    assert spending.budget(conn, tenant).cap_usd == Decimal(10)


def test_avisa_al_cruzar_el_80_y_el_100() -> None:
    tenant = uuid.uuid4()
    assert spending.alerts_for(tenant, before=0.5, after=0.79) == []
    assert [a.split()[1] for a in spending.alerts_for(tenant, before=0.79, after=0.85)] == [
        "gasto_80"
    ]
    assert [a.split()[1] for a in spending.alerts_for(tenant, before=0.7, after=1.0)] == [
        "gasto_80",
        "gasto_100",
    ]
    assert spending.alerts_for(tenant, before=0.9, after=0.95) == []


def test_al_100_deriva_sin_llamar_al_modelo(conn: psycopg.Connection) -> None:
    stack: SimulatedStack = build_simulated_stack(conn)
    tenant = stack.seed.tenant_id
    spending.set_budget(conn, tenant, Decimal("0.01"))
    _spend(conn, tenant, "0.02")
    with tenant_scope(conn, tenant):
        before = conn.execute("select count(*) from usage").fetchone()
    result = stack.send("lucia", "¿Dónde está mi pedido 1001?")
    with tenant_scope(conn, tenant):
        after = conn.execute("select count(*) from usage").fetchone()
    assert result.action == "derivar"
    assert before == after  # ninguna llamada al modelo
    from app.audit.log import reconstruct_case

    events = reconstruct_case(conn, tenant, result.case_id)
    cause = next(e.payload["causa"] for e in events if e.kind == "escalated")
    assert cause == {"tipo": "tope_de_gasto"}
    assert "gasto" in (behind.handoff_line(cause, english=False) or "")


# -- 3. el panel ------------------------------------------------------------------------------
# Desde la decisión 102 el panel prueba la key antes de guardarla: lo cubre test_modelo_propio.py.
