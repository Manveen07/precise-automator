# Twin Campaigns + Twain Spacing Fix — Design

**Date:** 2026-06-25
**Status:** Approved for planning

## Problem

Some campaigns are "Twain-personalized": the email content is not authored in our app.
Instead the sequence body is a fixed template referencing per-lead custom variables
(`{{Subject 1}}`, `{{Step 1}}`, `{{Step 3}}`) that an external tool (Twain) fills in.
Twain's output frequently carries a spacing bug — lone `<br>` between paragraphs (tight
line, no gap), runs of 3+ `<br>`, or spaces around `<br>` — which renders incorrectly in
Smartlead plain-text mode.

**Hard-won lesson (verified live, June 2026):** Twain's per-lead field values are
**`<br>`-based, not `\n`-based** — Twain pushes literal `<br>` tags into the custom fields.
An earlier audit that only checked raw `\n` reported "0 flagged" while 285 leads still had
lone `<br>`. **The normalizer and audit MUST operate on `<br>` patterns** (with `\n` handled
as a fallback, since a minority of leads also carry raw newlines).

There are two independent spacing layers, each able to carry the bug — inspect both:

| Layer | Example | Set by | Break char |
|---|---|---|---|
| Sequence template | `Hi {{first_name}},<br><br>{{Step 1}}<br><br>%signature%` | our campaign build | `<br>` |
| Per-lead field value | the text filling `{{Step 1}}` / `{{Step 3}}` | Twain | `<br>` |

We need two capabilities:

1. **Create / mark a twin campaign** — a campaign whose sequence is the fixed Twain
   template, with no app-authored body.
2. **Fix the spacing** of each lead's `Subject 1` / `Step 1` / `Step 3` custom-field
   values on a twin campaign (resolved by linked Smartlead id or a pasted Smartlead URL).

## The twin sequence template

A two-email sequence. Custom variables are filled by Twain per lead; subject may be
empty on some leads.

- **Email 1** — step 1, delay 0 days
  - subject: `{{Subject 1}}`
  - body: `Hi {{first_name}},\n\n{{Step 1}}\n\n%signature%`
- **Email 2** — step 2, delay 3 days (default)
  - subject: `""` (threaded reply)
  - body: `{{Step 3}}\n\n%signature%`

Rules that hold by construction:
- `{{Step 1}}` must NOT contain a greeting (template supplies `Hi {{first_name}},`).
- `{{Step 3}}` DOES start with its own `Hi {First},<br><br>` greeting.
- No signature inside the custom fields (template appends `%signature%`).

**Template join must be `<br><br>`.** The Step 1 greeting join (`Hi {{first_name}},<br><br>{{Step 1}}`)
has been observed reverting to a lone `<br>` after a later sync/rebuild. Whatever rebuilds
the campaign must keep every join as `<br><br>`, and the template must be re-verified after
any re-sync. Our app authors the body as `Hi {{first_name}},\n\n{{Step 1}}\n\n%signature%`
and relies on `format_email_body_for_smartlead` to emit `<br><br>`; the sync verification
step must **GET the pushed sequence back and hard-fail / mark the sync failed (not merely
log) if any join is a lone `<br>` instead of `<br><br>`.** Soft logging is insufficient —
this join has silently reverted before and was only caught by a screenshot.

## Entry points

1. **Create at campaign creation** — a checkbox on the new-campaign form:
   *"Twin campaign (Twain-personalized)"*. When checked, the messaging parse step is
   skipped and the fixed twin sequence is injected. The campaign doc is flagged
   `is_twin: true`. Available for any workspace.
2. **Mark an existing campaign as twin** — a control on the campaign detail page with an
   optional Smartlead URL field. Sets `is_twin: true` and stores `twin_smartlead_url`.
3. **Fix trigger** — a *"Fix Twain spacing"* button shown only on twin campaigns. Resolves
   the target Smartlead campaign id as: **pasted URL first, else the linked
   `smartlead_campaign_id`.** Errors clearly if neither is available.

## Data model

Campaign Mongo doc gains:
- `is_twin: bool` (default false)
- `twin_smartlead_url: str | None`
- `twin_last_fix: dict | None` — summary of the most recent fix run:
  `{ "ran_at", "campaign_id", "total_leads", "leads_changed", "field_counts",
     "greeting_flags": [...], "errors": [...] }`

`is_twin` is also surfaced on the plan/detail view for the UI badge and button gating.

## The fix (background worker, mirrors the existing sync worker)

1. Resolve `campaign_id`: parse `email-campaign/(\d+)` from `twin_smartlead_url` if set,
   else use `smartlead_campaign_id`. Resolve the workspace API key from the doc's
   `smartlead_workspace`.
2. Paginate `get_leads(campaign_id, limit=100, offset=...)` until all leads fetched.
3. **Skip leads with no email** — they can't be updated or sent.
4. Per lead, read custom fields `Subject 1`, `Step 1`, `Step 3` (only keys present).
5. **Normalize `<br>` spacing** (see normalizer rules). Idempotent; never alters wording.
6. **Flag (do not edit) greeting-content issues:** Step 1 begins with a greeting, or Step 3
   missing a `Hi …,` opener. (Note: a *tight* greeting like `Hi X,<br>` is auto-fixed by the
   lone-`<br>` rule — only greeting presence/absence is flagged, not spacing.)
7. Write back **per-lead** via `update_lead(campaign_id, lead_id, email,
   custom_fields={changed keys})` — the bulk upsert silently skips custom-field overwrites,
   so the per-lead endpoint `POST /campaigns/{id}/leads/{lead_id}` (with `email` in the body)
   is mandatory. Write **only leads whose values actually changed**.
8. **Audit after writing:** re-scan (or scan the post-normalize values) with the `<br>`
   audit regexes; expect 0 defects. Record any residual defects in the summary.
9. **Re-check the template too, not just lead fields.** GET the campaign sequence; if the
   Step 1 greeting join (or any join) is a lone `<br>` instead of `<br><br>`, repush the
   corrected twin sequence. The visible bug is usually in BOTH layers — fixing only the
   field data leaves a reverted template greeting tight (the Iman case). Record template
   repush in the summary.
10. Persist the run summary to `twin_last_fix`; render it on the campaign detail page.

Execution: triggered by a button, runs as a FastAPI BackgroundTask (same pattern as
`sync_campaign_now`). The detail page shows the summary once the run completes.

## New module: `app/services/twain_service.py`

Isolated, pure-logic where possible:

- `normalize_twain_field(value: str, *, is_subject: bool = False) -> str`
  - Body fields (`<br>`-primary):
    - normalize `<br>` tag variants (`<br/>`, `<br />`, case-insensitive) to literal `<br>`;
    - strip spaces/tabs around every `<br>`;
    - collapse runs of 3+ `<br>` to `<br><br>`;
    - promote lone `<br>` (not adjacent to another `<br>`) to `<br><br>`;
    - raw-newline fallback: strip trailing spaces before a newline, collapse 3+ consecutive newlines to two, and treat a lone raw newline the same as a lone `<br>`;
    - strip BOM/zero-width; normalize unicode whitespace (nbsp, line/para separators);
    - strip leading/trailing breaks.
  - Subject (`is_subject=True`): strip all `<br>`, flatten whitespace to single spaces, trim.
  - Pure, idempotent (re-running yields the same string), no wording changes.
- `audit_twain_field(value: str) -> list[str]` — returns names of spacing defects found
  using the audit regexes below. Used to assert 0 defects post-fix.
- `twain_sequence_plan(followup_delay_days: int = 3) -> list[dict]` — the fixed template
  in the app's plan `SequenceStep` shape.
- `flag_greeting_issues(step1: str | None, step3: str | None) -> list[str]` — returns
  human-readable flags (Step 1 has a greeting / Step 3 missing one); does not mutate.

### Audit regexes (shared by `audit_twain_field`)

```
lone <br>:              (?i)(?<!<br>)<br>(?!<br>)
triple+ <br>:           (?i)(?:<br>){3,}
space before <br>:      [ \t]<br>
lone \n:                (?<!\n)\n(?!\n)
triple+ \n:             \n{3,}
trailing space + \n:    [ \t]+\n
Step 3 tight greeting:  (?i)Hi [^,]+,<br>(?!<br>)   OR   (?i)Hi [^,]+,\n(?!\n)
```

Healthy campaign: 0 matches across all sendable leads.

Shared low-level helpers (unicode cleanup, soft-break/sentence repair) currently inside
`sequence_builder.py` are refactored into reusable functions so both modules use them —
no duplicated logic.

## Smartlead service additions (`app/services/smartlead_service.py`)

- `async def get_leads(campaign_id, limit=100, offset=0) -> dict`
  → GET `campaigns/{id}/leads?limit=&offset=` (returns `data[].lead.custom_fields`).
- `async def update_lead(campaign_id, lead_id, email, custom_fields) -> dict`
  → POST `campaigns/{campaign_id}/leads/{lead_id}` with `{email, custom_fields}` — the
  **per-lead** endpoint (matches the MCP `update_campaign_lead` contract: campaign_id +
  lead_id + email + custom_fields). Do NOT use the bulk `add_leads` upsert for fixes — it
  silently skips custom-field overwrites on existing leads (reports success, applies nothing).
  **`email` is required in the request body even though `lead_id` is in the URL** — omitting
  it returns 400 `"email" is required`. Docstring this so the redundant-looking field is not
  "cleaned up" later.

## Validation

`validation_service.ALLOWED_MERGE_TAGS` must accept the twin custom-field tags
`{{Subject 1}}`, `{{Step 1}}`, `{{Step 3}}` so twin sequences validate cleanly. Scope the
allowance to twin campaigns (e.g. pass an `is_twin` flag into validation, or extend the
allow-list) so non-twin campaigns are unaffected. Confirm `format_email_body_for_smartlead`
preserves multi-word merge tags (a space inside `{{Step 1}}`) without mangling.

## UI

`campaign_detail.html`:
- A **Twin** badge when `is_twin`.
- A *"Mark as twin"* form (toggle + optional Smartlead URL) for non-twin campaigns.
- A *"Fix Twain spacing"* button for twin campaigns.
- A `twin_last_fix` summary block: leads changed, per-field counts, greeting flags, errors.

`campaign_new.html`:
- A *"Twin campaign (Twain-personalized)"* checkbox; when checked, messaging upload/paste
  is optional/ignored and the fixed twin sequence is injected.

Styling matches existing `detail-actions` / `form-card` patterns. Keep it minimal.

## Testing (TDD — write tests first)

`tests/test_twain_service.py`:
- Normalizer (`<br>`-primary): lone `<br>` -> `<br><br>`; `<br>{3,}` -> `<br><br>`; spaces
  around `<br>` stripped; self-closing `<br>` variants normalized; existing `<br><br>` left
  intact; raw-newline fallback (lone newline, 3+ newlines, trailing-space newline); BOM/nbsp
  cleaned; wording never changed; subject stripped of `<br>` and flattened to one line;
  idempotency (normalize(normalize(x)) == normalize(x)).
- **Paired contract (most valuable test):** `audit_twain_field(normalize_twain_field(x)) == []`
  for EVERY fixture (dirty and clean). Proves the normalizer and audit agree — the exact
  drift that bit us live (audit said 0 while leads were still broken).
- `audit_twain_field`: detects each defect class; a clean `<br><br>`-only field returns [].
- `flag_greeting_issues`: Step 1 with greeting flagged; Step 3 without greeting flagged;
  clean inputs produce no flags.
- `twain_sequence_plan`: two steps, delays 0 and 3, correct subjects/bodies/merge tags,
  joins emit `<br><br>` after the formatter.

`tests/test_campaign_routes.py` (additions):
- Create-twin injects the twin template and sets `is_twin`.
- Mark-as-twin persists `is_twin` + `twin_smartlead_url`.
- Fix resolves URL over linked id; falls back to linked id when no URL.
- Fix calls `update_lead` only for leads whose values changed.
- Fix summary reports counts and greeting flags.
- Fix on a non-twin campaign is rejected.
- Smartlead mocked (no live calls).

## Out of scope (YAGNI)

- Auto-editing greetings (only flagged, per decision).
- Creating the paired Smartlead campaign automatically at our-app creation time beyond
  injecting the sequence — sync to Smartlead uses the existing sync flow.
- Cross-workspace URL fixing (fix uses the doc's own workspace API key).
