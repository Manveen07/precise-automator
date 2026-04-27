# Precise Automator

Internal FastAPI app for creating, reviewing, revising, and syncing Smartlead draft campaigns from validated `CampaignPlan` JSON.

## Local Setup

```powershell
docker compose up -d --wait postgres redis
C:\Python312\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --disable-pip-version-check -r requirements.txt
copy .env.example .env
python -m app.scripts.init_db
uvicorn app.main:app --reload
```

On this machine, `python` currently resolves to the Windows Store alias. Use `C:\Python312\python.exe` until PATH is fixed.

Docker Desktop must be running before `docker compose up -d postgres redis`.
Local Postgres is mapped to host port `55432` to avoid conflicts with any existing Postgres on `5432`.

The app intentionally stores Smartlead environment variable names in the database, not raw API keys.

`python -m app.scripts.init_db` runs Alembic migrations and then seeds the default workspace/template rows.

Run migrations only:

```powershell
alembic upgrade head
```

## Services

- `web`: FastAPI + Jinja + HTMX UI and internal API.
- `worker`: RQ worker for Smartlead sync jobs.
- `postgres`: durable application state.
- `redis`: queue backend.

## Worker

```powershell
rq worker campaign_sync --url redis://localhost:6379/0 --worker-class rq.SimpleWorker
```

Use `rq.SimpleWorker` on Windows because the default RQ worker depends on `os.fork()`, which is Unix-only.

## Database Changes

Create a new migration after model changes:

```powershell
alembic revision --autogenerate -m "describe change"
alembic upgrade head
```
