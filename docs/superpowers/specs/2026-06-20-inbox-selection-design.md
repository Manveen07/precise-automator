# Inbox Selection — Design Spec

**Date:** 2026-06-20
**Status:** Approved, ready for implementation

## Problem

Campaigns sync to Smartlead with **no email accounts attached** (`inbox_selection.mode`
is always `"skip"`). Operators pick inboxes manually in Smartlead. The team already
maintains a deliverability/availability source of truth (a Google Sheet, exposed via an
Apps Script Web App) and a selection algorithm. We need to surface that in the app: an
intuitive UI that recommends and attaches the right inboxes for a campaign's client.

## Data source

Google Apps Script Web App (runs as the sheet owner; works server-side on Render with no
Google creds in our app):

```
GET {INBOX_SHEET_WEBAPP_URL}?sheet={tab}&action=read
```

Returns a JSON 2D array. Row 0 = headers. The **"All Inboxes"** tab returns 404 inbox
rows with these columns (mapped **by header name**, not index, for resilience):

`Client, Email, Name, Provider, Account ID, Availability, Busy Reason, # Campaigns,
Connection OK, Max/Day, Sent Today, Capacity Left, True Load, Avail. Capacity,
Warmup State, Warmup Rep %, Warmup Max, Last Active, Test Status, Test Date`

Three clients present: `Belardi Wong`, `DARLEAN`, `PRECISE_LEADS`. These map to the app's
three workspaces.

## Selection algorithm (pure function)

`select_inboxes(rows, client, needed_daily_volume) -> SelectionResult`

1. **Filter eligibility** (all must pass):
   - `Client == client`
   - `Availability == FREE`
   - `Warmup Rep %` ≥ 90 (strip `%`)
   - `Test Status == inbox`
   - `Avail. Capacity` > 0
2. **Dedup by Account ID** — an inbox appears once per assigned campaign; keep the row with
   the **lowest** `Avail. Capacity` (conservative, worst-case load).
3. **Size the pick:** `avg = mean(Avail. Capacity of eligible)`,
   `inboxes_needed = ceil(needed_daily_volume / avg)`, capped at the pool size.
4. **Rank:** unassigned inboxes (`# Campaigns == 0`) first, then highest `Avail. Capacity`;
   tiebreak `Warmup State` ramped > warming, then fresher `Test Date`. Take top N.

Returns:
- `recommended`: picked rows (account_id, email, provider, capacity, warmup, test_date)
- `email_account_ids`: picked account IDs
- `free_pool`: all eligible rows (for manual add beyond the recommendation)
- `busy`: excluded rows for this client grouped by `Busy Reason` (diagnostics)
- `estimated_daily_capacity`, `provider_counts {gmail, outlook}`
- `shortfall`: true if total eligible capacity < needed volume

`needed_daily_volume` comes from the plan's `schedule.max_new_leads_per_day` (default 100).

## Modules

- **`app/services/inbox_sheet_service.py`** — IO only. `fetch_inbox_rows(tab)` calls the Web
  App, parses the 2D array into `list[dict]` keyed by header, coerces numeric fields. 5-minute
  in-process cache (avoid refetch per page load). Raises a typed error on non-JSON / HTTP
  failure so callers can degrade gracefully.
- **`app/services/inbox_selection_service.py`** — pure logic, no IO. The algorithm above.
  Replaces the divergent algorithm currently inline in `routes/inboxes.py`.
- **`app/config.py`** — `INBOX_SHEET_WEBAPP_URL` (env), `INBOX_SHEET_TAB` default
  `"All Inboxes"`, and a `workspace_key -> sheet client name` map.

## Routes

- `GET /api/campaigns/{id}/inboxes` — resolve client from the campaign's workspace, volume
  from the plan; fetch rows, run selection, return JSON (`recommended`, `free_pool`, `busy`,
  summary). On sheet-fetch failure, return a structured error the UI shows without blocking.
- `POST /api/campaigns/{id}/inbox-selection` — form `account_ids` (repeated). Writes
  `plan.inbox_selection = {mode: "manual_ids", email_account_ids: [...]}`. Persists to the
  doc. Existing sync (`workers/sync_campaign.py`) already attaches these to Smartlead.

## UI — "Inboxes" panel on `campaign_detail.html`

- **Summary band:** needed volume vs selected capacity (progress bar), Gmail/Outlook chips,
  count selected.
- **Recommended** (pre-checked): email, provider badge, capacity-left bar, warmup state,
  test date, checkbox to add/remove.
- **▸ More FREE inboxes** (collapsed): rest of the eligible pool, unchecked, to add manually.
- **▸ BUSY / excluded** (collapsed): grouped by `Busy Reason` (`stale_test → re-test`,
  `no_capacity`, `disconnected`, …) so the operator sees why the pool is small and what to fix.
- **Apply selection** → POST → confirmation. Done before Sync.
- Vanilla `fetch`, matching the page's existing JS style. Lives in its own panel (not crammed
  into Plan Preview).

## Safety

- **Cross-client guard:** only the campaign's client is shown and attachable (sheet's `Client`
  column == Smartlead account; never attach across clients).
- **Graceful degradation:** sheet fetch failure shows an inline error and lets the operator
  proceed (selection stays `skip`); never blocks sync.
- Selection is a draft step; final review still happens in Smartlead.

## Testing (TDD)

- `inbox_selection_service`: pure unit tests — eligibility filter, dedup-by-account
  (lowest capacity), `ceil` sizing, ranking/tiebreak, shortfall flag, provider split,
  cross-client exclusion.
- `inbox_sheet_service`: parse a sample 2D-array fixture (no network); fetch error path
  (mocked) raises typed error.
- Routes: `GET` returns recommendation for the right client; `POST` persists IDs;
  cross-client account rejected.

## YAGNI / out of scope

- No Gmail/Outlook ratio **enforcement** (show the split, don't enforce).
- No live Smartlead warmup fetch (the sheet has it).
- No Zapmail auto-purchase trigger (separate backlog item).
- No edit of the sheet from the app (read-only).
