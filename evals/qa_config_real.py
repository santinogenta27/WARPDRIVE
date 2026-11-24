"""Hito QA 2, etapa 1 — la muestra de la matriz de configuración con el modelo real.

Las mismas opciones que `backend/tests/test_qa_configuracion.py` cambia desde el panel, pero con
el modelo real gratuito: Groq con Gemini de respaldo (nunca Claude: se vacía ANTHROPIC_API_KEY) y
embeddings de Gemini. Muestra: idioma, trato, firma, una regla de monto, un documento borrado y
el agente pausado. Todo corre en una transacción de la base de staging que se revierte al final.

    python evals/qa_config_real.py            # imprime cada caso y guarda el resultado en JSON

Unas 40 llamadas al modelo.
"""

import json
import os
import re
import sys
import uuid
from contextlib import nullcontext
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for extra in ("backend", "evals", "mocks/acme-api", "mocks/northwind-api"):
    sys.path.insert(0, str(ROOT / extra))

from app.config import load_dotenv  # noqa: E402

load_dotenv()
# Solo modelos gratuitos: sin Claude ni Voyage.
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["VOYAGE_API_KEY"] = ""
os.environ["LLM_FALLBACK"] = "on"
os.environ["ONBOARDING_WORKER"] = "off"
os.environ["PANEL_EMAIL"] = "operador@plataforma.example"
os.environ["PANEL_PASSWORD"] = "clave-del-operador-qa"

from fastapi.testclient import TestClient  # noqa: E402
from psycopg.types.json import Jsonb  # noqa: E402
from simulated.stack import RecordingMailer  # noqa: E402

from acme_api import create_app  # noqa: E402  (la API simulada de Acme)
from app.agent.loop import Agent  # noqa: E402
from app.channels.web.routes import WebDeps, web_deps  # noqa: E402
from app.channels.whatsapp.routing import register_whatsapp_channel  # noqa: E402
from app.companies import knowledge  # noqa: E402
from app.companies.create import chat_slug, create_company  # noqa: E402
from app.db.connection import connect, tenant_scope  # noqa: E402
from app.demo.acme import seed_acme  # noqa: E402
from app.llm.client import LLMClient, PostgresUsageRecorder  # noqa: E402
from app.llm.embedding_cache import CachingEmbedder  # noqa: E402
from app.llm.factory import embedder_from_env, embedding_backend, provider_from_env  # noqa: E402
from app.main import app  # noqa: E402
from app.panel import auth  # noqa: E402
from app.panel.routes import PanelDeps, panel_deps  # noqa: E402
from app.policy.guardian import Guardian  # noqa: E402
from app.tools.runtime import ToolRuntime  # noqa: E402

OUT = ROOT / "evals" / "last_qa_config_real.json"
VOSEO = re.compile(
    r"\b(pod[eé]s|quer[eé]s|ten[eé]s|decime|pasame|indicanos|fijate|contame)\b", re.I
)
TUTEO = re.compile(r"\b(puedes|quieres|tienes|dime|pásame|indícanos|fíjate|cuéntame)\b", re.I)
ENGLISH = re.compile(r"\b(the|you|your|we|our|is|are)\b", re.I)


def model_calls(conn: Any, tenant: uuid.UUID) -> int:
    with tenant_scope(conn, tenant):
        row = conn.execute("select count(*) from usage where model not ilike '%embed%'").fetchone()
    return int(row[0]) if row else 0


def main() -> int:
    conn = connect(os.environ["DATABASE_URL_STAGING"])
    results: list[dict[str, Any]] = []

    current: dict[str, Any] = {}

    def trail(case: Any) -> list[str]:
        """Los pasos del caso: por qué respondió así."""
        if case is None:
            return []
        with tenant_scope(conn, current["tenant"]):
            rows = conn.execute(
                "select kind, payload from audit_events where case_id = %s order by seq", (case,)
            ).fetchall()
        keep = {"classified", "rag_search", "tool_call", "grounding_rejected", "escalated"}
        return [f"{k}: {json.dumps(p, ensure_ascii=False)[:300]}" for k, p in rows if k in keep]

    def record(
        option: str, expected: str, ok: bool, said: str, case: Any = None, **extra: Any
    ) -> None:
        results.append(
            {
                "opcion": option,
                "esperado": expected,
                "ok": ok,
                "respuesta": said,
                "pasos": trail(case or current.get("case")),
                **extra,
            }  # fmt: skip
        )
        print(f"[{'OK' if ok else 'FALLA'}] {option}: {said[:220]!r}", flush=True)

    try:
        recorder = PostgresUsageRecorder(conn)
        embedder = CachingEmbedder(
            embedder_from_env(recorder),
            ROOT / "evals" / ".cache" / "embeddings",
            profile=str(embedding_backend()),
        )
        llm = LLMClient(provider=provider_from_env(), recorder=recorder)
        seed = seed_acme(conn, embedder)
        sandbox = TestClient(create_app())
        guardian = Guardian(conn, ToolRuntime(sandbox))
        agent = Agent(conn, llm, embedder, guardian, mailer=RecordingMailer())
        address = f"web:qa-real-{uuid.uuid4().hex[:8]}"
        register_whatsapp_channel(conn, seed.tenant_id, phone_number_id=address, kind="web")
        shop = create_company(conn, "Tienda QA Real", "dueno@tiendaqareal.example", "clave-qa-real")
        current["tenant"] = seed.tenant_id
        app.dependency_overrides[panel_deps] = lambda: PanelDeps(
            connection=lambda: nullcontext(conn),
            tenant_id=current["tenant"],
            guardian=lambda c, t: guardian,
            embedder=lambda c: embedder,
            kick_worker=lambda: None,
        )
        app.dependency_overrides[web_deps] = lambda: WebDeps(
            connection=lambda: nullcontext(conn), agent=lambda c: agent, address=address
        )
        auth._failures.clear()
        panel = TestClient(app)
        panel.post("/panel/login", data={"email": os.environ["PANEL_EMAIL"],
                                          "password": os.environ["PANEL_PASSWORD"]})  # fmt: skip

        def csrf(client: TestClient, path: str = "/panel/agent") -> str:
            found = re.search(r'name="csrf" value="([^"]+)"', client.get(path).text)
            assert found is not None, path
            return found.group(1)

        def post(client: TestClient, path: str, data: dict[str, Any], **kw: Any) -> None:
            response = client.post(path, data={"csrf": csrf(client), **data},
                                   follow_redirects=False, **kw)  # fmt: skip
            assert "error=" not in response.headers.get("location", ""), response.headers

        def contact(label: str) -> uuid.UUID:
            return seed.contacts[label] if label in seed.contacts else seed.contacts["lucia"]

        def send(label: str, text: str) -> Any:
            result = agent.handle_message(seed.tenant_id, contact(label), text)
            current["case"] = result.case_id
            return result

        def voice(**values: str) -> None:
            base = {"tono": "", "trato": "", "usar": "", "evitar": "", "emojis": "si", "firma": ""}
            post(panel, "/panel/configuracion/voz", {**base, **values})

        def close_cases() -> None:
            with tenant_scope(conn, seed.tenant_id):
                conn.execute("update cases set status = 'resolved' where status <> 'resolved'")
                conn.execute("update approvals set status = 'rejected' where status = 'pending'")

        # 1. Idioma.
        post(panel, "/panel/configuracion/idioma", {"idioma": "en"})
        said = send("lucia", "Are you open on Sundays?").message
        record(
            "Idioma inglés",
            "Responde en inglés, sin derivar",
            bool(ENGLISH.search(said)) and "passing you" not in said,
            said,
        )
        post(panel, "/panel/configuracion/idioma", {"idioma": "es"})
        close_cases()
        said = send("lucia", "¿Abren los domingos?").message
        record("Idioma de vuelta a español", "Responde en español", not ENGLISH.search(said), said)

        # 2. Trato de usted.
        close_cases()
        voice(trato="usted")
        said = send("lucia", "¿Dónde está mi pedido 1001?").message
        ok = not VOSEO.search(said) and not TUTEO.search(said)
        record("Trato de usted", "Sin voseo ni tuteo", ok, said)
        voice()
        close_cases()
        said = send("lucia", "¿Dónde está mi pedido 1001?").message
        record("Trato de vuelta a vos", "Sin tuteo ni usted", not TUTEO.search(said), said)

        # 3. Firma.
        close_cases()
        voice(firma="— Equipo Acme")
        said = send("lucia", "¿Dónde está mi pedido 1001?").message
        record("Firma", "Termina con la firma", said.rstrip().endswith("— Equipo Acme"), said)
        voice()

        # 4. Regla de monto: tope de 1000 al reembolso.
        close_cases()
        with tenant_scope(conn, seed.tenant_id):
            refund = conn.execute(
                "select id from tools where name = 'iniciar_reembolso'"
            ).fetchone()
        assert refund is not None
        post(panel, f"/panel/configuracion/herramientas/{refund[0]}",
             {"limites": "si", "monto_maximo": "1000", "aprobacion_sobre": "0"})  # fmt: skip
        result = send("martin", "Quiero el reembolso del pedido 1005")
        tools = [(t.name, t.status) for t in result.tool_calls]
        promised = re.search(
            r"reembols\w* (realizad|hech|aprobad|procesad)|ya (te )?reembols|iniciamos|en revisi",
            result.message,
            re.I,
        )
        ok = ("iniciar_reembolso", "executed") not in tools and not promised
        record("Monto máximo del reembolso", "No reembolsa ni lo promete", ok, result.message,
               herramientas=tools)  # fmt: skip

        # 5 y 6. Empresa nueva: documento borrado y agente pausado.
        current["tenant"] = shop.tenant_id
        owner = TestClient(app)
        auth._failures.clear()
        owner.post("/panel/login", data={"email": "dueno@tiendaqareal.example",
                                          "password": "clave-qa-real"})  # fmt: skip
        doc = b"# Envios al sur\n\nEl envio a Ushuaia tarda 18 dias habiles desde la compra.\n"
        owner.post("/panel/knowledge/upload", data={"csrf": csrf(owner, "/panel/knowledge")},
                   files=[("archivos", ("envios.md", doc, "text/markdown"))])  # fmt: skip
        slug = chat_slug(conn, shop.tenant_id)

        def cases() -> set[Any]:
            with tenant_scope(conn, shop.tenant_id):
                return {r[0] for r in conn.execute("select id from cases").fetchall()}

        def ask(text: str, origin: str | None = None) -> str:
            before = cases()
            headers = {"Origin": origin} if origin else {}
            api = TestClient(app)
            session = api.post("/chat/session", params={"canal": slug}, headers=headers).json()
            answer = api.post("/chat/messages", json={"session": session["session"], "text": text},
                              headers=headers)  # fmt: skip
            texts = []
            for block in answer.text.strip().split("\n\n"):
                fields = dict(line.split(": ", 1) for line in block.splitlines())
                if fields.get("event") == "message":
                    texts.append(json.loads(fields["data"])["text"])
            current["case"] = next(iter(cases() - before), None)
            return " ".join(texts)

        said = ask("¿Cuánto tarda el envío a Ushuaia?")
        record("Documento subido", "Responde 18 días", "18" in said, said)
        found = next(d for d in knowledge.documents(conn, shop.tenant_id))
        post(owner, f"/panel/onboarding/indexados/{found.id}/borrar", {})
        said = ask("¿Cuánto tarda el envío a Ushuaia?")
        record("Documento borrado", "Ya no dice 18 días", "18" not in said, said)

        post(owner, "/panel/deploy/domains", {"dominios": "www.tiendaqareal.example"})
        with tenant_scope(conn, shop.tenant_id):
            conn.execute(
                "insert into onboarding_runs (tenant_id, status, report) values (%s, 'done', %s)",
                (shop.tenant_id, Jsonb({"resolucion_estimada": 0.8,
                                        "adversariales": {"total": 1, "superados": 1}})),
            )  # fmt: skip
        post(owner, "/panel/salir-en-vivo", {"activo": "si"})
        post(owner, "/panel/salir-en-vivo", {"activo": "no"})
        before = model_calls(conn, shop.tenant_id)
        said = ask("¿Hacen envíos a Córdoba?", origin="https://www.tiendaqareal.example")
        calls = model_calls(conn, shop.tenant_id) - before
        record("Agente pausado", "Deriva sin llamar al modelo", calls == 0 and bool(said), said,
               llamadas=calls)  # fmt: skip
    finally:
        app.dependency_overrides.clear()
        conn.rollback()
        conn.close()
    OUT.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [r for r in results if not r["ok"]]
    print(f"{len(results) - len(failed)}/{len(results)} OK · {OUT}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
