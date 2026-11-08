from fastapi import FastAPI

app = FastAPI(title="Enterprise Data AI")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
