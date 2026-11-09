from fastapi import FastAPI

from app.channels.twilio.webhook import router as twilio_router
from app.channels.whatsapp.webhook import router as whatsapp_router

app = FastAPI(title="Enterprise Data AI")
app.include_router(whatsapp_router)
app.include_router(twilio_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
