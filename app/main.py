from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import require_auth
from app.routes import campaigns, inboxes, leads

app = FastAPI(title="Precise Automator")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
protected = [Depends(require_auth)]
app.include_router(campaigns.router, dependencies=protected)
app.include_router(leads.router, dependencies=protected)
app.include_router(inboxes.router, dependencies=protected)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
