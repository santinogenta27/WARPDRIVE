"""Runner de evals de la empresa de demo Acme.

El runner real llega en la tarea 11. Hasta entonces informa que no hay casos y sale con 0,
para que `make evals` sea parte del flujo desde el inicio sin fingir resultados.
"""

import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def main() -> int:
    cases = list((FIXTURES / "acme" / "eval_cases").glob("*.json"))
    if not cases:
        print("evals: 0 casos todavía (el runner llega en la tarea 11).")
        return 0
    print(f"evals: {len(cases)} casos encontrados, pero el runner llega en la tarea 11.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
