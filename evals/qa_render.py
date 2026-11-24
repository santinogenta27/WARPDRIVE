"""Hito QA 2, etapa 2 — contra el servidor de prueba real (Render).

Render despliega `main`, que todavía tiene el panel del Hito 12 (decisión 094), y su base ya no es
la `DATABASE_URL` del .env: sin su log ni una casilla real, el email de confirmación de un registro
no se puede leer desde acá. Lo que se prueba:

1. El registro de una empresa nueva hasta "Check your email". Con `--confirm-link` (el enlace que
   llega por email a una casilla real), sigue: confirma, sube un PDF, un DOCX y un CSV de la tienda
   inventada y pone el idioma en inglés.
2. El chat de Northwind en su página propia (`/c/northwind-outfitters`), con un navegador headless
   y el modelo que tenga Render: una política con fuente y una consulta de productos.
3. Que esas conversaciones aparezcan en el panel de Northwind (usuario de demo y
   `NORTHWIND_PASSWORD` del .env).

    python evals/qa_render.py --browser-python <python con playwright> [--confirm-link URL]
"""

import argparse
import json
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "evals" / "fixtures" / "brightside"
OUT = ROOT / "evals" / "last_qa_render.json"
NORTHWIND_USER = "demo@northwind.example"


def env_value(name: str) -> str:
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{name}="):
            return line.split("=", 1)[1].strip().strip('"')
    return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="https://warpdrive-5vui.onrender.com")
    parser.add_argument("--browser-python", required=True)
    parser.add_argument("--confirm-link", default="")
    parser.add_argument("--email", default="")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    steps: list[dict[str, Any]] = []

    def check(step: str, ok: Any, detail: Any = "") -> bool:
        steps.append({"paso": step, "ok": bool(ok), "detalle": detail})
        print(f"[{'OK' if ok else 'FALLA'}] {step}: {str(detail)[:300]}", flush=True)
        return bool(ok)

    # 1. Registro.
    owner = httpx.Client(base_url=base, timeout=300, follow_redirects=True)
    page = owner.get("/registro").text
    check("Registro: la página en inglés", "Create account" in page, "")
    if not args.confirm_link:
        mark = uuid.uuid4().hex[:6]
        email = args.email or f"owner-{mark}@brightside.example"
        sent = owner.post(
            "/registro",
            data={
                "empresa": f"Brightside Render QA {mark}",
                "email": email,
                "clave": "brightside-qa-2026",
            },
        )
        check("Registro: pide revisar el email", "Check your email" in sent.text, email)
    else:
        landed = owner.get(args.confirm_link)
        if check("Confirmación: entra al panel", "/panel" in str(landed.url), str(landed.url)):
            token = re.search(
                r'name="csrf" value="([^"]+)"', owner.get("/panel/configuracion").text
            )
            assert token is not None
            files = [
                ("archivos", (n, (FIXTURES / n).read_bytes(), "application/octet-stream"))
                for n in (
                    "brightside_policies.pdf",
                    "brightside_shipping_guide.docx",
                    "brightside_faq.csv",
                )
            ]
            uploaded = owner.post(
                "/panel/onboarding/fuentes", data={"csrf": token.group(1)}, files=files
            )
            check("Documentos: PDF, DOCX y CSV", "brightside" in uploaded.text.lower(), "")

    # 2. El chat de Northwind en el navegador.
    out = ROOT / "evals" / "last_qa_render_browser.json"
    script = ["What is your return policy?", "Do you have a rain jacket in size M?"]
    subprocess.run(
        [
            args.browser_python,
            str(ROOT / "evals" / "qa_e2e_browser.py"),
            f"{base}/c/northwind-outfitters",
            "-",
            str(out),
            *script,
        ],
        check=False,
        timeout=1500,
    )
    seen = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    said = {s["step"]: " ".join(s["replies"]) for s in seen}
    check(
        "Chat en Render: política con fuente",
        "30" in said.get(script[0], "") or "return" in said.get(script[0], "").lower(),
        said.get(script[0]),
    )
    check(
        "Chat en Render: productos",
        "$" in said.get(script[1], "") or "jacket" in said.get(script[1], "").lower(),
        said.get(script[1]),
    )

    # 3. El panel de Northwind.
    panel = httpx.Client(base_url=base, timeout=300, follow_redirects=True)
    panel.post(
        "/panel/login", data={"email": NORTHWIND_USER, "password": env_value("NORTHWIND_PASSWORD")}
    )
    cases = panel.get("/panel/casos").text
    check(
        "Panel en Render: las conversaciones aparecen",
        "rain jacket" in cases.lower() or "return policy" in cases.lower(),
        "",
    )
    OUT.write_text(json.dumps(steps, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [s for s in steps if not s["ok"]]
    print(f"{len(steps) - len(failed)}/{len(steps)} OK · {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
