from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from app.auth import require_auth, router as auth_router
from app.routes import campaigns, inboxes, leads

app = FastAPI(title="Precise Automator")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_router)
protected = [Depends(require_auth)]
app.include_router(campaigns.router, dependencies=protected)
app.include_router(leads.router, dependencies=protected)
app.include_router(inboxes.router, dependencies=protected)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
