"""El widget en un navegador headless, para el QA de punta a punta (Hito QA 2, etapa 2).

Abre una página de prueba con el código del widget pegado, abre el chat y manda cada mensaje del
guion. Espera la respuesta antes de seguir y saca una captura. `{code}` se reemplaza por el último
código de verificación del log del servidor (mailer de consola). Escribe un JSON con lo que vio.

Corre con Playwright y el Chrome del sistema, fuera del entorno del proyecto:

    <python con playwright> evals/qa_e2e_browser.py URL LOG SALIDA.json "mensaje 1" "{code}" ...
"""

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
CODE = re.compile(r"\b(\d{6})\b")
WAIT_S = 240


def items(page: Any) -> list[dict[str, str]]:
    """Los mensajes del chat, con su clase (agent, human, user, notice)."""
    found: list[dict[str, str]] = page.locator(".log > *").evaluate_all(
        "nodes => nodes.map(n => ({cls: n.className, text: n.innerText}))"
    )
    return found


def last_code(log: Path) -> str:
    text = log.read_text(encoding="utf-8", errors="replace")
    start = text.rfind("Mailer de consola")
    found = CODE.findall(text[start : start + 600]) if start >= 0 else []
    if not found:
        raise RuntimeError("No hay un código de verificación en el log")
    return found[-1]


def replies(items: list[dict[str, str]] | None) -> list[str]:
    """Lo que respondió la tienda (el agente o una persona), sin el indicador "Thinking…"."""
    return [
        i["text"]
        for i in items or []
        if re.search(r"\b(agent|human)\b", i["cls"]) and "typing" not in i["cls"]
    ]


def main() -> int:
    url, log, out, *script = sys.argv[1:]
    shots = Path(out).with_suffix("")
    shots.mkdir(parents=True, exist_ok=True)
    seen: list[dict[str, object]] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=CHROME)
        page = browser.new_page(viewport={"width": 1280, "height": 860})
        page.goto(url)
        launcher = page.locator("button.launcher")  # Playwright atraviesa el shadow DOM
        launcher.wait_for(state="visible", timeout=30000)
        time.sleep(2)  # que el widget termine de leer su configuración
        launcher.click()
        page.locator("textarea#msg").wait_for(state="visible", timeout=15000)
        time.sleep(1.5)
        page.screenshot(path=str(shots / "00-abierto.png"))
        seen.append({"step": "abrir", "replies": replies(items(page))})
        for n, raw in enumerate(script, start=1):
            text = raw.replace("{code}", last_code(Path(log))) if "{code}" in raw else raw
            before = len(replies(items(page)))
            page.locator("textarea#msg").fill(text)
            page.locator("button.send").click()
            time.sleep(1)
            started = time.time()
            got: list[str] = []
            while time.time() - started < WAIT_S:
                got = replies(items(page))[before:]
                busy = page.locator(".typing").count() > 0
                if got and not busy:
                    time.sleep(2)  # puede llegar más de un mensaje
                    got = replies(items(page))[before:]
                    break
                time.sleep(1)
            page.screenshot(path=str(shots / f"{n:02d}.png"))
            seen.append({"step": text, "replies": got, "seconds": round(time.time() - started)})
            print(f"> {text}\n< {' | '.join(got)[:400]}", flush=True)
        browser.close()
    Path(out).write_text(json.dumps(seen, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
