"""Migraciones en SQL plano, aplicadas en orden y una sola vez cada una.

Uso: `python -m app.db.migrate` (con PYTHONPATH=backend, como en el Makefile).
"""

import sys
from pathlib import Path

from app.db.connection import connect

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
_LOCK_KEY = 727_274  # evita que dos procesos migren a la vez


def migrate(url: str | None = None) -> list[str]:
    """Aplica las migraciones pendientes en una transacción. Devuelve las aplicadas."""
    applied_now: list[str] = []
    with connect(url) as conn:
        conn.execute("select pg_advisory_xact_lock(%s)", (_LOCK_KEY,))
        conn.execute(
            "create table if not exists schema_migrations ("
            " version text primary key,"
            " applied_at timestamptz not null default now())"
        )
        applied = {row[0] for row in conn.execute("select version from schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in applied:
                continue
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("insert into schema_migrations (version) values (%s)", (path.stem,))
            applied_now.append(path.stem)
    return applied_now


if __name__ == "__main__":
    done = migrate()
    print("Migraciones aplicadas:", ", ".join(done) if done else "ninguna (al día)")
    sys.exit(0)
