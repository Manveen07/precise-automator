# Precise Automator

Internal FastAPI tool that turns operator-supplied messaging copy into a
validated Smartlead campaign. Operators upload (or paste) a sequence file,
review the parsed plan, optionally revise it with Claude, and click
**Sync to Smartlead**. The app creates a Smartlead campaign as a draft;
launching the campaign and adding leads remain manual steps in Smartlead.

## Stack

- FastAPI + Jinja2 (server-rendered HTML, no SPA)
- MongoDB Atlas (single collection `precise_automator_campaigns`)
- Smartlead REST API
- Anthropic SDK (only for AI revisions and spintax — not initial generation)

No Postgres, no Redis, no separate worker process. Sync runs inline via
FastAPI `BackgroundTasks`.

## Running locally

1. Copy `.env.example` to `.env` and fill in real values.
2. Install deps:

   ```powershell
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   ```

3. Boot the app:

   ```powershell
   .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000
   ```

4. Open http://localhost:8000/.

## Required environment variables

| Variable | Purpose |
|---|---|
| `MONGODB_URL` | Atlas connection string |
| `MONGODB_DB_NAME` | Database name (e.g. `infrabot`) |
| `ANTHROPIC_API_KEY` | Required for AI revisions / spintax |
| `SMARTLEAD_PRECISELEAD_API_KEY` | API key for the PreciseLead workspace |
| `SMARTLEAD_PRECISELEAD_CLIENT_ID` | Optional numeric override for PreciseLead; if blank, the app tries to fetch Smartlead clients and match `PreciseLead` by name. If the API key is client-scoped and cannot list clients, campaign creation omits `client_id` so Smartlead uses the key's default client. |
| `SMARTLEAD_BELARDI_WONG_API_KEY` | API key for Belardi Wong |
| `SMARTLEAD_DARLEAN_API_KEY` | API key for Darlean |
| `APP_BASE_URL` | Used to register Smartlead reply webhooks (HTTPS only) |

## Data model

A campaign is a single Mongo document:

```
{
  _id: ObjectId,
  smartlead_campaign_id: int | null,    // populated after first sync
  smartlead_workspace: "preciselead" | "belardi_wong" | "darlean",
  campaign_name: "...",
  raw_input: { ... },                   // original input + parsed messaging
  current_plan: { ... },                // CampaignPlan JSON
  validation_errors: [...],
  status: "drafting" | "ready" | "syncing" | "synced" | "failed" | "archived",
  last_sync_error: str | null,
  created_at: ISODate,
  updated_at: ISODate,
  synced_at: ISODate | null,
}
```

Re-syncing an already-synced campaign updates the existing Smartlead campaign
in place — never creates a duplicate.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest
```

Unit tests use `mongomock` so they don't need a live Atlas connection.
