"""Recorrido del panel como un cliente nuevo, en un navegador headless (Hito 13, tarea 8).

Levanta la API en un hilo contra DATABASE_URL_STAGING, dentro de una transacción que se revierte
(no deja datos), y recorre con Chrome, en computadora y en celular:

1. registro y confirmación del email (el link se toma del email, sin mandarlo);
2. Home de una empresa nueva;
3. Knowledge: sube un PDF, un DOCX y un CSV arrastrables, mira qué aprendió y le pregunta;
4. Deploy, Agent (reglas y marca y voz) y Test & Improve;
5. con una empresa con conversaciones: Home con pendientes, Inbox, Approvals e Insights.

Guarda las capturas en docs/img/panel/despues/. No llama a ningún modelo.

Necesita Playwright, que no es una dependencia del proyecto (es una herramienta para mirar el
panel, no la usa la app): `pip install playwright` en un entorno aparte y el Chrome del sistema
(CHROME_PATH, por defecto el de Windows).

Uso: python evals/panel_tour.py [carpeta]
"""

import os
import re
import sys
import threading
import time
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
for _path in (
    REPO / "backend",
    HERE,
    REPO / "backend" / "tests",
    REPO / "mocks" / "acme-api",
    REPO / "mocks" / "northwind-api",
):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from app.config import load_dotenv  # noqa: E402

CHROME = os.environ.get("CHROME_PATH", r"C:\Program Files\Google\Chrome\Application\chrome.exe")
VIEWS = {"escritorio": (1366, 900), "celular": (390, 844)}
KNOWLEDGE = HERE / "fixtures" / "knowledge"
PORT = 8767


def main() -> int:
    load_dotenv()
    os.environ["DATABASE_URL"] = os.environ.get("DATABASE_URL_STAGING") or os.environ.get(
        "DATABASE_URL", ""
    )
    os.environ["ONBOARDING_WORKER"] = "off"
    for key in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "VOYAGE_API_KEY"):
        os.environ.pop(key, None)  # sin modelos: el recorrido es de la interfaz
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Falta Playwright: pip install playwright (en un entorno aparte).")
        return 2
    import psycopg
    import uvicorn
    from simulated.stack import build_simulated_stack
    from test_panel_ingles import _harbor_data

    from app.channels.web.routes import WebDeps, web_deps
    from app.companies.create import create_company
    from app.db.connection import connect
    from app.db.migrate import migrate
    from app.mailer import Email
    from app.main import app
    from app.panel import signup_routes
    from app.panel.routes import PanelDeps, panel_deps

    out = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "docs" / "img" / "panel" / "despues"
    out.mkdir(parents=True, exist_ok=True)
    migrate(os.environ["DATABASE_URL"])
    conn = connect(os.environ["DATABASE_URL"])
    stack = build_simulated_stack(conn)
    harbor = create_company(conn, "Harbor Supply", "maya@harborsupply.example", "clave-de-harbor")
    _harbor_data(conn, stack, harbor.tenant_id)
    lock = threading.Lock()
    inbox: list[Email] = []

    class Capture:
        def send(self, email: Email) -> None:
            inbox.append(email)

    class Locked(AbstractContextManager[psycopg.Connection]):
        def __enter__(self) -> psycopg.Connection:
            lock.acquire()
            return conn

        def __exit__(self, *exc: Any) -> None:
            lock.release()

    def capture() -> Any:
        return Capture()

    signup_routes.mailer_from_env = capture  # type: ignore[assignment]
    app.dependency_overrides[panel_deps] = lambda: PanelDeps(
        connection=lambda: Locked(),
        tenant_id=stack.seed.tenant_id,
        guardian=lambda c, t: stack.agent._guardian,
        embedder=lambda c: stack.embedder,
        kick_worker=lambda: None,
    )
    app.dependency_overrides[web_deps] = lambda: WebDeps(
        connection=lambda: Locked(), agent=lambda c: stack.agent
    )
    server = uvicorn.Server(uvicorn.Config(app, port=PORT, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    while not server.started:
        time.sleep(0.1)
    base = f"http://127.0.0.1:{PORT}"
    shots = 0
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(executable_path=CHROME)
            for view, (width, height) in VIEWS.items():
                n = 0

                def shot(page: Any, name: str, view: str = view) -> None:
                    nonlocal n, shots
                    n += 1
                    shots += 1
                    page.wait_for_timeout(600)
                    path = out / f"{view}-{n:02d}-{name}.jpg"
                    page.screenshot(path=str(path), full_page=True, type="jpeg", quality=82)

                # -- un cliente nuevo ---------------------------------------------------------
                ctx = browser.new_context(viewport={"width": width, "height": height})
                page = ctx.new_page()
                page.goto(f"{base}/registro")
                shot(page, "registro")
                page.fill("#empresa", f"Brisa Home {view}")
                page.fill("#email", f"duena-{view}@brisahome.example")
                page.fill("#clave", "clave-de-brisa-home")
                page.click("button[type=submit]")
                shot(page, "revisa-tu-email")
                body = next(e for e in inbox if f"duena-{view}" in e.to).body
                found = re.search(r"/registro/confirmar\?token=[\w-]+", body)
                assert found is not None, "el email de confirmación no trae el link"
                page.goto(base + found.group(0))
                page.goto(f"{base}/panel/home")
                shot(page, "home-nueva")
                page.goto(f"{base}/panel/knowledge")
                shot(page, "knowledge-vacio")
                page.set_input_files(
                    "#archivos",
                    [
                        str(KNOWLEDGE / "acme_manual_de_politicas.pdf"),
                        str(KNOWLEDGE / "acme_manual_de_atencion.docx"),
                        str(KNOWLEDGE / "acme_catalogo.csv"),
                    ],
                )
                errors: list[str] = []
                page.on("pageerror", lambda error, seen=errors: seen.append(str(error)))
                try:
                    page.wait_for_function(
                        "document.querySelectorAll("
                        "'#uploads .badge.ready, #uploads .badge.failed').length >= 3",
                        timeout=120_000,
                    )
                except Exception:
                    print("cargas:", page.inner_html("#uploads")[:1500], errors[-5:])
                    raise
                shot(page, "knowledge-subiendo")
                page.wait_for_load_state("load")
                page.wait_for_timeout(2500)
                page.goto(f"{base}/panel/knowledge")
                shot(page, "knowledge-documentos")
                page.fill("#pregunta", "¿Cuánto tiempo tengo para devolver un calzado?")
                page.click("form[action='/panel/knowledge/ask'] button[type=submit]")
                shot(page, "knowledge-pregunta")
                page.click("a[href^='/panel/knowledge/doc/']")
                shot(page, "knowledge-que-aprendio")
                for slug, name in (
                    ("deploy", "deploy"),
                    ("agent", "agent-reglas"),
                    ("agent/voice", "agent-marca-y-voz"),
                    ("test", "test-preparacion"),
                    ("test/playground", "test-playground"),
                    ("settings/data", "settings-datos"),
                ):
                    page.goto(f"{base}/panel/{slug}")
                    shot(page, name)
                ctx.close()
                # -- una empresa con conversaciones --------------------------------------------
                ctx = browser.new_context(viewport={"width": width, "height": height})
                page = ctx.new_page()
                page.goto(f"{base}/panel/login")
                shot(page, "login")
                page.fill("#email", "maya@harborsupply.example")
                page.fill("#password", "clave-de-harbor")
                page.click("button[type=submit]")
                for slug, name in (
                    ("home", "home-con-pendientes"),
                    ("inbox", "inbox"),
                    ("inbox/approvals", "inbox-aprobaciones"),
                    ("insights", "insights"),
                ):
                    page.goto(f"{base}/panel/{slug}")
                    shot(page, name)
                if view == "celular":
                    page.click("#menu-btn")
                    shot(page, "menu")
                ctx.close()
            browser.close()
    finally:
        server.should_exit = True
        conn.rollback()
        conn.close()
    print(f"{shots} capturas en {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
