"""Worker de la cola.

Garantías:
  * Dos workers nunca toman el mismo job (`FOR UPDATE SKIP LOCKED`).
  * El handler corre en la misma transacción que marca el job como `done`: si falla, sus efectos
    en la base se revierten y el job se reprograma con espera; si el proceso se cae a mitad, el
    job queda `running` y otro worker lo recupera tras `lock_timeout`. Así los efectos en la
    base nunca se duplican. Los efectos externos (APIs) deben usar claves de idempotencia.
"""

import time
from collections.abc import Callable
from datetime import timedelta

import psycopg

from app.db.connection import connect
from app.jobs.queue import Job

Handler = Callable[[psycopg.Connection, Job], None]
Backoff = Callable[[int], timedelta]


def exponential_backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(2**attempts, 300))


class Worker:
    def __init__(
        self,
        url: str | None,
        handlers: dict[str, Handler],
        *,
        backoff: Backoff = exponential_backoff,
        lock_timeout: timedelta = timedelta(minutes=5),
    ) -> None:
        self._conn = connect(url)
        self._handlers = handlers
        self._backoff = backoff
        self._lock_timeout = lock_timeout

    def close(self) -> None:
        self._conn.close()

    def claim(self) -> Job | None:
        """Toma el próximo job listo (o uno colgado) y lo marca `running`. Confirma enseguida."""
        row = self._conn.execute(
            "update jobs j set status = 'running', attempts = j.attempts + 1, locked_at = now()"
            " where j.id = ("
            "   select id from jobs"
            "   where kind = any(%s)"
            "     and ((status = 'queued' and run_after <= now())"
            "          or (status = 'running' and locked_at < now() - %s"
            "              and attempts < max_attempts))"
            "   order by run_after"
            "   for update skip locked"
            "   limit 1)"
            " returning j.id, j.kind, j.payload, j.attempts, j.max_attempts",
            (list(self._handlers), self._lock_timeout),
        ).fetchone()
        self._conn.commit()
        if row is None:
            return None
        return Job(id=row[0], kind=row[1], payload=row[2], attempts=row[3], max_attempts=row[4])

    def run_once(self) -> bool:
        """Procesa un job si hay uno listo. Devuelve si procesó algo."""
        job = self.claim()
        if job is None:
            return False
        try:
            owned = self._conn.execute(
                "select 1 from jobs where id = %s and status = 'running' and attempts = %s"
                " for update",
                (job.id, job.attempts),
            ).fetchone()
            if owned is None:  # otro worker lo recuperó: no ejecutar dos veces
                self._conn.rollback()
                return True
            self._handlers[job.kind](self._conn, job)
            self._conn.execute(
                "update jobs set status = 'done', finished_at = now(), locked_at = null,"
                " last_error = null where id = %s",
                (job.id,),
            )
            self._conn.commit()
        except Exception as exc:  # noqa: BLE001 — cualquier falla del handler reprograma el job
            self._conn.rollback()
            self._conn.execute(
                "update jobs set"
                " status = case when attempts >= max_attempts then 'failed' else 'queued' end,"
                " run_after = now() + %s, locked_at = null, last_error = %s,"
                " finished_at = case when attempts >= max_attempts then now() end"
                " where id = %s and attempts = %s",
                (self._backoff(job.attempts), repr(exc)[:2000], job.id, job.attempts),
            )
            self._conn.commit()
        return True

    def run_forever(self, poll_interval: float = 1.0) -> None:
        while True:
            if not self.run_once():
                time.sleep(poll_interval)
