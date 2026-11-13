from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.channels.twilio.webhook import router as twilio_router
from app.channels.web.routes import router as web_router
from app.channels.whatsapp.webhook import router as whatsapp_router
from app.demo.behind_routes import router as behind_router
from app.panel.billing_routes import router as billing_router
from app.panel.company_routes import router as company_router
from app.panel.metrics_routes import router as metrics_router
from app.panel.routes import router as panel_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Al arrancar, el worker del onboarding retoma una corrida que haya quedado a mitad por un
    # reinicio del servicio (decisión 026). Después duerme hasta que alguien encole otra.
    from app.companies.worker import kick

    kick()
    yield


app = FastAPI(title="Enterprise Data AI", lifespan=lifespan)
app.include_router(whatsapp_router)
app.include_router(twilio_router)
app.include_router(web_router)
app.include_router(panel_router)
app.include_router(company_router)
app.include_router(metrics_router)
app.include_router(billing_router)
app.include_router(behind_router)  # vista detrás de escena (decisión 041)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
