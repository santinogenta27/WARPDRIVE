"""Cola de trabajos sobre Postgres (tabla `jobs`), sin broker extra."""

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class Job:
    id: uuid.UUID
    kind: str
    payload: dict[str, Any]
    attempts: int  # incluye el intento en curso
    max_attempts: int


def enqueue(
    conn: psycopg.Connection,
    kind: str,
    payload: dict[str, Any],
    *,
    idempotency_key: str | None = None,
    run_after: datetime | None = None,
    max_attempts: int = 5,
) -> uuid.UUID:
    """Encola un job. Con la misma `idempotency_key` devuelve el job existente sin duplicarlo."""
    row = conn.execute(
        "insert into jobs (kind, payload, idempotency_key, run_after, max_attempts)"
        " values (%s, %s, %s, coalesce(%s, now()), %s)"
        " on conflict (idempotency_key) do nothing returning id",
        (kind, Jsonb(payload), idempotency_key, run_after, max_attempts),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "select id from jobs where idempotency_key = %s", (idempotency_key,)
        ).fetchone()
    assert row is not None
    job_id: uuid.UUID = row[0]
    return job_id
