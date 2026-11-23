from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.channels.twilio.webhook import router as twilio_router
from app.channels.web.routes import router as web_router
from app.channels.whatsapp.webhook import router as whatsapp_router
from app.demo.behind_routes import router as behind_router
from app.demo.cart import router as cart_router
from app.panel.billing_routes import router as billing_router
from app.panel.company_routes import router as company_router
from app.panel.customers_routes import router as customers_router
from app.panel.home_routes import router as home_router
from app.panel.live_routes import router as live_router
from app.panel.metrics_routes import router as metrics_router
from app.panel.nav_routes import router as nav_router
from app.panel.results_routes import router as results_router
from app.panel.review_routes import router as review_router
from app.panel.routes import router as panel_router
from app.panel.signup_routes import router as signup_router
from app.panel.simulation_routes import router as simulation_router
from app.panel.team_routes import router as team_router
from app.panel.trials_routes import router as trials_panel_router
from app.trial.routes import router as trial_router


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
app.include_router(home_router)  # Home (decisión 085)
app.include_router(nav_router)  # secciones del panel y direcciones de antes (decisión 085)
app.include_router(live_router)  # probar y salir en vivo (decisión 056)
app.include_router(signup_router)  # registro self-service e invitaciones (decisión 058)
app.include_router(team_router)
app.include_router(metrics_router)
app.include_router(review_router)  # revisión de conversaciones (decisión 073)
app.include_router(customers_router)  # memoria de los clientes (decisión 078)
app.include_router(simulation_router)  # simulaciones por empresa (decisión 080)
app.include_router(results_router)  # resultados medibles (decisión 083)
app.include_router(billing_router)
app.include_router(behind_router)  # vista detrás de escena (decisión 041)
app.include_router(cart_router)  # carrito de la demo de Northwind (decisión 065)
app.include_router(trial_router)  # prueba gratis self-service (decisión 075)
app.include_router(trials_panel_router)  # sus pruebas, en el panel del operador


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
