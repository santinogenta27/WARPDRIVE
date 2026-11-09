from fastapi import FastAPI

from app.channels.whatsapp.webhook import router as whatsapp_router

app = FastAPI(title="Enterprise Data AI")
app.include_router(whatsapp_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
