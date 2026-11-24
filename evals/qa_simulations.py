"""Hito QA 2, etapa 3 — simulaciones con el modelo real gratuito, con avance guardado.

Las mismas simulaciones de Test & Improve (decisión 080): el modelo arma clientes simulados con un
objetivo, conversa con el agente y un juez califica cada conversación. Acá se corren con Groq y
Gemini de respaldo (nunca Claude) y se guarda cada cliente terminado en
`evals/.cache/qa-sim-<empresa>.json`. Si se agota la cuota del día, se corta, y la próxima corrida
sigue desde ahí con el mismo comando.

    python evals/qa_simulations.py --company acme --size 10
    python evals/qa_simulations.py --company northwind --size 10
    python evals/qa_simulations.py --company acme --restart     # empieza de cero
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for extra in ("backend", "evals", "mocks/acme-api", "mocks/northwind-api"):
    sys.path.insert(0, str(ROOT / extra))

from app.config import load_dotenv  # noqa: E402

load_dotenv()
os.environ["ANTHROPIC_API_KEY"] = ""  # solo modelos gratuitos
os.environ["VOYAGE_API_KEY"] = ""

from simulated.stack import RecordingMailer, build_real_stack  # noqa: E402

from app.agent.loop import Agent  # noqa: E402
from app.db.connection import connect  # noqa: E402
from app.llm.errors import QuotaExhausted  # noqa: E402
from app.policy.guardian import Guardian  # noqa: E402
from app.simulation import runner as sim  # noqa: E402

CACHE = ROOT / "evals" / ".cache"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--company", choices=("acme", "northwind"), default="acme")
    parser.add_argument("--size", type=int, default=10)
    parser.add_argument("--max-turns", type=int, default=sim.MAX_CUSTOMER_TURNS)
    parser.add_argument("--restart", action="store_true")
    args = parser.parse_args()
    progress = CACHE / f"qa-sim-{args.company}.json"
    if args.restart:
        progress.unlink(missing_ok=True)
    state: dict[str, Any] = (
        json.loads(progress.read_text(encoding="utf-8"))
        if progress.exists()
        else {"personas": [], "conversaciones": []}
    )

    def save() -> None:
        progress.parent.mkdir(parents=True, exist_ok=True)
        progress.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    command = f"python evals/qa_simulations.py --company {args.company} --size {args.size}"
    conn = connect(os.environ.get("DATABASE_URL_STAGING") or os.environ["DATABASE_URL"])
    try:
        stack = build_real_stack(
            conn, CACHE, cache_models=False, company=args.company, fallback=True
        )
        tenant = stack.seed.tenant_id
        company = sim._company(conn, tenant)
        runtime = sim.sandbox_runtime(conn, tenant)

        def factory(c: Any, rt: Any, llm: Any) -> Agent:
            return Agent(c, llm, stack.embedder, Guardian(c, rt), mailer=RecordingMailer())

        if not state["personas"]:
            state["personas"] = sim.generate_personas(stack.llm, tenant, company, args.size)
            save()
        done = {c["cliente"] for c in state["conversaciones"]}
        for persona in state["personas"]:
            if persona["id"] in done:
                continue
            turns, evidence, error = sim._converse(
                conn, tenant, stack.llm, factory, runtime, company, persona, args.max_turns
            )
            if error.endswith("QuotaExhausted"):  # también GeminiQuotaExhausted
                raise QuotaExhausted("cuota agotada durante la conversación", daily=True)
            verdict = (
                {**{k: False for k in sim.JUDGE_KEYS}, "motivo": f"La conversación falló: {error}."}
                if error
                else sim.judge(stack.llm, tenant, company, persona, turns, evidence)
            )
            state["conversaciones"].append(
                {
                    "cliente": persona["id"],
                    "tipo": persona["tipo_de_caso"],
                    "corrida": 1,
                    "turnos": turns,
                    "juez": verdict,
                    "paso": all(verdict[k] for k in sim.JUDGE_KEYS),
                }
            )
            save()
            passed = all(verdict[k] for k in sim.JUDGE_KEYS)
            reason = str(verdict.get("motivo", ""))[:200]
            print(
                f"[{'OK' if passed else 'FALLA'}] {persona['tipo_de_caso']}: {reason}", flush=True
            )
    except QuotaExhausted as error:
        save()
        print(
            f"Se agotó la cuota ({error}). Avance guardado en {progress}.\nPara seguir: {command}"
        )
        return 2
    finally:
        conn.rollback()
        conn.close()
    summary = sim.summarize(state["personas"], state["conversaciones"])
    out = ROOT / "evals" / f"last_qa_simulations-{args.company}.json"
    out.write_text(
        json.dumps({"resumen": summary, **state}, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"{args.company}: {summary['pass1']:.0%} de las conversaciones pasaron · {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
