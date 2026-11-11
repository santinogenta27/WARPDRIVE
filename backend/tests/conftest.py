"""Fixtures compartidas.

Los tests de base de datos corren contra DATABASE_URL (Neon en desarrollo, Postgres del CI).
Cada test usa una transacción que se revierte al final: no deja datos en la base.
"""

from collections.abc import Iterator

import psycopg
import pytest

from app.config import database_url
from app.db.connection import connect
from app.db.migrate import migrate


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clave de cifrado propia de los tests (no depende del .env local)."""
    monkeypatch.setenv("ENCRYPTION_KEY", "s3gMcV8bfYlqsO1y1V2N2rU0n-c4Wcq1xYQq0p6CwVU=")
    # El worker del onboarding que vive en la API no arranca en los tests.
    monkeypatch.setenv("ONBOARDING_WORKER", "off")


@pytest.fixture(scope="session")
def db_url() -> str:
    url = database_url()
    if not url:
        pytest.skip("DATABASE_URL no está definida")
    return url


@pytest.fixture(scope="session")
def migrated(db_url: str) -> str:
    migrate(db_url)
    return db_url


@pytest.fixture
def conn(migrated: str) -> Iterator[psycopg.Connection]:
    connection = connect(migrated)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()
