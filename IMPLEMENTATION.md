# Precise Automator — Implementation Reference

A complete description of every feature currently implemented and how each piece fits together. Source of truth: the code at the time of writing. If code and this doc disagree, code wins.

---

## 1. Purpose

Precise Automator is an internal FastAPI tool that takes operator-supplied messaging copy (uploaded text or pasted) and produces a validated **CampaignPlan JSON**, then pushes that plan to **Smartlead** as a draft campaign with the correct settings, schedule, and email sequence. It exists so operators can stop assembling Smartlead campaigns by hand.

The pipeline:

```
operator input  →  parser  →  deterministic plan (or AI revision)  →  schema validation
              →  approve  →  RQ-queued worker  →  Smartlead REST API
```

Smartlead campaigns are created in **draft** status. The app never starts/launches a campaign, never adds leads, and never enables tracking. Those remain manual steps in Smartlead.

---

## 2. Stack

| Layer | Choice | Notes |
|-------|--------|-------|
| Web | FastAPI 0.115 | Sync routes, async where needed (Smartlead I/O) |
| Templates | Jinja2 | Server-rendered HTML; no SPA |
| ORM | SQLAlchemy 2.0 | `DeclarativeBase`, `Mapped[]` style |
| Migrations | Alembic 1.15 | Single revision so far: `20260424_0001_initial_schema` |
| DB | PostgreSQL 16 (`psycopg` v3) | JSONB for plan storage |
| Queue | RQ on Redis 7 | Single named queue: `campaign_sync` |
| HTTP client | httpx 0.28 | Async, 90 s default timeout |
| LLM | Anthropic SDK 0.49 | Used **only** for revisions, not initial generation |
| Validation | pydantic 2.11 | `CampaignPlan` schema |
| Auth | None | Currently unauthenticated; `User` table exists but unused |
| Static | Plain CSS, vanilla JS | Reskinned in claude.ai aesthetic |

Pinned versions in [requirements.txt](requirements.txt). Container image is `python:3.12-slim` ([Dockerfile](Dockerfile)). Deploy target is Railway via Dockerfile builder ([railway.json](railway.json)).

---

## 3. Repository layout

```
precise-automator/
├── app/
│   ├── main.py              FastAPI app + router registration + /health
│   ├── config.py            Settings (pydantic-settings) + get_secret_value
│   ├── db.py                SQLAlchemy engine + SessionLocal + get_db
│   ├── seed.py              seed_defaults() — workspaces + default template
│   ├── models/
│   │   └── campaign.py      All ORM models in one file
│   ├── schemas/
│   │   └── campaign_plan.py Pydantic CampaignPlan + nested types
│   ├── routes/
│   │   ├── campaigns.py     UI pages + the bulk of the API
│   │   ├── workspaces.py    GET /api/workspaces, /api/templates
│   │   ├── webhooks.py      POST /api/webhooks/smartlead (HMAC/shared-secret auth)
│   │   ├── leads.py         POST /api/leads/upload (CSV preview only)
│   │   └── inboxes.py       POST /api/inboxes/recommend (mailbox picker)
│   ├── services/
│   │   ├── parser_service.py        text → parsed messaging
│   │   ├── local_plan_service.py    parsed messaging → CampaignPlan
│   │   ├── validation_service.py    plan → list[error_str]
│   │   ├── sequence_builder.py      plan.sequence → Smartlead seq payload
│   │   ├── smartlead_service.py     thin httpx client
│   │   ├── anthropic_service.py     Claude generation + revision
│   │   └── slack_service.py         optional outbound webhook
│   ├── workers/
│   │   └── sync_campaign.py         RQ job — orchestrates Smartlead API calls
│   ├── scripts/
│   │   └── init_db.py               wait-for-db + alembic upgrade + seed
│   ├── templates/                   Jinja templates (base, dashboard, detail, new)
│   └── static/                      styles.css, campaign_new.js
├── migrations/                      Alembic
├── scripts/
│   ├── bootstrap.ps1                first-run setup (Windows)
│   └── run_worker.ps1               start Windows-compatible RQ worker
├── tests/                           pytest, mock-DB style
├── docker-compose.yml               postgres:55432, redis:6379
├── Dockerfile
├── railway.json
├── alembic.ini
├── requirements.txt
└── README.md
```

---

## 4. Configuration

Defined in [app/config.py](app/config.py) via pydantic-settings, loaded from `.env`.

| Variable | Default | Purpose |
|---|---|---|
| `APP_ENV` | `local` | informational only |
| `APP_BASE_URL` | `http://localhost:8000` | used to construct Smartlead webhook URL; webhook is **only registered if URL is HTTPS** |
| `APP_SECRET_KEY` | `replace_me` | declared but **not used anywhere** |
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:55432/precise_automator` | local Postgres on **non-standard port 55432** to avoid clashing with system Postgres |
| `REDIS_URL` | `redis://localhost:6379/0` | RQ backend |
| `ANTHROPIC_API_KEY` | `replace_me` | required for AI revision; literal string `"replace_me"` is treated as not-configured |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | model id used for revisions |
| `SMARTLEAD_WEBHOOK_SECRET` | `None` | optional HMAC/shared-secret verifier for Smartlead webhooks; required outside local if the route is exposed |
| `SLACK_WEBHOOK_URL` | `None` | optional Slack webhook for `send_slack_summary()` |
| `INBOX_SHEET_SCRIPT_URL` | `None` | declared, not currently called |
| `BLOCKED_PHRASES` | `["guaranteed", "risk-free"]` | token-boundary blocklist enforced in body validation |

`get_secret_value(env_name)` (same file) returns the value of an OS env var, falling back to cached `.env` values. Used to look up Smartlead workspace API keys whose env-var **name** is stored in the DB.

---

## 5. Database schema

Defined in [app/models/campaign.py](app/models/campaign.py). All tables use `uuid` primary keys (server-generated `uuid4`) and `JSONB` for structured fields. Timestamps are timezone-aware (`DateTime(timezone=True)`).

### Core tables

#### `users`
- `id, email (unique), name, role, password_hash, oauth_subject, is_active, created_at`
- `role` is constrained to `creator | reviewer | admin`
- **Currently unused.** No login, no session, `created_by` on requests is never populated.

#### `smartlead_workspaces`
- `workspace_key` (unique slug, e.g. `smartlead_mcp`)
- `display_name`
- `api_key_env_name` — name of the env var holding the actual API key. The raw key is **never stored in Postgres.**
- `client_id` — optional integer passed to Smartlead's `campaigns/create` to scope the campaign to a sub-account
- `active` — soft-disable flag

#### `mailbox_groups`
- FK → workspace
- `email_account_ids_json` (JSONB)
- Defined but no routes write to it.

#### `campaign_templates`
- `template_key`, `name`, `version`, `system_prompt`, `example_block`, `schema_version`, `active`
- `schema_version` is currently always `campaign_plan_v1`
- One default template seeded: `cold_email_standard_v1`.

#### `campaign_requests`
- The top-level "I want a campaign" record.
- `raw_input_json` — full operator input including parsed messaging
- `lead_source_type` — `csv_upload | pasted_list | none` (only `none` written today)
- `status` constrained to: `drafting | needs_revision | approved | syncing | synced | failed | archived`
- `created_at`, `updated_at` (auto-updates on row write)
- Relationships: `workspace`, `template`, `drafts`, `runs`

#### `campaign_drafts`
- One per generation/revision. Multiple per request.
- `draft_json` — the validated `CampaignPlan`
- `prompt_version` — schema version of the template used
- `model_name` — `local_parser` or the Anthropic model id
- `validation_status` constrained to: `generated | invalid | valid | superseded | approved`
- `validation_errors_json` — list of human-readable error strings

#### `conversation_sessions`
- One per campaign_request.
- `message_log_json` — append-only audit of events: generated_draft, ai_revision_instruction, ai_revised_draft, ai_revision_failed.
- `latest_draft_id` updated on every event.

#### `campaign_runs`
- One per queued RQ job. The app protects one Smartlead campaign per request by reusing existing queued/running/succeeded runs.
- `idempotency_key` — `f"{request_id}:{draft_id}:smartlead_sync"` for first-time syncs. The unique constraint backs up the route-level duplicate-click guard.
- `smartlead_campaign_id` — populated after the create-campaign step succeeds.
- `run_status`: `queued | running | succeeded | failed | retrying`
- `started_at`, `finished_at`, `error_text`

#### `campaign_run_steps`
- Per-step audit inside a run. Useful for debugging which Smartlead call failed.
- `status`: `pending | running | succeeded | failed | skipped`
- `request_json`, `response_json` (truncated to dict by `_log_step`), `duration_ms`, `error_text`

#### `lead_uploads`
- `request_id`, `filename`, `row_count`, `normalized_leads_json`, `validation_errors_json`
- The model exists; the `/api/leads/upload` route currently **does not persist** here — it only previews.

#### `webhook_events`
- All inbound Smartlead webhook payloads, raw. `workspace_id` is resolved from `smartlead_campaign_id` via `CampaignRun` when possible.

### Status state-machines

```
campaign_requests.status
    drafting → needs_revision → drafting → approved → syncing → synced
                                                              ↘ failed

campaign_drafts.validation_status
    generated → invalid → (regenerate)
              ↘ valid → approved (terminal until next gen)
                      ↘ superseded (replaced by AI revision)

campaign_runs.run_status
    queued → running → succeeded
                     ↘ failed
```

---

## 6. The `CampaignPlan` schema

Defined in [app/schemas/campaign_plan.py](app/schemas/campaign_plan.py). This is the contract between the parser/AI and the Smartlead worker. **Every draft must validate against this schema** plus the extra rules in [validation_service.py](app/services/validation_service.py) before it can be approved.

```python
CampaignPlan {
  workspace_key: str             # must match an active SmartleadWorkspace
  client_key: str | None
  campaign_name: str             # stripped, must be non-empty
  template_family: str           # default "cold_email_standard_v1"
  goal: "book_meeting" | "reply" | "event_meeting"

  lead_source: { type: "csv_upload" | "pasted_list" | "none", expected_count: int | None }

  schedule: {
    timezone: str                # default "America/New_York"
    days_of_the_week: [int]      # 1–7, must be non-empty
    start_hour: "HH:MM"          # string compared, must be < end_hour
    end_hour:   "HH:MM"
    min_time_btw_emails: int     # minutes between sends, default 17
    max_new_leads_per_day: int   # default 100; validated to 1..500
  }

  settings: {
    send_as_plain_text: bool = True
    track_opens:        bool = False    # validation rejects True
    track_clicks:       bool = False    # validation rejects True
    stop_on_reply:      bool = True     # validation rejects False
    enable_ai_esp_matching: bool = True
    auto_pause_domain_leads_on_reply: bool = True
    ooo_restart_delay_days: int = 10
  }

  inbox_selection: {
    mode: "manual_ids" | "recommend" | "skip"
    email_account_ids: [int]
    provider_mix: { gmail: 0.7, outlook: 0.3 }   # default
  }

  sequence: [SequenceStep]       # 1..4 steps
  approval_required: bool = True
  notes_for_operator: [str]
}

SequenceStep {
  step_number: int
  delay_days:  int
  variants:    [SequenceVariant]   # at least one
}

SequenceVariant {
  variant_label: str | None       # auto-assigned A, B, C... if missing
  subject:       str               # required non-empty for step 1; must be empty for follow-ups
  body:          str
}
```

### Validation rules beyond the schema

[`validate_campaign_plan(plan, active_workspace_keys)`](app/services/validation_service.py) returns a list of error strings (empty = valid):

1. `workspace_key` matches an active workspace.
2. Timezone is resolvable via `zoneinfo.ZoneInfo`.
3. `max_new_leads_per_day` ∈ [1, 500].
4. `stop_on_reply` is true.
5. `track_opens` and `track_clicks` are both false.
6. Every step-1 variant has a non-empty subject.
7. No follow-up step variant has a non-empty subject.
8. No step has more than 20 variants.
9. Each body:
   - No malformed merge tags. Regex `(?<!\{)\{[A-Za-z_]\w*\}(?!\})` must not match — i.e. single-brace `{first_name}` is rejected; allowed forms are `{{first_name}}`, `{{company_name}}`, `{{company}}`, `%Signature%`.
   - Balanced brace count.
   - Doesn't contain any `BLOCKED_PHRASES` (substring, case-insensitive).

---

## 7. Parser

[`parse_messaging_file(text, selected_sequence_name)`](app/services/parser_service.py) handles **two input formats**:

### A. Repository format
Multiple sequences in one file, each headed by a "Subject Line Options" block then numbered emails:

```
Benchmark
Subject Line Options:
1. Quick Benchmark
2. Re: benchmark

Email 1
V1
Hi {{first_name}}, ...
%Signature%

V2
Hey {{first_name}}, ...

Email 2
... etc
```

The parser slices the file by `Subject Line` headings, names each sequence by the line just before the heading, and extracts subjects + email steps + variants per sequence. The operator picks one sequence by name (`selected_sequence_name`); if no match, the **first** sequence is silently selected. The other sequences are still returned in `parsed_messaging.campaigns` for the UI.

### B. Step-section format
A single sequence written as:

```
Subject Line Options:
1. ...

Step 1
V1
body
V2
body

Step 2
...
```

Used when the input is a single campaign rather than a repository.

### Variant + spintax handling

Inside a step, lines `V1`, `V2`, ... split variants. Within a variant, the body is everything **after the last `Spintax` marker** (case-insensitive, optional dashes/colon). If no spintax marker exists, the whole variant text is the body. This lets writers leave their plain-language draft above and the spintax-fanned version below — only the spintax version is consumed.

Output:
```python
{
  "source_format": "repository" | "step_sections",
  "selected_campaign": str | None,
  "subjects": [str],
  "steps": [{"step_number": int, "body_variants": [{"variant_label": "A", "body": str}]}],
  "campaigns": [...]   # only populated for repository format
}
```

This dict is stored on `CampaignRequest.raw_input_json["parsed_messaging"]`.

---

## 8. Deterministic plan builder

[`build_campaign_plan_from_input(raw_input, note=None)`](app/services/local_plan_service.py) is the **default path** for first-time draft generation. **Claude is not called.** It transforms the parser output into a full `CampaignPlan` dict.

Behavior:

- Takes at most the **first 4 steps** (`steps[:4]`); the schema also caps at 4. Extra steps are silently dropped.
- Step 1 variants: **cross-product of subjects × body variants**. So 2 subjects × 3 body variants = 6 step-1 variants. Each gets an auto label `A..Z` (then `V27`, `V28`, ...).
- Follow-up variants: one per body, subject empty.
- Body normalization: trims whitespace, replaces `%signature%` / `%SIGNATURE%` with `%Signature%`.
- Default delay days per step: `{1: 1, 2: 3, 3: 4, 4: 5}` (3 if step number falls outside).
- Schedule defaults: America/New_York, Mon–Fri (1–5), 09:00–18:00, 17-minute spacing, operator-supplied daily cap.
- Settings defaults: plain text on, tracking off, stop_on_reply on, OOO restart delay = 10 days.
- `inbox_selection.mode = "skip"` by default; if `email_account_ids` are present, initial sync attaches them. `/smartlead/apply` can re-apply inboxes later.
- `notes_for_operator` is seeded with the deterministic-source disclaimer, a "review before sync" note, and the selected sequence name.

The result is then handed to `validate_campaign_plan`. Errors get stored on the draft, status set to `invalid`. No errors → `valid`.

---

## 9. AI revision (optional)

[`AnthropicCampaignService`](app/services/anthropic_service.py) has two methods:

- `generate_campaign_plan` — defined but **not wired to any route**. Initial generation is always deterministic.
- `revise_campaign_plan(latest_plan, revision_instruction, validation_errors, template_prompt, examples)` — used by `/api/campaigns/{id}/revise-draft`.

The system prompt:
- Restricts output to JSON matching `CampaignPlan`.
- Reminds Claude not to call Smartlead, not to invent API responses, not to leak secrets.
- Tells it to preserve merge tags and spintax exactly.
- Enforces step-1 subjects required, follow-up subjects empty.

Sends a single user message containing `{latest_plan, revision_instruction, validation_errors}` as JSON. Parses `response.content[0].text` with `json.loads`.

Failure modes handled by the route ([campaigns.py revise_draft](app/routes/campaigns.py)):

| Failure | What happens |
|---|---|
| `ANTHROPIC_API_KEY` missing or `"replace_me"` | Records an error on the conversation log, deterministic draft preserved, returns `ok=false`. No Anthropic call attempted. |
| `JSONDecodeError` from `json.loads` | Same — log + return `ok=false`, no exception propagates. |
| `AnthropicError` (network, 4xx, 5xx) | Same. |
| Success | Old draft marked `superseded`, new draft stored, validated, status `valid` or `invalid`. |

The conversation session log records each event so the audit trail survives even on revision failure.

---

## 10. Sequence builder (Smartlead format)

[`build_smartlead_sequences(plan_sequence)`](app/services/sequence_builder.py) converts the validated plan's `sequence` array into Smartlead's expected payload:

```python
[
  {
    "seq_number": 1,
    "seq_delay_details": {"delay_in_days": 1},
    "variant_distribution_type": "MANUAL_EQUAL",
    "seq_variants": [
      {"subject": "Quick benchmark", "email_body": "<html...>", "variant_label": "A"},
      ...
    ],
  },
  ...
]
```

`format_email_body_for_smartlead(body)` HTML-formats each plain-text body:
1. Normalizes line endings to `\n`.
2. Right-trims each line.
3. Collapses 3+ consecutive newlines to 2.
4. Ensures `%Signature%` is preceded by a blank line.
5. Replaces `\n\n` with `<br><br>` and remaining `\n` with `<br>`.

Smartlead expects HTML in the body field; this is the minimum HTML conversion for plain-text-style emails.

---

## 11. Smartlead client

[`SmartleadService`](app/services/smartlead_service.py) is a thin async httpx wrapper around `https://server.smartlead.ai/api/v1/`.

### Auth model
API key passed as `?api_key=...` query string on every request. The key is fetched per-call via `get_secret_value(workspace.api_key_env_name)` so different workspaces use different keys without storing keys in the DB.

The client sets `User-Agent: Precise-Automator/1.0`.

### Methods

| Method | Endpoint | Purpose |
|---|---|---|
| `create_campaign(name, client_id?)` | `POST /campaigns/create` | Returns `{id, ...}` |
| `apply_v1_settings(id, ooo_delay_days=10)` | `POST /campaigns/{id}/settings` ×3 | Three sequential settings POSTs (see below) |
| `update_schedule(id, schedule)` | `POST /campaigns/{id}/schedule` | Posts the schedule dict directly |
| `update_sequences(id, sequences)` | `POST /campaigns/{id}/sequences` | Wraps `{"sequences": [...]}` |
| `attach_email_accounts(id, ids)` | `POST /campaigns/{id}/email-accounts` | |
| `add_leads(id, leads)` | `POST /campaigns/{id}/leads` | Defined; **not currently called** |
| `create_webhook(id, url)` | `POST /campaigns/{id}/webhooks` | Subscribes to `EMAIL_REPLY` and `LEAD_CATEGORY_UPDATED` |
| `update_status(id, status)` | `PATCH /campaigns/{id}/status` | |
| `archive_campaign(id)` | `update_status(id, "ARCHIVED")` | |
| `delete_campaign(id)` | `DELETE /campaigns/{id}` | |
| `get_campaign(id)` | `GET /campaigns/{id}` | |
| `get_sequences(id)` | `GET /campaigns/{id}/sequences` | |
| `get_campaign_analytics(id)` | `GET /campaigns/{id}/analytics` | |
| `get_campaign_statistics(id, limit, offset)` | `GET /campaigns/{id}/statistics` | |
| `get_campaign_lead_statistics(id, limit, offset)` | `GET /campaigns/{id}/leads-statistics` | |
| `get_campaign_performance(start, end, tz, ids)` | `GET /analytics/campaign/overall-stats` | |

### `apply_v1_settings` posts three settings calls in sequence

Smartlead's settings endpoint accepts partial updates; the worker splits into three calls because some flag combinations require separate requests:

1. Core settings:
   ```json
   {
     "stop_lead_settings": "REPLY_TO_AN_EMAIL",
     "follow_up_percentage": 50,
     "track_settings": ["DONT_TRACK_EMAIL_OPEN", "DONT_TRACK_LINK_CLICK"],
     "add_unsubscribe_tag": false,
     "unsubscribe_text": "",
     "auto_pause_domain_leads_on_reply": true,
     "enable_ai_esp_matching": true,
     "bounce_autopause_threshold": "3",
     "domain_level_rate_limit": false,
     "ai_categorisation_options": [1,2,3,4,5,6,7,8,9],
     "out_of_office_detection_settings": {
       "ignoreOOOasReply": true,
       "autoCategorizeOOO": false,
       "autoReactivateOOO": true,
       "reactivateOOOwithDelay": 0
     }
   }
   ```
2. OOO restart delay (overrides the 0 above):
   ```json
   {"out_of_office_detection_settings": {"reactivateOOOwithDelay": ooo_delay_days}}
   ```
3. Plain-text enforcement:
   ```json
   {"send_as_plain_text": true, "force_plain_text": true}
   ```

Tracking is hard-coded off and `stop_on_reply` is hard-coded on. The validator also rejects any plan that contradicts this, so the UI cannot bypass it.

---

## 12. HTTP API surface

### Pages (HTML)
| Route | Template | Purpose |
|---|---|---|
| `GET /` | redirect | 303 → `/app` |
| `GET /app` | `dashboard.html` | List of last 25 campaigns w/ smartlead id + last run status |
| `GET /campaigns/new` | `campaign_new.html` | Create-request form |
| `GET /campaigns/{id}` | `campaign_detail.html` | Workflow, draft preview, runs, parsed messaging |
| `GET /health` | JSON | `{"ok": true}` |

### API
| Route | Method | Purpose |
|---|---|---|
| `/api/workspaces` | GET | Active workspaces (calls `seed_defaults` first) |
| `/api/templates` | GET | Active templates (calls `seed_defaults` first) |
| `/api/campaigns/new` | POST (multipart) | Create `CampaignRequest`. Reads upload as utf-8-sig / utf-16 / cp1252 |
| `/api/campaigns/{id}/generate-draft` | POST | Build draft from parsed input (deterministic) |
| `/api/campaigns/{id}/revise-draft` | POST | AI revision (form field `revision_instruction`) |
| `/api/campaigns/{id}/approve` | POST | Mark latest **valid** draft as `approved`, request → `approved` |
| `/api/campaigns/{id}/sync` | POST | Enqueue an RQ job in `campaign_sync` queue, unless an active/succeeded Smartlead run already exists |
| `/api/campaigns/{id}/status` | GET | JSON view of last 5 runs + step-level status |
| `/api/campaigns/{id}/smartlead` | GET | Live `get_campaign` + `get_sequences` from Smartlead |
| `/api/campaigns/{id}/smartlead` | DELETE (`?mode=archive\|delete`) | Same as the dedicated POSTs below; kept for API clients |
| `/api/campaigns/{id}/smartlead/apply` | POST | Re-push schedule/settings/sequences (and inbox attach if ids present) for the existing Smartlead campaign |
| `/api/campaigns/{id}/smartlead/archive` | POST | PATCH status → ARCHIVED |
| `/api/campaigns/{id}/smartlead/delete` | POST | DELETE the Smartlead campaign |
| `/api/campaigns/{id}/analytics` | GET | Top-level + sequence + lead stats + 30-day performance window |
| `/api/leads/upload` | POST | CSV parse + dedup preview only — does **not** persist |
| `/api/inboxes/recommend` | POST | Pick mailboxes from a roster (see §14) |
| `/api/webhooks/smartlead` | POST | Persist any payload to `webhook_events` (no auth) |

### Form vs JSON response

`_api_or_campaign_redirect(request, payload, campaign_id)` returns:
- **303 redirect** to `/campaigns/{id}` if the request `Accept` header contains `text/html` (i.e. a browser form post).
- The raw `payload` dict (JSON) otherwise.

This lets the same endpoints serve the HTML UI and a programmatic client.

---

## 13. The sync worker

[`sync_campaign(run_id)`](app/workers/sync_campaign.py) is the RQ entry point. It runs `asyncio.run(_sync_campaign(run_id))` because the Smartlead client is async but RQ jobs are sync.

### Step ordering

| # | Step name | Action |
|---|---|---|
| 1 | `create_campaign` | `POST /campaigns/create` with name + client_id. Stores the returned id on the run row immediately. |
| 2 | `apply_settings` | The 3 settings POSTs above, forwarding `settings.ooo_restart_delay_days` from the approved draft. |
| 3 | `apply_schedule` | `POST /campaigns/{id}/schedule` with the plan's schedule dict. |
| 4 | `push_sequences` | Builds Smartlead seq payload from `plan.sequence`, posts as `{"sequences": [...]}`. |
| 5 | `attach_email_accounts` (conditional) | If `inbox_selection.email_account_ids` is present, attaches those inboxes during the initial sync. |
| 6 | `create_webhook` (conditional) | Only if `APP_BASE_URL.startswith("https://")`. Subscribes to `EMAIL_REPLY` + `LEAD_CATEGORY_UPDATED` at `/api/webhooks/smartlead`; local skips are recorded as `skipped` run steps. |
| 7 | `verify_campaign` | Fetches the campaign + sequences back from Smartlead and stores in the step's `response_json`. Used as a sanity probe. |

Each step is wrapped in `_log_step`, which:
1. Inserts a `CampaignRunStep` row with `status=running`.
2. Awaits the actual call.
3. On success: `status=succeeded`, sets `response_json`, computes `duration_ms`, returns the response dict.
4. On failure: `status=failed`, records `error_text`, **re-raises** so the outer `try/except` marks the whole run failed.

The whole run is bracketed:
- On entry: run row → `running`, `started_at` set.
- On success: run row → `succeeded`, `finished_at` set.
- On any exception: run row → `failed`, `error_text` includes status code and response body for Smartlead HTTP failures, `finished_at` set, then exception **re-raised** so RQ records the job as failed too.

### Inbox attachment during sync

If the approved draft has `inbox_selection.email_account_ids`, the worker attaches those Smartlead inboxes during initial sync. The **Apply Latest Draft** action also applies inboxes, settings, schedule, and sequences to an already-created Smartlead campaign.

### Why a custom SimpleWorker on Windows

The default RQ `Worker` forks a child for each job (`os.fork()`), which doesn't exist on Windows. `SimpleWorker` runs the job in-process, but RQ's default timeout handler still uses Unix-only `SIGALRM`. The provided `scripts/run_worker.ps1` uses `app.workers.rq_windows.WindowsSimpleWorker`, a `SimpleWorker` subclass that swaps in RQ's `TimerDeathPenalty`.

---

## 14. Inbox recommender

`POST /api/inboxes/recommend` ([app/routes/inboxes.py](app/routes/inboxes.py)) takes a list of mailbox rows (typically pasted from a Smartlead inbox export) and a `target_daily_volume`, then picks a subset.

Filters applied:
- `availability == "FREE"` (case-insensitive)
- `warmup_rep ≥ 90`
- `test_status == "inbox"` (case-insensitive)
- `available_capacity > 0`

For duplicate rows on the same `account_id`, keeps the **lower** capacity row (defensive — any row available means the account can be used).

Then sizes the selection: `inboxes_needed = ceil(target_volume / avg_capacity)`, capped at 30, floor 1. Sorted to prefer **unassigned** inboxes (rows with `campaign in {None, "", "N/A"}`) and higher capacity.

Returns the selected rows, their account ids, the sum of capacities, and a `{gmail, outlook}` provider count.

This route is currently unused by the rest of the app — it's a building block for the eventual "auto-pick mailboxes" feature.

---

## 15. Lead upload preview

`POST /api/leads/upload` reads the uploaded CSV, decodes as utf-8-sig, requires `email` or `Email` column, dedups on lowercased email, and returns row count + first 50 errors. It does **not** persist to `lead_uploads` despite the model existing. Treat it as a UI-side validation helper.

---

## 16. Webhook receiver

`POST /api/webhooks/smartlead` ([app/routes/webhooks.py](app/routes/webhooks.py)) verifies either `X-Smartlead-Signature` as an HMAC-SHA256 body signature or a shared secret in `?secret=` / `X-Smartlead-Webhook-Secret`. It extracts `campaign_id`/`smartlead_campaign_id` and `event_type`/`type`, resolves the workspace when possible, and inserts a row into `webhook_events`.

The worker's step 5 is the only place this URL is registered with Smartlead, and only when `APP_BASE_URL` is HTTPS.

---

## 17. Slack notifier

[`send_slack_summary(text)`](app/services/slack_service.py) — single function, posts `{"text": text}` to `SLACK_WEBHOOK_URL`. **No callsites yet.** Stub for future use.

---

## 18. UI

Server-rendered Jinja templates extending `base.html`, styled by [app/static/styles.css](app/static/styles.css) (claude.ai-inspired: warm cream `#faf9f5`, coral accent `#c96442`, serif headings, sans body).

### Pages

#### Dashboard ([app/templates/dashboard.html](app/templates/dashboard.html))
Table of last 25 campaigns: Name → request status badge → last run badge → Smartlead ID → workspace → updated. Empty state with primary CTA when no campaigns.

#### New Campaign ([app/templates/campaign_new.html](app/templates/campaign_new.html))
Multi-part form:
- Workspace dropdown (active workspaces)
- Template dropdown (active templates)
- Campaign name (required)
- Max new leads / day (1–500, default 100)
- Messaging file upload (text)
- Sequence name (optional, used to pick within a repository file)
- Messaging text textarea (gets auto-filled from the upload via [campaign_new.js](app/static/campaign_new.js))

The JS also auto-fills the campaign name from the filename if blank, stripping the `Email Sequence Repository` prefix and underscores.

#### Campaign Detail ([app/templates/campaign_detail.html](app/templates/campaign_detail.html))
Five sections:

1. **Summary grid** — request status, draft status, source (`local_parser` or model id), Smartlead ID.
2. **Next Action** — one primary button at a time: Generate, Approve, Sync, Sync in Progress, or Already Synced.
3. **Draft preview** — settings summary (cap, hours, timezone, tracking off) and per-step variant tables (label, subject, body in `<pre>` for whitespace fidelity). Validation errors render as a red notice. Raw JSON is collapsed in `<details>`.
4. **AI Revision** — textarea + button posting to `/revise-draft`. Shown only when a draft exists.
5. **Smartlead** — run table plus collapsed maintenance actions (Apply / Inspect / Analytics / Archive / Delete). Actions are disabled until a Smartlead ID exists.
6. **Parsed Messaging** — collapsed `<details>` showing the raw parser output JSON.

### Templating helper

`_api_or_campaign_redirect` (see §12) lets each form post return a 303 redirect for the browser flow without preventing JSON consumption.

---

## 19. Migrations & seeding

[migrations/versions/20260424_0001_initial_schema.py](migrations/versions/20260424_0001_initial_schema.py) is the only revision. It creates all tables in `models/campaign.py`.

[`init_db.py`](app/scripts/init_db.py):
1. `wait_for_database()` — retries `SELECT 1` up to 30 times with 1-second backoff.
2. Runs `alembic upgrade head` programmatically.
3. Calls `seed_defaults(db)`.

[`seed_defaults`](app/seed.py) inserts (idempotent on `workspace_key` / `template_key`):

- Workspace `smartlead_mcp` → reads `SMARTLEAD_MCP_API_KEY`
- Workspace `belardi_wong` → reads `SMARTLEAD_BELARDI_WONG_API_KEY`
- Template `cold_email_standard_v1` with a system prompt that mirrors the `AnthropicCampaignService` constraints.

`seed_defaults` is also called eagerly from the dashboard, `/campaigns/new`, `/api/workspaces`, and `/api/templates` so a brand-new DB lights up without manual seeding.

---

## 20. Tests

Six files under [tests/](tests/), all use a hand-written mock DB (`FakeDb`/`FakeQuery`) rather than spinning up Postgres:

- `test_campaign_routes.py` — 3 tests: deterministic generate-draft does **not** instantiate Anthropic; AI revision with bad JSON returns ok=false rather than 500; HTML form posts redirect 303.
- `test_local_plan_service.py` — plan output structure, body normalization, subject × variant cross-product.
- `test_parser_service.py` — both formats, spintax handling, sequence selection.
- `test_sequence_builder.py` — Smartlead payload shape, `<br>` substitution, signature spacing.
- `test_smartlead_service.py` — URL building / api_key injection.
- `test_validation_service.py` — every rule in `validate_campaign_plan`.

Last full run: **14 passed** (Linux/Mac env). On Windows the suite can't even collect without `psycopg[binary]` installed — see review item #21.

---

## 21. Local dev workflow

Per [README.md](README.md) and [scripts/bootstrap.ps1](scripts/bootstrap.ps1):

```powershell
docker compose up -d --wait postgres redis     # postgres on 55432, redis on 6379
C:\Python312\python.exe -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env                          # fill in SMARTLEAD_*_API_KEY etc
python -m app.scripts.init_db                  # alembic + seed
uvicorn app.main:app --reload                   # http://localhost:8000
```

In a second terminal, the worker:

```powershell
.\scripts\run_worker.ps1
# or:
rq worker campaign_sync --url redis://localhost:6379/0 --worker-class app.workers.rq_windows.WindowsSimpleWorker
```

Smartlead webhook registration is skipped in local because `APP_BASE_URL` is `http://`, so reply tracking only kicks in once deployed with HTTPS.

---

## 22. Deployment

[Dockerfile](Dockerfile): `python:3.12-slim`, copies the source, runs `uvicorn app.main:app --host 0.0.0.0 --port 8080`.

[railway.json](railway.json): Dockerfile builder, restart on failure up to 10 times.

The worker is not in the Dockerfile CMD — Railway needs a separate service running `rq worker campaign_sync --url $REDIS_URL` on Linux, or the Windows-compatible class when running on Windows.

Required env vars in production:
- `DATABASE_URL`, `REDIS_URL` (provided by Railway add-ons)
- `APP_BASE_URL` (HTTPS — required for webhook step to fire)
- `ANTHROPIC_API_KEY` (only if AI revision is used)
- `SMARTLEAD_MCP_API_KEY` and any other `*_API_KEY` referenced by seeded workspaces
- `SMARTLEAD_WEBHOOK_SECRET` (currently unused — pending fix)
- `SLACK_WEBHOOK_URL` (optional)

---

## 23. Things explicitly NOT implemented

So you can see the boundaries:

- **No auth.** All routes are open. `users` table exists but never queried.
- **No CSRF protection.** Destructive POST forms accept any cross-origin submission.
- **No webhook signature verification.** `/api/webhooks/smartlead` accepts anything.
- **No lead push to Smartlead.** `add_leads` exists in the client but is unused. Leads remain a manual step in Smartlead.
- **No campaign launch.** Campaigns are always created in draft. No `update_status(id, "ACTIVE")` call.
- **No initial-generation use of Claude.** Only revision. `AnthropicCampaignService.generate_campaign_plan` is dead code.
- **No retries on Smartlead failures.** Single attempt per step; 5xx → fail the run.
- **No `mailbox_groups` writes.** Inbox config goes through `inbox_selection` on each plan.
- **No persistence of `/api/leads/upload` results.** Preview only.
- **No `WebhookEvent.workspace_id` resolution.** The column exists but the handler doesn't fill it.
- **No `created_by` on requests.** Column exists, never set.
- **No `INBOX_SHEET_SCRIPT_URL` integration.** Setting declared, no caller.
- **No SimpleWorker fork-fallback in production.** Worker class must be explicitly `SimpleWorker` on Windows; Linux can use the default.
- **No DB transaction wrappers around HTTP requests.** Multiple `db.commit()` per request, partial state on crash.

---

## 24. Mental model: a campaign's full lifecycle

```
Operator opens /campaigns/new
   ↓ POST /api/campaigns/new (multipart)
       ├─ parse_messaging_file(text) → parsed_messaging
       └─ INSERT campaign_requests (status=drafting, lead_source_type=none)
   ↓ 303 → /campaigns/{id}

Operator clicks "Generate Draft"
   ↓ POST /api/campaigns/{id}/generate-draft
       ├─ build_campaign_plan_from_input(raw_input)
       ├─ validate_campaign_plan(plan, active_workspace_keys)
       ├─ INSERT campaign_drafts (validation_status=valid|invalid)
       └─ UPSERT conversation_sessions (event=generated_draft)

If invalid → operator can click "AI Revise Draft"
   ↓ POST /api/campaigns/{id}/revise-draft (form: revision_instruction)
       ├─ Anthropic call (or short-circuit if key missing)
       ├─ old draft → superseded, new draft inserted
       └─ conversation event appended

If valid → operator clicks "Approve Draft"
   ↓ POST /api/campaigns/{id}/approve
       ├─ draft.validation_status = approved
       └─ request.status = approved

Operator clicks "Sync to Smartlead"
   ↓ POST /api/campaigns/{id}/sync
       ├─ return existing queued/running/succeeded/Smartlead run if one exists
       ├─ otherwise INSERT campaign_runs (run_status=queued, deterministic idempotency_key)
       ├─ request.status = syncing
       └─ rq.Queue("campaign_sync").enqueue(sync_campaign, run_id, timeout=900)

RQ worker picks up the job:
   ↓ sync_campaign(run_id) → asyncio.run(_sync_campaign(run_id))
       run → running, started_at set
       Step 1: create_campaign         → smartlead_campaign_id stored
       Step 2: apply_settings          → 3 sequential POSTs
       Step 3: apply_schedule
       Step 4: push_sequences
       Step 5: create_webhook          (only if APP_BASE_URL is HTTPS)
       Step 6: verify_campaign         (read-back probe)
       run → succeeded, finished_at set, request.status = synced

Operator returns to /campaigns/{id} → sees run row succeeded + Smartlead ID badge

Optional follow-ups:
   POST /api/campaigns/{id}/smartlead/apply   → re-push schedule/settings/sequences + attach mailboxes
   GET  /api/campaigns/{id}/smartlead         → live read
   GET  /api/campaigns/{id}/analytics         → 4 analytics endpoints
   POST /api/campaigns/{id}/smartlead/archive → status=ARCHIVED
   POST /api/campaigns/{id}/smartlead/delete  → DELETE the campaign

Smartlead replies to leads (after manual lead upload + launch):
   POST /api/webhooks/smartlead → INSERT webhook_events
```

That's the whole system end to end.
