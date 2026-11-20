"""Worker del onboarding dentro del proceso de la API (decisión 026).

El plan gratis de Render tiene un solo servicio, sin proceso worker. El pipeline se encola en
`jobs` como cualquier otro job y lo toma un hilo de este proceso, con el mismo `Worker` de la
cola (mismas garantías: un job no se ejecuta dos veces y, si falla, se reintenta).

El hilo no consulta la base todo el tiempo (mantendría despierta la base de Neon): duerme hasta
que alguien encola una corrida (`kick`) y, por las dudas, revisa cada 10 minutos. Al arrancar la
API revisa una vez, para retomar una corrida que haya quedado a mitad por un reinicio.
"""

import logging
import os
import threading
from datetime import timedelta

from app.companies.onboarding import JOB_KIND, run_onboarding_job
from app.jobs.worker import Handler, Worker

logger = logging.getLogger(__name__)
_wake = threading.Event()
_lock = threading.Lock()
_thread: threading.Thread | None = None
IDLE_SECONDS = 600.0


def _handlers() -> dict[str, Handler]:
    """El onboarding y la prueba gratis: su armado y su borrado a los 7 días (decisión 075)."""
    from app.trial import service as trial

    return {
        JOB_KIND: run_onboarding_job,
        trial.JOB_KIND: trial.run_build_job,
        trial.EXPIRE_JOB: trial.run_expire_job,
    }


def _loop() -> None:
    while True:
        try:
            # Una corrida puede tardar varios minutos: un job en curso no se da por colgado
            # hasta pasada una hora.
            worker = Worker(None, _handlers(), lock_timeout=timedelta(hours=1))
            try:
                while worker.run_once():
                    pass
            finally:
                worker.close()
        except Exception:  # noqa: BLE001 — el hilo no se cae: lo intenta en la próxima vuelta
            logger.exception("El worker del onboarding falló")
        _wake.wait(IDLE_SECONDS)
        _wake.clear()


def enabled() -> bool:
    return os.environ.get("ONBOARDING_WORKER", "on").strip().lower() != "off"


def kick() -> None:
    """Avisa que hay una corrida en la cola (y arranca el hilo si todavía no corre)."""
    global _thread
    if not enabled():
        return
    with _lock:
        if _thread is None or not _thread.is_alive():
            _thread = threading.Thread(target=_loop, name="onboarding-worker", daemon=True)
            _thread.start()
    _wake.set()
