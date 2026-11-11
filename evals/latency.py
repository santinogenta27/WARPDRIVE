"""Mide cuánto tarda cada paso de una respuesta: base, modelo, búsqueda y herramientas.

Uso (con PYTHONPATH=backend):
    python evals/latency.py            # modelo y embeddings simulados: mide la base y el cableado
    python evals/latency.py --real     # modelos reales del entorno (gasta cuota)

Corre mensajes típicos de Acme dentro de una transacción que se revierte y muestra, por
mensaje, los milisegundos de cada paso y los viajes a Postgres (`turn_timing` de la auditoría).
"""

import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "backend", ROOT / "mocks" / "acme-api", ROOT / "evals"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from simulated.stack import SimulatedStack, build_real_stack, build_simulated_stack  # noqa: E402

from app.audit import log as audit_log  # noqa: E402
from app.db.connection import connect  # noqa: E402

MESSAGES = [
    ("lucia", "hola"),
    ("lucia", "¿Abren los domingos?"),
    ("lucia", "¿Dónde está mi pedido 1001?"),
    ("martin", "Quiero el reembolso del pedido 1005"),
]
COLUMNS = ("total_ms", "base_ms", "modelo_ms", "busqueda_ms", "herramientas_ms", "otros_ms")


def measure(stack: SimulatedStack) -> list[dict[str, Any]]:
    rows = []
    for label, text in MESSAGES:
        result = stack.send(label, text)
        events = audit_log.reconstruct_case(stack.conn, stack.seed.tenant_id, result.case_id)
        timing = [e.payload for e in events if e.kind == "turn_timing"][-1]
        rows.append({"mensaje": text, "accion": result.action, **timing})
        stack.close_open_cases()
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real", action="store_true", help="modelos reales del entorno")
    args = parser.parse_args()
    conn = connect()
    try:
        if args.real:
            stack = build_real_stack(conn, Path(tempfile.mkdtemp()), cache_models=False)
        else:
            stack = build_simulated_stack(conn)
        rows = measure(stack)
    finally:
        conn.rollback()
        conn.close()
    print(
        f"{'mensaje':<40}" + "".join(f"{c.removesuffix('_ms'):>14}" for c in COLUMNS) + "  viajes"
    )
    for row in rows:
        cells = "".join(f"{row[c]:>11} ms" for c in COLUMNS)
        print(f"{row['mensaje'][:38]:<40}{cells}  {row['llamadas']['base']:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
