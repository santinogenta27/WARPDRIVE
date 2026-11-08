"""Tarea 4 — test de aceptación: un job fallido se reintenta y no se ejecuta dos veces.

El worker confirma (commit) sus cambios, así que estos tests usan datos reales: cada uno usa un
`kind` propio y limpia al terminar.
"""

import uuid
from collections.abc import Iterator
from datetime import timedelta

import psycopg
import pytest

from app.db.connection import connect
from app.jobs.queue import Job, enqueue
from app.jobs.worker import Worker

NO_BACKOFF = timedelta(0)


@pytest.fixture
def kind(migrated: str) -> Iterator[str]:
    name = f"test-{uuid.uuid4()}"
    yield name
    with connect(migrated) as conn:
        conn.execute("delete from jobs where kind = %s", (name,))
        conn.execute("delete from tenants where name like %s", (f"{name}%",))


def _job_row(url: str, job_id: uuid.UUID) -> tuple[str, int]:
    with connect(url) as conn:
        row = conn.execute("select status, attempts from jobs where id = %s", (job_id,)).fetchone()
    assert row is not None
    return row[0], row[1]


def _enqueue(url: str, kind: str, **kwargs: object) -> uuid.UUID:
    with connect(url) as conn:
        job_id = enqueue(conn, kind, {"n": 1}, **kwargs)  # type: ignore[arg-type]
    return job_id


def test_job_fallido_se_reintenta_y_no_duplica_efectos(migrated: str, kind: str) -> None:
    calls: list[int] = []

    def handler(conn: psycopg.Connection, job: Job) -> None:
        calls.append(job.attempts)
        # Efecto en la base dentro de la misma transacción que marca el job como hecho.
        conn.execute("insert into tenants (name) values (%s)", (f"{kind}-efecto",))
        if job.attempts == 1:
            raise RuntimeError("falla transitoria")

    job_id = _enqueue(migrated, kind)
    worker = Worker(migrated, {kind: handler}, backoff=lambda _: NO_BACKOFF)
    try:
        assert worker.run_once() is True  # intento 1: falla y se reprograma
        assert _job_row(migrated, job_id) == ("queued", 1)
        assert worker.run_once() is True  # intento 2: funciona
        assert worker.run_once() is False  # ya no hay nada que hacer
    finally:
        worker.close()

    assert calls == [1, 2]
    assert _job_row(migrated, job_id) == ("done", 2)
    with connect(migrated) as conn:
        effects = conn.execute(
            "select count(*) from tenants where name = %s", (f"{kind}-efecto",)
        ).fetchone()
    assert effects == (1,)  # el efecto del intento fallido se revirtió


def test_dos_workers_no_toman_el_mismo_job(migrated: str, kind: str) -> None:
    other = Worker(migrated, {kind: lambda conn, job: None}, backoff=lambda _: NO_BACKOFF)
    seen_by_other: list[bool] = []

    def handler(conn: psycopg.Connection, job: Job) -> None:
        seen_by_other.append(other.run_once())  # mientras este job corre

    _enqueue(migrated, kind)
    worker = Worker(migrated, {kind: handler}, backoff=lambda _: NO_BACKOFF)
    try:
        assert worker.run_once() is True
    finally:
        worker.close()
        other.close()
    assert seen_by_other == [False]


def test_encolar_con_la_misma_clave_no_duplica(migrated: str, kind: str) -> None:
    first = _enqueue(migrated, kind, idempotency_key=f"{kind}-clave")
    second = _enqueue(migrated, kind, idempotency_key=f"{kind}-clave")
    assert first == second
    with connect(migrated) as conn:
        assert conn.execute("select count(*) from jobs where kind = %s", (kind,)).fetchone() == (1,)


def test_job_que_agota_sus_intentos_queda_fallido(migrated: str, kind: str) -> None:
    def handler(conn: psycopg.Connection, job: Job) -> None:
        raise RuntimeError("siempre falla")

    job_id = _enqueue(migrated, kind, max_attempts=2)
    worker = Worker(migrated, {kind: handler}, backoff=lambda _: NO_BACKOFF)
    try:
        assert worker.run_once() is True
        assert worker.run_once() is True
        assert worker.run_once() is False
    finally:
        worker.close()
    assert _job_row(migrated, job_id) == ("failed", 2)


def test_job_de_un_worker_caido_se_recupera_una_vez(migrated: str, kind: str) -> None:
    calls: list[int] = []
    job_id = _enqueue(migrated, kind)

    crashed = Worker(migrated, {kind: lambda conn, job: None})
    assert crashed.claim() is not None  # lo toma y "se cae" sin ejecutarlo
    crashed.close()
    with connect(migrated) as conn:
        conn.execute(
            "update jobs set locked_at = now() - interval '1 hour' where id = %s", (job_id,)
        )

    worker = Worker(
        migrated,
        {kind: lambda conn, job: calls.append(job.attempts)},
        lock_timeout=timedelta(minutes=5),
    )
    try:
        assert worker.run_once() is True
        assert worker.run_once() is False
    finally:
        worker.close()
    assert calls == [2]
    assert _job_row(migrated, job_id) == ("done", 2)
