from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.routes import campaigns, inboxes, leads, webhooks, workspaces

app = FastAPI(title="Precise Automator")

app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(campaigns.router)
app.include_router(workspaces.router)
app.include_router(webhooks.router)
app.include_router(leads.router)
app.include_router(inboxes.router)


@app.get("/health")
def health() -> dict[str, bool]:
    return {"ok": True}
