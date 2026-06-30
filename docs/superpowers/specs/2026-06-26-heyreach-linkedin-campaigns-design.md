# HeyReach LinkedIn Campaign Creation — Design

**Date:** 2026-06-26
**Status:** Draft for review
**Scope:** Sub-project B of the HeyReach epic — *creation* only. Monitoring is a separate
infra-bot task (see "Out of scope").

## Problem

Precise-automator creates email campaigns in Smartlead. We also run LinkedIn outreach in
HeyReach. We want to create a HeyReach LinkedIn **DRAFT** campaign from the app: an empty
lead list, all available LinkedIn senders attached, and a proven sequence built from the
campaign's LinkedIn message bodies. The campaign manager then adds leads in HeyReach and
starts it. The app returns the HeyReach campaign URL.

## Decisions (locked)

- **No connection note** — `CONNECTION_REQUEST` payload `messages:[""]`, blank.
- **Leads:** create an **empty** USER_LIST; leads are added later in HeyReach. The app does
  not push leads.
- **Senders:** auto-attach **all** LinkedIn accounts in the workspace.
- **Status:** create in **DRAFT** (never auto-start). Return the HeyReach campaign URL.
- **Message text:** reuse the campaign's LinkedIn step bodies (authored in the app, like
  email bodies but no subject).
- **Templates by message count (1/2/3 follow-ups):** the number of LinkedIn message steps
  selects the template. Max 3.
- **Not-accepted path:** wait longer, then END (a `LIKE_POST` interaction with a multi-day
  delay before END), matching HeyReach's own proven template.

## The proven sequence (from a live HeyReach campaign, the basis for our templates)

Real node shapes captured from HeyReach campaign 464590 (`get_campaign_sequence`):

- `CONNECTION_REQUEST` — payload `{ "messages": [""], "fallbackMessage": "", "toBeWithdrawnAfterDays": 25 }`;
  `conditionalNode` = **accepted** path, `unconditionalNode` = **not-accepted** path.
- `MESSAGE` — payload `{ "messages": [text], "fallbackMessage": text }`;
  `conditionalNode` = **replied** exit (MUST be an `END`), `unconditionalNode` = next step.
- `LIKE_POST` — payload `{ "reactionType": "LIKE", "randomReaction": false, "reactBefore": "MONTH1", "skipDelayIfCannotLike": false }`;
  `unconditionalNode` only.
- `VIEW_PROFILE` — no payload; `unconditionalNode` only.
- `CHECK_IS_CONNECTION` — `conditionalNode` = already-connected (true), `unconditionalNode` = not (false).
- `END` — `actionDelay` + `actionDelayUnit` only.

**Delay rules (hard):** `actionDelay >= 3` with `actionDelayUnit` HOUR (or use DAY) on every
node that follows an action node, including END. `0` is only valid on the very first node and
on a MESSAGE/INMAIL reply-exit END. Default everywhere else to `3 HOUR` or a DAY value. Max 500 days.

### Our template shape (per message count N ∈ {1,2,3})

Entry honors the "check connected first" intent, then both branches run the same N-message chain:

```
CHECK_IS_CONNECTION (delay 0)
├─ connected (conditional)      → <message-chain(N)>
└─ not connected (unconditional)→ VIEW_PROFILE (3h) → CONNECTION_REQUEST (3h, blank, withdraw 25d)
                                    ├─ accepted (conditional)       → <message-chain(N)>
                                    └─ not accepted (unconditional) → LIKE_POST (2 DAY) → END (1 DAY)
```

`<message-chain(N)>` — N MESSAGE nodes with an interaction + delays between them:

```
MESSAGE_1 (3h)
  ├─ replied (conditional) → END (0)
  └─ no reply (uncond)     → [if more messages] LIKE_POST (2 DAY) → MESSAGE_2 (3h) → … → END (2 DAY)
                              [else]            END (2 DAY)
```

- 1 follow-up: MESSAGE_1 → END.
- 2 follow-ups: MESSAGE_1 → LIKE_POST → MESSAGE_2 → END.
- 3 follow-ups: MESSAGE_1 → LIKE_POST → MESSAGE_2 → VIEW_PROFILE → MESSAGE_3 → END.

The chain appears twice in the tree (connected branch and accepted branch) — it is a tree,
not a DAG, so the builder emits two independent copies.

## New module: `app/services/heyreach_sequence_builder.py`

Pure logic, no I/O.

- `build_linkedin_sequence(messages: list[str], *, withdraw_days: int = 25) -> dict`
  - `messages`: 1–3 already-final message bodies (app merge tags). Raises if empty or > 3.
  - Returns the full node-tree dict (ready to `json.dumps` into `sequenceJson`).
  - Translates merge tags app→HeyReach and builds `fallbackMessage` for every MESSAGE.
- `to_heyreach_message(body: str) -> tuple[str, str]` → `(message, fallbackMessage)`.
  - Merge-tag map: `{{first_name}}`→`{FIRST_NAME}`, `{{company}}`/`{{company_name}}`→`{COMPANY}`.
  - `fallbackMessage`: same text with personalization neutralized — `{FIRST_NAME}`→`there`
    (e.g. "Hey there,"), `{COMPANY}`→`your company`. (HeyReach uses the fallback when a field
    is missing.)
  - Strips `%signature%` (LinkedIn messages carry no signature token).
- `_message_chain(messages, idx) -> dict` — recursive builder for `<message-chain(N)>`.

All delays default to `3 HOUR`; inter-message interactions use `2 DAY`. Every path ends in END.

## Plan model: LinkedIn steps

`SequenceStep` gains `channel: Literal["email", "linkedin"] = "email"`.

- LinkedIn steps: `subject` ignored/empty, `body` is the message text. `delay_days` ignored
  (HeyReach delays are template-fixed in MVP).
- `linkedin_messages(plan) -> list[str]` helper: bodies of `channel=="linkedin"` steps, in
  `step_number` order. Its length (1–3) selects the template.
- Validation: a campaign may have email steps, LinkedIn steps, or both. LinkedIn steps must
  have a non-empty body; >3 LinkedIn steps is an error (templates cap at 3).

## Config

- `HEYREACH_API_KEY` per workspace — add `heyreach_api_key_env` to each `SMARTLEAD_WORKSPACES`
  entry (e.g. `HEYREACH_PRECISELEAD_API_KEY`); resolve in `get_workspace_config`. `.env.example`
  + `render.yaml` get the new keys (`sync: false`).
- A workspace without a HeyReach key → the "Create LinkedIn campaign" action errors clearly.

## Smartlead-style client: `app/services/heyreach_service.py`

`HeyReachService(api_key)` over `https://api.heyreach.io/api/public`, header `X-API-KEY`.
(Exact endpoint paths confirmed against HeyReach's public API / the MCP contract at
implementation; the MCP tool names map 1:1 to public endpoints.)

- `async def get_linkedin_accounts(limit=100, offset=0) -> dict` — list senders (paginate).
- `async def create_empty_list(name: str) -> dict` — create a USER_LIST → returns list id.
- `async def create_campaign(name, list_id, account_ids, sequence, schedule=None) -> dict`
  — POST create (DRAFT); `sequence` is the dict from the builder, serialized to `sequenceJson`.
- `def campaign_url(campaign_id) -> str` — `https://app.heyreach.io/...campaign/{id}`.

Read-only helper for future linking/monitoring is **out of scope** here.

## Creation worker: `app/workers/heyreach_create.py`

Mirrors `sync_campaign`. Background task triggered by a button.

`create_heyreach_campaign_now(campaign_id)` → `_create_async`:
1. Load doc; collect `linkedin_messages(plan)`. If none → error "no LinkedIn steps".
2. Resolve workspace HeyReach key. Missing → error.
3. `get_linkedin_accounts` → all account ids (auto-attach). None → error "no LinkedIn senders".
4. `create_empty_list(campaign_name)` → list id.
5. `build_linkedin_sequence(messages)` → tree → `create_campaign(name, list_id, account_ids,
   sequence)` (DRAFT). Extract HeyReach campaign id.
6. Persist `heyreach_campaign_id` + `heyreach_campaign_url` + `heyreach_status="draft_created"`
   on the doc (new store fields + helper). On failure store `heyreach_last_error`.

Idempotency: if `heyreach_campaign_id` already set, the action warns (creating again makes a
second HeyReach campaign) — require an explicit re-create, or just create a new one and update
the stored id. MVP: create a new one each click and overwrite the stored id (simple; HeyReach
campaigns are cheap drafts). Surface the count in the summary.

## Routes + UI

- `POST /api/campaigns/{id}/heyreach-create` — schedules the background task; rejects if no
  LinkedIn steps. Sets `heyreach_creating=True` (progress flag, same pattern as twin fix).
- Detail page **"LinkedIn (HeyReach)"** panel:
  - Up to 3 message textareas (these persist as `channel="linkedin"` sequence steps via a
    small save route, reusing the existing sequence-edit pattern), labelled Message 1–3.
  - "Create LinkedIn campaign" button → runs the worker; spinner + auto-refresh while
    `heyreach_creating` (reuse the twin-fix poller pattern via the status endpoint).
  - After creation: a link to the HeyReach campaign URL + "Created in HeyReach (DRAFT) — add
    leads and start it there."
- Status endpoint gains `heyreach_creating`, `heyreach_campaign_url`.

## Testing (TDD)

`tests/test_heyreach_sequence_builder.py`:
- 1/2/3-message trees: correct node types, branching (CHECK_IS_CONNECTION + CONNECTION_REQUEST
  both lead to the chain), every path ends in END, all post-action delays ≥ 3h.
- Merge-tag translation: `{{first_name}}`→`{FIRST_NAME}`, `{{company_name}}`/`{{company}}`→`{COMPANY}`;
  fallback neutralizes them; `%signature%` stripped.
- Empty messages → raises; > 3 → raises.
- `CONNECTION_REQUEST` blank note + `toBeWithdrawnAfterDays`; not-accepted path = LIKE_POST→END.

`tests/test_heyreach_service.py` (mocked httpx, like test_smartlead_service):
- `get_linkedin_accounts` / `create_empty_list` / `create_campaign` hit the right endpoints
  with the right payloads (sequence serialized to `sequenceJson`, DRAFT).

`tests/test_heyreach_create.py` (worker, mocked HeyReachService):
- Builds list → sequence → campaign; persists id+url; auto-attaches all account ids.
- No LinkedIn steps → error in summary, no calls.
- No senders / no key → error, no campaign created.

`tests/test_campaign_routes.py` additions:
- heyreach-create route rejects when no LinkedIn steps; sets creating flag; persists nothing
  destructive; Smartlead/HeyReach mocked (no live calls).

## Out of scope (YAGNI / other tasks)

- **Monitoring / analytics** — client-level cross-platform dashboard (leads added, connections
  accepted, responses, positive/negative, daily). Lives in **infra-bot** with the Smartlead
  inbox logic; specced separately from the provided dashboard screenshot. HeyReach data via
  `get_overall_stats(campaignIds, dates)` + its `byDayStats`.
- Pushing leads from the app (empty list by decision).
- Auto-starting the HeyReach campaign (DRAFT only).
- Per-campaign custom delays/interactions beyond the three templates.
- Linking an already-existing HeyReach campaign for monitoring (that's the monitoring task).
