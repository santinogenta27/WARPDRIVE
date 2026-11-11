"""Worker de la cola: `python -m app.jobs` (con PYTHONPATH=backend, como en el Makefile)."""

import logging

from app.channels.inbound import handle_inbound_job
from app.channels.twilio.channel import JOB_KIND as TWILIO_JOB_KIND
from app.channels.twilio.channel import TwilioWhatsApp, twilio_from_env
from app.channels.whatsapp.handler import (
    JOB_KIND,
    WhatsAppDeps,
    handle_message_job,
    production_deps,
)
from app.config import load_dotenv
from app.jobs.worker import Handler, Worker


def build_handlers(meta: WhatsAppDeps, twilio: TwilioWhatsApp | None) -> dict[str, Handler]:
    """Cloud API de Meta siempre; Twilio solo si sus variables están en el entorno."""
    handlers: dict[str, Handler] = {
        JOB_KIND: lambda conn, job: handle_message_job(conn, job, meta),
    }
    if twilio is not None:
        handlers[TWILIO_JOB_KIND] = lambda conn, job: handle_inbound_job(
            conn, job, channel=twilio, agent=meta.agent
        )
    return handlers


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv()
    handlers = build_handlers(production_deps(), twilio_from_env())
    worker = Worker(None, handlers)
    logging.info("Worker escuchando la cola (%s). Ctrl+C para cortar.", ", ".join(handlers))
    try:
        worker.run_forever()
    finally:
        worker.close()


if __name__ == "__main__":
    main()
