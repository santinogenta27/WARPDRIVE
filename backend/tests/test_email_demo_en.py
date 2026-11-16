"""Demo en inglés: el código de verificación llega a un email real (decisión 043).

- Con `NORTHWIND_DEMO_CUSTOMER_EMAIL`, los pedidos del guion (NW-4521 y NW-4528, de la misma
  clienta) también responden a ese email en la API simulada que comparten el chat y el panel: al
  arrancar y después de "Reset demo". El email de los fixtures (`emily@example.com`) sigue
  funcionando.
- Sin la variable, todo queda como antes.
- El email del código: asunto "Your Northwind Outfitters verification code", el código grande y
  la vigencia de 10 minutos, en HTML y en texto.
- Con `RESEND_API_KEY` y `MAIL_FROM` se envía por Resend; sin ellas, por el mailer de consola.

El email de estos tests es inventado: el real va solo en las variables de Render.
"""

import json
import re
from collections.abc import Iterator

import httpx
import psycopg
import pytest
from simulated.stack import RecordingMailer, SimulatedStack, build_simulated_stack

from app.agent.loop import Agent
from app.demo import northwind
from app.identity import email_code
from app.mailer import ConsoleMailer, Email, ResendMailer
from app.policy.guardian import Guardian
from app.tools.runtime import ToolRuntime

DEMO_EMAIL = "video.demo@correo-del-cliente.com"  # inventado, no es de nadie
SCRIPT_ORDERS = ("NW-4521", "NW-4528")


@pytest.fixture(autouse=True)
def fresh_sandbox() -> Iterator[None]:
    """Cada test arma la API simulada compartida desde cero (como al arrancar la API)."""
    northwind.shared_northwind.cache_clear()
    yield
    northwind.shared_northwind.cache_clear()


def _phone_for(email: str) -> str | None:
    found = northwind.shared_northwind().get("/customers", params={"email": email}).json()
    phones = [c["phone"] for c in found["customers"]]
    return phones[0] if len(phones) == 1 else None


def _owner(order_id: str) -> str:
    order: dict[str, str] = northwind.shared_northwind().get(f"/orders/{order_id}").json()
    return order["customer_phone"]


def test_sin_la_variable_queda_el_email_de_los_fixtures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(northwind.DEMO_EMAIL_ENV, raising=False)
    assert _phone_for("emily@example.com") == _owner("NW-4521")
    assert _phone_for(DEMO_EMAIL) is None


def test_con_la_variable_los_pedidos_del_guion_responden_a_ese_email(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(northwind.DEMO_EMAIL_ENV, f"  {DEMO_EMAIL.upper()} ")
    for order in SCRIPT_ORDERS:
        assert _phone_for(DEMO_EMAIL) == _owner(order)
    assert _phone_for("emily@example.com") == _owner("NW-4521")  # el de los fixtures sigue

    # "Reset demo" del panel vuelve a armar la API simulada: el email sigue asociado.
    northwind.shared_northwind.cache_clear()
    assert _phone_for(DEMO_EMAIL) == _owner("NW-4521")


def test_un_valor_que_no_es_un_email_se_ignora(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(northwind.DEMO_EMAIL_ENV, "no es un email")
    assert _phone_for("no es un email") is None
    assert _phone_for("emily@example.com") == _owner("NW-4521")


def test_el_email_no_esta_en_el_codigo_ni_en_los_fixtures() -> None:
    """La variable es opcional: ningún archivo del repo trae un email real para la demo."""
    source = northwind.API_DIR / "data" / "customers.json"
    emails = {c["email"] for c in json.loads(source.read_text(encoding="utf-8"))}
    assert all(e.endswith("@example.com") for e in emails)


# -- el guion de punta a punta, con el email de la variable -----------------------------------
@pytest.fixture
def stack(conn: psycopg.Connection) -> SimulatedStack:
    return build_simulated_stack(conn, company="northwind")


def test_el_codigo_llega_al_email_de_la_variable(
    stack: SimulatedStack, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(northwind.DEMO_EMAIL_ENV, DEMO_EMAIL)
    mailer = RecordingMailer()
    # El agente de la demo: la API simulada compartida (la del chat y el panel).
    agent = Agent(
        stack.conn,
        stack.llm,
        stack.embedder,
        Guardian(stack.conn, ToolRuntime(northwind.shared_northwind())),
        mailer=mailer,
    )
    contact = stack.add_contact("video")
    tenant = stack.seed.tenant_id

    first = agent.handle_message(tenant, contact, "Where is my order NW-4521?")
    assert first.verification_requested
    agent.handle_message(tenant, contact, DEMO_EMAIL)
    assert len(mailer.sent) == 1
    sent = mailer.sent[0]
    assert sent.to == DEMO_EMAIL
    assert sent.subject == "Your Northwind Outfitters verification code"
    code = re.search(r"\b\d{6}\b", sent.body)
    assert code is not None and sent.html is not None and code.group(0) in sent.html

    answer = agent.handle_message(tenant, contact, code.group(0))
    assert any(t.name == "consultar_pedido" and t.status == "executed" for t in answer.tool_calls)


# -- el email del código ----------------------------------------------------------------------
def test_el_email_del_codigo_se_ve_prolijo() -> None:
    email = email_code.code_email(
        to=DEMO_EMAIL, company="Northwind Outfitters", code="482913", language="en"
    )
    assert email.subject == "Your Northwind Outfitters verification code"
    assert "482913" in email.body and "10 minutes" in email.body
    html = email.html or ""
    # El código, solo y grande (32 px, monoespaciado y espaciado), y la vigencia.
    big = re.search(r'<td[^>]*style="[^"]*font-size:\s*(\d+)px[^"]*"[^>]*>\s*482913\s*</td>', html)
    assert big is not None and int(big.group(1)) >= 28
    assert "10 minutes" in html and "Northwind Outfitters" in html
    assert "<script" not in html.lower()


def test_el_nombre_de_la_empresa_se_escapa_en_el_html() -> None:
    email = email_code.code_email(
        to=DEMO_EMAIL, company="Acme <b>& Co</b>", code="111222", language="es"
    )
    assert email.subject == "Tu código de verificación de Acme <b>& Co</b>"
    assert "Acme &lt;b&gt;&amp; Co&lt;/b&gt;" in (email.html or "")
    assert "10 minutos" in (email.html or "")


def test_resend_manda_el_html_y_el_texto() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": "abc"})

    mailer = ResendMailer(
        "re_test_123",
        "Northwind <codes@demo.test>",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    mailer.send(Email(to=DEMO_EMAIL, subject="s", body="Code 123456", html="<p>123456</p>"))
    payload = json.loads(requests[0].content)
    assert payload["text"] == "Code 123456" and payload["html"] == "<p>123456</p>"


def test_el_agente_de_la_demo_elige_el_mailer_segun_las_variables(
    conn: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = northwind.sandbox_client()
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("MAIL_FROM", raising=False)
    assert isinstance(northwind.build_agent(conn, client)._mailer, ConsoleMailer)

    monkeypatch.setenv("RESEND_API_KEY", "re_test_123")
    monkeypatch.setenv("MAIL_FROM", "Northwind Outfitters <codes@demo.test>")
    assert isinstance(northwind.build_agent(conn, client)._mailer, ResendMailer)
