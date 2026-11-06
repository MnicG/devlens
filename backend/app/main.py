from fastapi import FastAPI

app = FastAPI(title="DevLens API", version="0.1.0")


@app.get("/")
def read_root() -> dict[str, str]:
    return {"status": "ok", "system": "DevLens Evidence Engine"}
