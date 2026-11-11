"""Configuración desde variables de entorno, con carga opcional de un archivo .env local."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def load_dotenv(path: Path = REPO_ROOT / ".env") -> None:
    """Carga KEY=VALUE de un .env sin pisar variables ya definidas en el entorno."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.split(" #", 1)[0].strip()
        if value:
            os.environ.setdefault(key.strip(), value)


def database_url() -> str | None:
    load_dotenv()
    return os.environ.get("DATABASE_URL") or None
