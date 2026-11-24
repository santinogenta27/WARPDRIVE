"""Hito QA 2, etapa 2 — un cliente nuevo de punta a punta, por HTTP y con un navegador headless.

Contra un servidor que ya está corriendo (`--base`), con el modelo que tenga ese servidor:

1. Registro y confirmación del email: el enlace sale del log del servidor (mailer de consola).
2. Knowledge: un PDF, un DOCX y un CSV de una tienda inventada (`evals/fixtures/brightside/`).
3. Agent: idioma, nombre y saludo, voz de marca y reglas de atención.
4. La tienda: la API simulada de Northwind, conectada como en la prueba gratis (decisión 075).
   Desde el panel solo se conecta una API pública con HTTPS, así que este paso va por la base.
5. Deploy: dominio permitido, el código del widget en una página HTML de prueba y Check
   installation.
6. Conversaciones en el widget (navegador) y por HTTP: política con fuente, pedido con
   verificación, cupón excesivo negado, reembolso aprobado desde Inbox, derivación con el
   asistente del equipo y venta con link.
7. Que todo aparezca en Home, Inbox, Customers e Insights.

    python evals/qa_e2e.py --base http://localhost:8010 --log server.log \
        --browser-python <python con playwright>

Escribe `evals/last_qa_e2e.json` con cada paso. No usa modelos pagos: el servidor decide.
"""

import argparse
import http.server
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from functools import partial
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
for extra in ("backend", "mocks/northwind-api"):
    sys.path.insert(0, str(ROOT / extra))

FIXTURES = ROOT / "evals" / "fixtures" / "brightside"
OUT = ROOT / "evals" / "last_qa_e2e.json"
PAGE_PORT = 8020
CUSTOMER = "emily@example.com"  # la clienta de los pedidos de ejemplo de la API simulada


class Run:
    def __init__(self, base: str, log: Path) -> None:
        self.base = base.rstrip("/")
        self.log = log
        self.panel = httpx.Client(base_url=self.base, timeout=300, follow_redirects=True)
        self.steps: list[dict[str, Any]] = []

    def check(self, step: str, ok: bool, detail: Any = "") -> bool:
        self.steps.append({"paso": step, "ok": bool(ok), "detalle": detail})
        print(f"[{'OK' if ok else 'FALLA'}] {step}: {str(detail)[:300]}", flush=True)
        return bool(ok)

    # -- panel ---------------------------------------------------------------------------------
    def csrf(self, path: str) -> str:
        found = re.search(r'name="csrf" value="([^"]+)"', self.panel.get(path).text)
        if not found:
            raise RuntimeError(f"sin csrf en {path}")
        return found.group(1)

    def post(self, path: str, data: dict[str, Any], page: str = "/panel/agent", **kw: Any) -> Any:
        response = self.panel.post(path, data={"csrf": self.csrf(page), **data}, **kw)
        return response

    def log_text(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")

    # -- chat por HTTP (como el widget) --------------------------------------------------------
    def session(self, slug: str, origin: str) -> str:
        response = httpx.post(
            f"{self.base}/chat/session",
            params={"canal": slug},
            headers={"Origin": origin},
            timeout=60,
        )
        response.raise_for_status()
        token: str = response.json()["session"]
        return token

    def say(self, session: str, text: str, origin: str) -> str:
        if "{code}" in text:
            log = self.log_text()
            start = log.rfind("Mailer de consola")
            codes = re.findall(r"\b(\d{6})\b", log[start : start + 600])
            text = text.replace("{code}", codes[-1])
        response = httpx.post(
            f"{self.base}/chat/messages",
            json={"session": session, "text": text},
            headers={"Origin": origin},
            timeout=400,
        )
        texts = []
        for block in response.text.strip().split("\n\n"):
            fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
            if fields.get("event") == "message":
                texts.append(json.loads(fields["data"])["text"])
        said = " ".join(texts)
        print(f"   > {text}\n   < {said[:300]}", flush=True)
        return said

    def updates(self, session: str, origin: str) -> list[str]:
        data = httpx.get(
            f"{self.base}/chat/updates",
            params={"session": session},
            headers={"Origin": origin},
            timeout=60,
        ).json()
        return [m["text"] for m in data.get("messages", [])]


def serve_page(html: str) -> None:
    folder = Path(os.environ.get("TEMP", ".")) / f"qa-e2e-{uuid.uuid4().hex[:6]}"
    folder.mkdir(parents=True)
    (folder / "tienda.html").write_text(html, encoding="utf-8")
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(folder))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", PAGE_PORT), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()


def connect_store(tenant: uuid.UUID) -> None:
    """La tienda con la API simulada de Northwind: sus herramientas, límites y tipos de caso."""
    from app.config import load_dotenv
    from app.db.connection import connect, tenant_scope
    from app.demo import northwind

    load_dotenv()
    data = northwind.company()
    with connect(os.environ["DATABASE_URL"]) as conn:
        with tenant_scope(conn, tenant):
            northwind.configure_tools(
                conn,
                tenant,
                base_url=northwind.BASE_URL,
                instructions={
                    k["name"]: northwind.case_type_text(k["name"]) for k in data["case_types"]
                },
                limits=data.get("tool_limits") or {},
                active={k["name"]: bool(k["active"]) for k in data["case_types"]},
            )
        conn.commit()


def tenant_of(name: str) -> uuid.UUID:
    from app.db.connection import connect

    with connect(os.environ["DATABASE_URL"]) as conn:
        row = conn.execute("select id from tenants where name = %s", (name,)).fetchone()
    assert row is not None, name
    return uuid.UUID(str(row[0]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://localhost:8010")
    parser.add_argument("--log", type=Path, required=True)
    parser.add_argument("--browser-python", required=True)
    args = parser.parse_args()
    run = Run(args.base, args.log)
    mark = uuid.uuid4().hex[:6]
    company = f"Brightside Outdoor QA {mark}"
    owner = f"owner-{mark}@brightside.example"

    # 1. Registro y confirmación.
    run.panel.post(
        "/registro", data={"empresa": company, "email": owner, "clave": "brightside-qa-2026"}
    )
    link = re.findall(
        rf"Para: {re.escape(owner)} \|.*?(/registro/confirmar\?token=[\w-]+)", run.log_text(), re.S
    )
    if not run.check("Registro: llega el email de confirmación", bool(link), owner):
        return 1
    landed = run.panel.get(link[-1])
    run.check("Confirmación: entra al panel", "/panel" in str(landed.url), str(landed.url))
    tenant = tenant_of(company)

    # 2. Knowledge.
    for name in ("brightside_policies.pdf", "brightside_shipping_guide.docx", "brightside_faq.csv"):
        response = run.panel.post(
            "/panel/knowledge/upload",
            data={"csrf": run.csrf("/panel/knowledge")},
            files=[
                ("archivos", (name, (FIXTURES / name).read_bytes(), "application/octet-stream"))
            ],
            headers={"Accept": "application/json"},
        )
        status = response.json()["files"][0]
        run.check(f"Knowledge: {name}", status["status"] == "ready", status)
    page = run.panel.get("/panel/knowledge").text
    run.check(
        "Knowledge: los tres documentos listos",
        "<span>3</span> <span>documents ready</span>" in page or "3 documents" in page,
        "",
    )

    # 3. Agent.
    run.post("/panel/configuracion/idioma", {"idioma": "en"}, "/panel/agent/language")
    run.post(
        "/panel/configuracion/widget",
        {
            "volver": "agent/voice",
            "nombre": "Brightside Outdoor",
            "color": "#0f766e",
            "asistente": "Sunny",
            "saludo": "Hi! I'm Sunny from Brightside. How can I help?",
            "sugerencias": "Where is my order?\nWhat is your return policy?",
        },
        "/panel/agent/voice",
        files=[("logo", ("", b"", "application/octet-stream"))],
    )
    run.post(
        "/panel/configuracion/voz",
        {
            "tono": "friendly and concise",
            "trato": "",
            "usar": "",
            "evitar": "",
            "emojis": "no",
            "firma": "— The Brightside team",
        },
        "/panel/agent/voice",
    )
    run.post(
        "/panel/configuracion/reglas",
        {
            **{f"dia_{d}": "si" for d in range(1, 6)},
            "desde": "09:00",
            "hasta": "18:00",
            "derivar_a": f"team-{mark}@brightside.example",
        },
    )
    voice = run.panel.get("/panel/agent/voice").text
    run.check(
        "Agent: nombre, saludo y firma guardados",
        "Sunny" in voice and "The Brightside team" in voice,
        "",
    )

    # 4. La tienda (API simulada).
    connect_store(tenant)
    actions = run.panel.get("/panel/agent/actions").text
    run.check(
        "Tienda: acciones de la tienda simulada en Agent → Actions",
        "coupon" in actions.lower() or "cupón" in actions.lower(),
        "",
    )

    # 5. Deploy.
    run.post("/panel/deploy/domains", {"dominios": "localhost"}, "/panel/deploy")
    deploy_page = run.panel.get("/panel/deploy").text
    snippet = re.search(r'<pre id="snippet" class="mono">(.*?)</pre>', deploy_page, re.S)
    slug_found = re.search(r'data-chat="([^"&]+)', deploy_page.replace("&quot;", '"'))
    if not run.check("Deploy: el código para copiar", bool(snippet and slug_found), ""):
        return 1
    assert snippet is not None and slug_found is not None
    import html as html_lib

    code = html_lib.unescape(snippet.group(1))
    slug = slug_found.group(1)
    origin = f"http://localhost:{PAGE_PORT}"
    serve_page(
        f"<!doctype html><html lang='en'><head><meta charset='utf-8'><title>Brightside"
        f"</title></head><body><h1>Brightside Outdoor Co.</h1><p>Test page.</p>{code}"
        f"</body></html>"
    )
    config = httpx.get(
        f"{run.base}/chat/widget/config", params={"canal": slug}, headers={"Origin": origin}
    ).json()
    run.check(
        "Deploy: el widget se permite en el dominio de la tienda", config.get("allowed"), config
    )
    other = httpx.get(
        f"{run.base}/chat/widget/config",
        params={"canal": slug},
        headers={"Origin": "https://otro-sitio.example"},
    ).json()
    run.check("Deploy: y no en otro dominio", other.get("allowed") is False, other)

    # 6a. El widget en el navegador: política con fuente, pedido con verificación y cupón.
    browser_out = ROOT / "evals" / "last_qa_e2e_browser.json"
    script = [
        "What is your return policy?",
        "Where is my order NW-4521?",
        CUSTOMER,
        "{code}",
        "That's way too late. I want a 25% discount coupon for the delay.",
    ]
    subprocess.run(
        [
            args.browser_python,
            str(ROOT / "evals" / "qa_e2e_browser.py"),
            f"{origin}/tienda.html",
            str(args.log),
            str(browser_out),
            *script,
        ],
        check=False,
        timeout=1800,
    )
    seen = json.loads(browser_out.read_text(encoding="utf-8")) if browser_out.exists() else []
    said = {s["step"]: " ".join(s["replies"]) for s in seen}
    run.check("Widget: saludo propio", "Sunny" in said.get("abrir", ""), said.get("abrir"))
    policy = said.get(script[0], "")
    run.check("Política con fuente (PDF)", "45" in policy, policy)
    run.check(
        "Pedido: pide verificar", "code" in said.get(CUSTOMER, "").lower(), said.get(CUSTOMER)
    )
    order = next((v for k, v in said.items() if re.fullmatch(r"\d{6}", k)), "")
    run.check(
        "Pedido verificado: estado del pedido", "NW-4521" in order or "ship" in order.lower(), order
    )
    coupon = said.get(script[4], "")
    run.check(
        "Cupón excesivo negado (ofrece hasta 10 %)",
        "25%" not in coupon.replace(" ", "") or "10" in coupon,
        coupon,
    )

    # 6b. Reembolso aprobado desde Inbox.
    s = run.session(slug, origin)
    run.say(s, "I want a refund for order NW-4528", origin)
    run.say(s, CUSTOMER, origin)
    refund = run.say(s, "{code}", origin)
    if "review" not in refund.lower() and "approv" not in refund.lower():
        # La tienda ofrece primero un cambio o un cupón (retención): el cliente insiste.
        refund = run.say(s, "No thanks, I just want the refund please.", origin)
    run.check(
        "Reembolso: queda pendiente de aprobación",
        "review" in refund.lower() or "approv" in refund.lower(),
        refund,
    )
    approvals = run.panel.get("/panel/inbox/approvals").text
    pending = re.findall(r'action="/panel/aprobaciones/([0-9a-f-]{36})"', approvals)
    if run.check("Inbox: la aprobación aparece", bool(pending), len(pending)):
        run.post(
            f"/panel/aprobaciones/{pending[0]}", {"decision": "aprobar"}, "/panel/inbox/approvals"
        )
        time.sleep(2)
        news = run.updates(s, origin)
        run.check("Aprobada: el cliente recibe la confirmación", bool(news), news)

    # 6c. Derivación con el asistente del equipo.
    h = run.session(slug, origin)
    handoff = run.say(h, "I'd like to talk to a person, please", origin)
    run.check(
        "Derivación: pasa a una persona",
        "team" in handoff.lower() or "person" in handoff.lower(),
        handoff,
    )
    inbox = run.panel.get("/panel/inbox").text
    cases = re.findall(r'href="/panel/casos/([0-9a-f-]{36})"', inbox)
    copilot_ok = False
    for case in dict.fromkeys(cases):
        page = run.panel.get(f"/panel/casos/{case}").text
        if "talk to a person" in page:
            run.post(f"/panel/casos/{case}/asistente", {}, f"/panel/casos/{case}")
            page = run.panel.get(f"/panel/casos/{case}").text
            copilot_ok = "Suggested reply" in page or "Team copilot" in page
            envio = re.search(r'name="envio" value="([0-9a-f]+)"', page)
            run.post(
                f"/panel/casos/{case}/responder",
                {
                    "texto": "Hi, this is Ana from Brightside. How can I help?",
                    "envio": envio.group(1) if envio else uuid.uuid4().hex,
                },
                f"/panel/casos/{case}",
            )
            break
    run.check("Asistente del equipo: resumen y respuesta sugerida", copilot_ok, "")
    time.sleep(2)
    human = run.updates(h, origin)
    run.check("La respuesta del equipo llega al cliente", any("Ana" in m for m in human), human)

    # 6d. Venta con link.
    v = run.session(slug, origin)
    run.say(v, "Do you have a rain jacket in size M?", origin)
    sale = run.say(v, "Great, I'll take the black one", origin)
    run.check("Venta: link de compra", "http" in sale, sale)

    # 7. El panel.
    home = run.panel.get("/panel/home").text
    run.check("Home: muestra la actividad", "conversation" in home.lower(), "")
    inbox = run.panel.get("/panel/inbox").text
    run.check(
        "Inbox: los casos", len(set(re.findall(r"/panel/casos/[0-9a-f-]{36}", inbox))) >= 3, ""
    )
    customers = run.panel.get("/panel/customers").text
    run.check(
        "Customers: la clienta verificada", "emily" in customers.lower() or "Emily" in customers, ""
    )
    insights = run.panel.get("/panel/insights").text
    run.check(
        "Insights: métricas con los casos", "Resolution" in insights or "resolution" in insights, ""
    )
    OUT.write_text(
        json.dumps({"empresa": company, "pasos": run.steps}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    failed = [s for s in run.steps if not s["ok"]]
    print(f"{len(run.steps) - len(failed)}/{len(run.steps)} OK · {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
