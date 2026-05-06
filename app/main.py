from fastapi import Depends, FastAPI, Response
from fastapi.staticfiles import StaticFiles

from app.auth import require_auth, router as auth_router
from app.routes import campaigns, inboxes, leads, monitor

app = FastAPI(title="Precise Automator")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(auth_router)
app.include_router(monitor.router)
protected = [Depends(require_auth)]
app.include_router(campaigns.router, dependencies=protected)
app.include_router(leads.router, dependencies=protected)
app.include_router(inboxes.router, dependencies=protected)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}


@app.head("/")
def root_head() -> Response:
    return Response(status_code=200)


@app.head("/health")
def health_head() -> Response:
    return Response(status_code=200)
