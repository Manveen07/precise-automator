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
| `APP_USERNAME` | HTTP Basic auth username |
| `APP_PASSWORD` | HTTP Basic auth password |
| `ANTHROPIC_API_KEY` | Required for AI revisions / spintax |
| `SMARTLEAD_PRECISELEAD_API_KEY` | API key for the PreciseLead workspace |
| `SMARTLEAD_BELARDI_WONG_API_KEY` | API key for Belardi Wong |
| `SMARTLEAD_DARLEAN_API_KEY` | API key for Darlean |
| `APP_BASE_URL` | Public app URL used in deployment metadata |
| `SMARTLEAD_WEBHOOK_SECRET` | Shared secret for Smartlead monitor webhooks |
| `SLACK_BOT_TOKEN` | Slack bot token for monitor alerts and action menus |
| `SLACK_SIGNING_SECRET` | Slack signing secret for action verification |
| `SLACK_CHANNEL_ID` | Slack channel for campaign pause alerts |
| `BOUNCE_PROTECTION_THRESHOLD` | Bounce pause threshold; default `0.03` |

For PreciseLead campaigns, Smartlead agency client attribution is inferred from
the campaign name and passed as `client_id` on `/campaigns/create`:

| Campaign name contains | Smartlead client |
|---|---|
| `Melior` | Ryan Markman / Melior (`12256`) |
| `OSC`, `Staff AI`, `SVSG`, `Sri` | Srivatsan / SVSG (`145916`) |
| `Avenge`, `Avench` | Anuroop / Avench (`88657`) |

If none of those aliases match, campaign creation omits `client_id`, leaving the
campaign under PreciseLeads itself.

## Data model

A campaign is a single Mongo document:

```
{
  _id: ObjectId,
  smartlead_campaign_id: int | null,    // populated after first sync
  smartlead_workspace: "preciselead" | "belardi_wong" | "darlean",
  smartlead_client_id: int | null,      // inferred from campaign name for PreciseLead agency clients
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

## Smartlead bounce monitor

Precise Automator can also receive Smartlead status webhooks and alert Slack
when a campaign is paused with bounce rates above the configured threshold.

Smartlead webhook URL:

```text
https://YOUR-RENDER-URL/api/monitor/smartlead?secret=SMARTLEAD_WEBHOOK_SECRET
```

Slack interactivity URL for Resume Campaign actions:

```text
https://YOUR-RENDER-URL/api/monitor/slack/actions
```

The monitor classifies a pause as:

- `confirmed_bounce_protection` when the webhook includes an explicit bounce reason.
- `likely_bounce_protection` when status is `PAUSED` and analytics show bounce rate >= `BOUNCE_PROTECTION_THRESHOLD`.
- `generic_pause` for other pauses.

## Tests

```powershell
.venv\Scripts\python.exe -m pytest
```

Unit tests use `mongomock` so they don't need a live Atlas connection.

## Render deployment

This repo uses `render.yaml` for a single Docker web service. There is no
Render Postgres, Redis, or worker service.

1. Push the repo to GitHub.
2. In Render, create a new Blueprint from the repo.
3. Set every `sync: false` secret from the Render dashboard.
4. Keep `MONGODB_DB_NAME=infrabot`; app data is isolated in the
   `precise_automator_campaigns` collection.
5. After deploy, verify `/health` returns 200, then open `/app` and confirm the
   browser prompts for Basic auth.

Before production, rotate any Mongo Atlas credential that was pasted into chat
or shared outside Render's secret store.
