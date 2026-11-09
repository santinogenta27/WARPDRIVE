"""Worker de la cola: `python -m app.jobs` (con PYTHONPATH=backend, como en el Makefile)."""

import logging

from app.channels.whatsapp.handler import JOB_KIND, handle_message_job, production_deps
from app.config import load_dotenv
from app.jobs.worker import Worker


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    deps = production_deps()
    worker = Worker(None, {JOB_KIND: lambda conn, job: handle_message_job(conn, job, deps)})
    logging.info("Worker escuchando la cola (%s). Ctrl+C para cortar.", JOB_KIND)
    try:
        worker.run_forever()
    finally:
        worker.close()


if __name__ == "__main__":
    main()
