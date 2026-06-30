# Twin Campaigns + Twain Spacing Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add "twin" campaigns (fixed Twain-personalized sequence) and a background fix that normalizes each lead's `Subject 1` / `Step 1` / `Step 3` custom-field spacing on a twin campaign, resolved by linked Smartlead id or a pasted URL.

**Architecture:** A new pure-logic `twain_service` (normalizer + audit + greeting flags + fixed sequence template). Two new `SmartleadService` methods (`get_leads`, `update_lead` via the per-lead endpoint). A new background worker `twin_fix` mirroring the existing `sync_campaign` worker. Mongo doc gains `is_twin` / `twin_smartlead_url` / `twin_last_fix`. Routes for create-twin, mark-as-twin, and run-fix. The sync worker gains a hard-fail check on the Step 1 `<br><br>` join.

**Tech Stack:** Python 3.10+, FastAPI, Jinja2, pymongo (mongomock in tests), httpx, pytest. Tests run from `precise-automator/` with `PYTHONPATH=. .venv/Scripts/python.exe -m pytest`.

## Global Constraints

- The normalizer/audit are `<br>`-primary (Twain pushes literal `<br>`, not `\n`); raw newlines are a fallback only. Verbatim lesson: an audit that only checked `\n` reported "0 flagged" while 285 leads were broken.
- The fix MUST write via the per-lead endpoint `POST campaigns/{campaign_id}/leads/{lead_id}` with `email` in the body. `email` is required even though `lead_id` is in the URL (omitting → 400 `"email" is required`). The bulk `add_leads` upsert silently skips custom-field overwrites — never use it for fixes.
- Skip leads with no email (can't update or send).
- Write back only leads whose values actually changed.
- The twin Step 1 template join must be `<br><br>`; the sync worker must hard-fail (mark sync failed), not just log, if it reverts to a lone `<br>`.
- The fix run re-checks BOTH layers: lead fields AND the campaign template join.
- Paired contract: `audit_twain_field(normalize_twain_field(x)) == []` for every input.
- TDD: write the failing test first, watch it fail, implement minimally, watch it pass, commit. Frequent commits.
- Tests must mock Smartlead — no live API calls.

---

### Task 1: `twain_service` (normalizer, audit, greeting flags, sequence template)

Pure logic, no I/O. The test file and a reference implementation were supplied by the product owner and are reproduced verbatim below — they ARE the contract.

**Files:**
- Create: `app/services/twain_service.py`
- Test: `tests/test_twain_service.py`

**Interfaces:**
- Produces:
  - `normalize_twain_field(value: str, *, is_subject: bool = False) -> str`
  - `audit_twain_field(value: str) -> list[str]`
  - `flag_greeting_issues(step1: str | None, step3: str | None) -> list[str]`
  - `twain_sequence_plan(followup_delay_days: int = 3) -> list[dict]`

- [ ] **Step 1: Write the failing test** — create `tests/test_twain_service.py` with exactly this content:

```python
"""Tests for app.services.twain_service.

The central contract this file locks: the normalizer and the audit AGREE —
audit_twain_field(normalize_twain_field(x)) == [] for every input.
"""

import pytest

from app.services.twain_service import (
    audit_twain_field,
    flag_greeting_issues,
    normalize_twain_field,
    twain_sequence_plan,
)

DIRTY_BODIES = [
    "A<br>B",
    "A<br><br><br>B",
    "A<br><br><br><br>B",
    "A <br> B",
    "A\t<br>\tB",
    "A<br/>B",
    "A<br />B",
    "A<BR>B",
    "A\nB",
    "A\n\n\nB",
    "A  \nB",
    "Para1<br>Para2<br><br>Para3<br>Para4",
    " A<br>B​",
    "<br>A<br><br>B<br>",
]

CLEAN_BODIES = [
    "A<br><br>B",
    "A<br><br>B<br><br>C",
    "Single paragraph, no breaks.",
    "Hi Mark,<br><br>That close had moving parts.<br><br>Worth a look?",
]


@pytest.mark.parametrize("dirty", DIRTY_BODIES)
def test_normalize_produces_clean_field(dirty):
    assert audit_twain_field(normalize_twain_field(dirty)) == []


@pytest.mark.parametrize("clean", CLEAN_BODIES)
def test_normalize_leaves_clean_fields_clean(clean):
    assert audit_twain_field(normalize_twain_field(clean)) == []


def test_lone_br_promoted_to_double():
    assert normalize_twain_field("A<br>B") == "A<br><br>B"


def test_triple_br_collapsed():
    assert normalize_twain_field("A<br><br><br>B") == "A<br><br>B"


def test_spaces_around_br_stripped():
    assert normalize_twain_field("A <br> B") == "A<br><br>B"


def test_self_closing_br_variants_normalized():
    assert normalize_twain_field("A<br/>B") == "A<br><br>B"
    assert normalize_twain_field("A<br />B") == "A<br><br>B"


def test_existing_double_br_left_intact():
    assert normalize_twain_field("A<br><br>B") == "A<br><br>B"


def test_raw_newline_fallback():
    assert normalize_twain_field("A\nB") == "A\n\nB"
    assert normalize_twain_field("A\n\n\nB") == "A\n\nB"
    assert normalize_twain_field("A  \nB") == "A\n\nB"


def test_unicode_and_bom_cleaned():
    out = normalize_twain_field("﻿A<br>B​")
    assert "﻿" not in out and "​" not in out


def test_leading_and_trailing_breaks_stripped():
    assert normalize_twain_field("<br>A<br><br>B<br>") == "A<br><br>B"


def test_wording_never_changed():
    body = "Running 2,000+ events, you feel coordination friction.<br>How are you?"
    out = normalize_twain_field(body)
    for token in ["Running 2,000+ events", "coordination friction", "How are you?"]:
        assert token in out


@pytest.mark.parametrize("case", DIRTY_BODIES + CLEAN_BODIES)
def test_idempotent(case):
    once = normalize_twain_field(case)
    assert normalize_twain_field(once) == once


def test_empty_and_none_safe():
    assert normalize_twain_field("") == ""
    assert normalize_twain_field("   ") == "   "
    assert normalize_twain_field(None) is None  # type: ignore[arg-type]


def test_subject_strips_br_and_flattens():
    assert normalize_twain_field("Event<br>coordination", is_subject=True) == "Event coordination"


def test_subject_collapses_whitespace():
    assert normalize_twain_field("  Event   coordination  ", is_subject=True) == "Event coordination"


def test_audit_detects_each_defect_class():
    assert "lone_br" in audit_twain_field("A<br>B")
    assert "triple_br" in audit_twain_field("A<br><br><br>B")
    assert "space_before_br" in audit_twain_field("A <br>B")
    assert "lone_nl" in audit_twain_field("A\nB")
    assert "triple_nl" in audit_twain_field("A\n\n\nB")
    assert "trailing_space_nl" in audit_twain_field("A \nB")


def test_audit_clean_field_returns_empty():
    assert audit_twain_field("A<br><br>B") == []


def test_audit_empty_returns_empty():
    assert audit_twain_field("") == []


def test_step1_with_greeting_flagged():
    assert "step1_has_greeting" in flag_greeting_issues("Hi Mark,<br><br>Body", "Hi Mark,<br><br>Body")


def test_step3_missing_greeting_flagged():
    assert "step3_missing_greeting" in flag_greeting_issues("Body only", "No greeting here")


def test_clean_greetings_no_flags():
    flags = flag_greeting_issues("You have a lot happening...", "Hi Mark,<br><br>Follow up")
    assert flags == []


def test_tight_greeting_spacing_not_flagged_as_content():
    assert "step3_missing_greeting" not in flag_greeting_issues(None, "Hi Mark,<br>Body")


def test_twain_sequence_plan_shape():
    plan = twain_sequence_plan()
    assert len(plan) == 2
    assert plan[0]["step_number"] == 1 and plan[0]["delay_days"] == 0
    assert plan[1]["step_number"] == 2 and plan[1]["delay_days"] == 3
    assert plan[0]["variants"][0]["subject"] == "{{Subject 1}}"
    assert plan[1]["variants"][0]["subject"] == ""
    assert "{{Step 1}}" in plan[0]["variants"][0]["body"]
    assert "{{Step 3}}" in plan[1]["variants"][0]["body"]
    assert "%signature%" in plan[0]["variants"][0]["body"]


def test_twain_sequence_plan_custom_delay():
    plan = twain_sequence_plan(followup_delay_days=5)
    assert plan[1]["delay_days"] == 5


def test_twain_sequence_plan_bodies_emit_double_br_after_formatter():
    from app.services.sequence_builder import format_email_body_for_smartlead
    plan = twain_sequence_plan()
    step1 = format_email_body_for_smartlead(plan[0]["variants"][0]["body"])
    assert "Hi {{first_name}},<br><br>{{Step 1}}<br><br>%signature%" == step1
    assert audit_twain_field(step1) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_twain_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.twain_service'`.

- [ ] **Step 3: Write the implementation** — create `app/services/twain_service.py` with exactly this content:

```python
"""Twain twin-campaign helpers: <br>-primary spacing normalizer, audit, greeting
flags, and the fixed twin sequence template.

Contract (locked by tests/test_twain_service.py):
    audit_twain_field(normalize_twain_field(x)) == []  for every input.
"""

import re

_AUDIT_PATTERNS = {
    "lone_br":        re.compile(r"(?i)(?<!<br>)<br>(?!<br>)"),
    "triple_br":      re.compile(r"(?i)(?:<br>){3,}"),
    "space_before_br": re.compile(r"[ \t]<br>"),
    "lone_nl":        re.compile(r"(?<!\n)\n(?!\n)"),
    "triple_nl":      re.compile(r"\n{3,}"),
    "trailing_space_nl": re.compile(r"[ \t]+\n"),
}

_GREETING_ANY = re.compile(r"(?i)^Hi [^,]+,")


def _clean_unicode(text: str) -> str:
    text = re.sub(r"[​-‍﻿]", "", text)   # zero-width + BOM
    text = text.replace(" ", " ")                     # nbsp -> space
    text = text.replace(" ", "
").replace(" ", "

")  # line/para sep
    return text


def normalize_twain_field(value: str, *, is_subject: bool = False) -> str:
    """Idempotent spacing normalizer. Never alters wording.

    Body fields are <br>-primary (Twain's actual format), with a raw-newline
    fallback. Subjects are flattened to a single line.
    """
    if not isinstance(value, str) or not value.strip():
        return value

    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _clean_unicode(text)

    if is_subject:
        text = re.sub(r"(?i)<br\s*/?>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    text = re.sub(r"(?i)<br\s*/?>", "<br>", text)          # standardize tag spelling
    text = re.sub(r"[ \t]*<br>[ \t]*", "<br>", text)        # strip spaces around <br>
    text = re.sub(r"(?:<br>){3,}", "<br><br>", text)        # 3+ -> 2
    text = re.sub(r"(?<!<br>)<br>(?!<br>)", "<br><br>", text)  # lone -> double
    text = re.sub(r"(?:<br>){3,}", "<br><br>", text)        # re-collapse if overshot

    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", "\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    text = re.sub(r"^(?:<br>|\n|\s)+", "", text)
    text = re.sub(r"(?:<br>|\n|\s)+$", "", text)
    return text


def audit_twain_field(value: str) -> list[str]:
    """Return names of spacing defects found. Clean field -> []."""
    if not isinstance(value, str) or not value.strip():
        return []
    return [name for name, pat in _AUDIT_PATTERNS.items() if pat.search(value)]


def flag_greeting_issues(step1: str | None, step3: str | None) -> list[str]:
    """Flag (do not edit) greeting-CONTENT issues.

    Step 1 should have NO greeting (template supplies it); Step 3 SHOULD start
    with 'Hi X,'. Tight greeting SPACING is auto-fixed by the normalizer, so it
    is not flagged here — only presence/absence.
    """
    flags: list[str] = []
    if isinstance(step1, str) and _GREETING_ANY.match(step1.strip()):
        flags.append("step1_has_greeting")
    if isinstance(step3, str) and step3.strip() and not _GREETING_ANY.match(step3.strip()):
        flags.append("step3_missing_greeting")
    return flags


def twain_sequence_plan(followup_delay_days: int = 3) -> list[dict]:
    """The fixed twin template in the app's plan SequenceStep shape.

    Bodies are authored with \\n\\n; format_email_body_for_smartlead emits
    <br><br> at push time.
    """
    return [
        {
            "step_number": 1,
            "delay_days": 0,
            "variants": [
                {
                    "variant_label": "A",
                    "subject": "{{Subject 1}}",
                    "body": "Hi {{first_name}},\n\n{{Step 1}}\n\n%signature%",
                }
            ],
        },
        {
            "step_number": 2,
            "delay_days": followup_delay_days,
            "variants": [
                {
                    "variant_label": "A",
                    "subject": "",
                    "body": "{{Step 3}}\n\n%signature%",
                }
            ],
        },
    ]
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_twain_service.py -q`
Expected: PASS (all tests green). If the paired-contract test (`test_normalize_produces_clean_field`) fails for any fixture, the normalizer and audit have drifted — fix the normalizer until `audit(normalize(x)) == []` holds for every fixture; do NOT weaken the audit.

- [ ] **Step 5: Commit**

```bash
git add tests/test_twain_service.py app/services/twain_service.py
git commit -m "feat: add twain_service (br-primary normalizer, audit, greeting flags, sequence template)"
```

---

### Task 2: SmartleadService `get_leads` + `update_lead`

**Files:**
- Modify: `app/services/smartlead_service.py` (append two methods after `get_sequences`, ~line 162)
- Test: `tests/test_smartlead_service.py`

**Interfaces:**
- Consumes: `SmartleadService.url()`, `.get()`, `.post()` (existing).
- Produces:
  - `async def get_leads(campaign_id: int, limit: int = 100, offset: int = 0) -> dict`
  - `async def update_lead(campaign_id: int, lead_id: int, email: str, custom_fields: dict) -> dict`

- [ ] **Step 1: Write the failing test** — append to `tests/test_smartlead_service.py` (create the file with this content if it does not exist; if it exists, add these two tests and reuse its existing imports):

```python
import pytest

from app.services.smartlead_service import SmartleadService


@pytest.mark.anyio
async def test_get_leads_hits_campaign_leads_endpoint(monkeypatch):
    svc = SmartleadService("KEY")
    captured = {}

    async def fake_get(endpoint, params=None):
        captured["endpoint"] = endpoint
        captured["params"] = params
        return {"total_leads": 1, "data": [{"lead": {"id": 5, "email": "a@x.com", "custom_fields": {}}}]}

    monkeypatch.setattr(svc, "get", fake_get)
    out = await svc.get_leads(123, limit=50, offset=100)
    assert captured["endpoint"] == "campaigns/123/leads"
    assert captured["params"] == {"limit": 50, "offset": 100}
    assert out["total_leads"] == 1


@pytest.mark.anyio
async def test_update_lead_posts_per_lead_endpoint_with_email_and_custom_fields(monkeypatch):
    svc = SmartleadService("KEY")
    captured = {}

    async def fake_post(endpoint, payload):
        captured["endpoint"] = endpoint
        captured["payload"] = payload
        return {"ok": True}

    monkeypatch.setattr(svc, "post", fake_post)
    out = await svc.update_lead(123, 5, "a@x.com", {"Step 1": "A<br><br>B"})
    assert captured["endpoint"] == "campaigns/123/leads/5"
    assert captured["payload"] == {"email": "a@x.com", "custom_fields": {"Step 1": "A<br><br>B"}}
    assert out["ok"] is True
```

If `tests/test_smartlead_service.py` already exists and does not configure anyio, add this fixture once near the top:

```python
@pytest.fixture
def anyio_backend():
    return "asyncio"
```

(If the existing test suite already runs async tests a different way, follow that existing pattern instead of `@pytest.mark.anyio`.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_smartlead_service.py -q`
Expected: FAIL — `AttributeError: 'SmartleadService' object has no attribute 'get_leads'`.

- [ ] **Step 3: Write the implementation** — in `app/services/smartlead_service.py`, after `get_sequences` (line ~162), add:

```python
    async def get_leads(self, campaign_id: int, limit: int = 100, offset: int = 0) -> dict:
        return await self.get(f"campaigns/{campaign_id}/leads", {"limit": limit, "offset": offset})

    async def update_lead(self, campaign_id: int, lead_id: int, email: str, custom_fields: dict) -> dict:
        """Update a single lead's fields via the per-lead endpoint.

        `email` is REQUIRED in the body even though `lead_id` is in the URL —
        omitting it returns HTTP 400 `"email" is required`. Do not "clean up"
        the redundant-looking email field. The bulk add_leads upsert silently
        skips custom-field overwrites on existing leads, so this per-lead
        endpoint is mandatory for fixes.
        """
        return await self.post(
            f"campaigns/{campaign_id}/leads/{lead_id}",
            {"email": email, "custom_fields": custom_fields},
        )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_smartlead_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/smartlead_service.py tests/test_smartlead_service.py
git commit -m "feat: add SmartleadService.get_leads and per-lead update_lead"
```

---

### Task 3: Twin campaign plan builder + validation characterization test

`build_campaign_plan_from_input` produces a normal plan from parsed messaging. Add a sibling that produces the fixed twin plan (no parsing). Also lock in that the twin plan passes the existing validator unchanged (no validation_service edit is needed — `ALLOWED_MERGE_TAGS` is unused and `{{Step 1}}` does not trip `MALFORMED_MERGE_RE`).

**Files:**
- Modify: `app/services/local_plan_service.py`
- Test: `tests/test_local_plan_service.py` (add tests; create if absent)

**Interfaces:**
- Consumes: `app.services.twain_service.twain_sequence_plan`, existing `build_campaign_plan_from_input` schedule/settings shape.
- Produces: `build_twin_campaign_plan(raw_input: dict, followup_delay_days: int = 3) -> dict`

- [ ] **Step 1: Write the failing test** — add to `tests/test_local_plan_service.py`:

```python
from app.services.local_plan_service import build_twin_campaign_plan
from app.services.validation_service import validate_campaign_plan


def test_build_twin_campaign_plan_uses_fixed_sequence():
    raw = {"workspace_key": "darlean", "campaign_name": "Events - Twain", "max_new_leads_per_day": 80}
    plan = build_twin_campaign_plan(raw)
    assert plan["workspace_key"] == "darlean"
    assert plan["campaign_name"] == "Events - Twain"
    assert plan["schedule"]["max_new_leads_per_day"] == 80
    seq = plan["sequence"]
    assert [s["step_number"] for s in seq] == [1, 2]
    assert seq[0]["variants"][0]["subject"] == "{{Subject 1}}"
    assert "{{Step 1}}" in seq[0]["variants"][0]["body"]
    assert "{{Step 3}}" in seq[1]["variants"][0]["body"]


def test_twin_plan_passes_validation():
    raw = {"workspace_key": "darlean", "campaign_name": "Events - Twain", "max_new_leads_per_day": 80}
    plan = build_twin_campaign_plan(raw)
    assert validate_campaign_plan(plan, {"darlean"}) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_local_plan_service.py -q`
Expected: FAIL — `ImportError: cannot import name 'build_twin_campaign_plan'`.

- [ ] **Step 3: Write the implementation** — in `app/services/local_plan_service.py`, add at top `from app.services.twain_service import twain_sequence_plan`, then add:

```python
def build_twin_campaign_plan(raw_input: dict, followup_delay_days: int = 3) -> dict:
    """A twin campaign: fixed Twain sequence, no messaging parse.

    The per-lead body content (Subject 1 / Step 1 / Step 3) is filled by Twain
    externally; here we only author the template that references it.
    """
    max_new_leads_per_day = int(raw_input.get("max_new_leads_per_day") or 100)
    return {
        "workspace_key": raw_input["workspace_key"],
        "client_key": None,
        "campaign_name": raw_input["campaign_name"],
        "template_family": "twain_twin_v1",
        "goal": "book_meeting",
        "lead_source": {"type": "none", "expected_count": None},
        "schedule": {
            "timezone": "America/New_York",
            "days_of_the_week": [1, 2, 3, 4, 5],
            "start_hour": "09:00",
            "end_hour": "18:00",
            "min_time_btw_emails": 17,
            "max_new_leads_per_day": max_new_leads_per_day,
        },
        "settings": {
            "send_as_plain_text": True,
            "track_opens": False,
            "track_clicks": False,
            "stop_on_reply": True,
            "enable_ai_esp_matching": True,
            "auto_pause_domain_leads_on_reply": True,
            "ooo_restart_delay_days": 10,
        },
        "inbox_selection": {"mode": "skip", "email_account_ids": [], "provider_mix": {"gmail": 0.7, "outlook": 0.3}},
        "sequence": twain_sequence_plan(followup_delay_days),
        "approval_required": True,
        "notes_for_operator": [
            "Twin campaign: bodies are Twain-personalized per-lead custom fields.",
            "Run 'Fix Twain spacing' after leads are pushed.",
        ],
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_local_plan_service.py -q`
Expected: PASS. (If `test_twin_plan_passes_validation` fails, STOP — it means the validator does trip on the twin tags after all; that contradicts the plan's assumption and needs a validation_service fix scoped to twins. Report before proceeding.)

- [ ] **Step 5: Commit**

```bash
git add app/services/local_plan_service.py tests/test_local_plan_service.py
git commit -m "feat: add build_twin_campaign_plan + validation characterization test"
```

---

### Task 4: Persist twin fields in the store

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_store.py` (add tests; create if absent — uses the `fresh_mongomock` autouse fixture from `tests/conftest.py`)

**Interfaces:**
- Consumes: existing `insert_campaign`, `campaigns_collection`, `to_object_id`, `now_utc`, `get_campaign`.
- Produces:
  - `insert_campaign(..., is_twin: bool = False, twin_smartlead_url: str | None = None)` — two new keyword params, stored on the doc (`is_twin`, `twin_smartlead_url`, plus `twin_last_fix: None`).
  - `set_twin(campaign_id: str, is_twin: bool, twin_smartlead_url: str | None) -> dict | None`
  - `save_twin_fix(campaign_id: str, summary: dict) -> dict | None`

- [ ] **Step 1: Write the failing test** — add to `tests/test_store.py`:

```python
from app import store


def _new(**kw):
    return store.insert_campaign(
        workspace_key="darlean",
        campaign_name="T",
        raw_input={},
        plan={},
        validation_errors=[],
        **kw,
    )


def test_insert_defaults_not_twin():
    doc = _new()
    assert doc["is_twin"] is False
    assert doc["twin_smartlead_url"] is None
    assert doc["twin_last_fix"] is None


def test_insert_twin_fields():
    doc = _new(is_twin=True, twin_smartlead_url="https://app.smartlead.ai/app/email-campaign/42/overview")
    assert doc["is_twin"] is True
    assert "42" in doc["twin_smartlead_url"]


def test_set_twin_updates_flag_and_url():
    doc = _new()
    cid = str(doc["_id"])
    updated = store.set_twin(cid, True, "https://app.smartlead.ai/app/email-campaign/42/overview")
    assert updated["is_twin"] is True
    assert "42" in updated["twin_smartlead_url"]


def test_save_twin_fix_persists_summary():
    doc = _new(is_twin=True)
    cid = str(doc["_id"])
    summary = {"total_leads": 10, "leads_changed": 3, "errors": []}
    updated = store.save_twin_fix(cid, summary)
    assert updated["twin_last_fix"]["leads_changed"] == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_store.py -q`
Expected: FAIL — `KeyError: 'is_twin'` (and missing `set_twin`/`save_twin_fix`).

- [ ] **Step 3: Write the implementation** — in `app/store.py`:

In `insert_campaign`, add params and doc fields. Change the signature line `created_by: str | None = None,` block to also accept the twin params, and add the three fields to `doc`:

```python
def insert_campaign(
    *,
    workspace_key: str,
    campaign_name: str,
    raw_input: dict,
    plan: dict,
    validation_errors: list[str],
    smartlead_campaign_id: int | None = None,
    smartlead_client_id: int | None = None,
    smartlead_client_name: str | None = None,
    smartlead_client_match: str | None = None,
    status: str | None = None,
    created_by: str | None = None,
    is_twin: bool = False,
    twin_smartlead_url: str | None = None,
) -> dict:
    now = now_utc()
    status = status or ("drafting" if validation_errors else "ready")
    doc = {
        "smartlead_campaign_id": smartlead_campaign_id,
        "smartlead_workspace": workspace_key,
        "smartlead_client_id": smartlead_client_id,
        "smartlead_client_name": smartlead_client_name,
        "smartlead_client_match": smartlead_client_match,
        "campaign_name": campaign_name,
        "raw_input": raw_input,
        "current_plan": plan,
        "validation_errors": validation_errors,
        "status": status,
        "last_sync_error": None,
        "created_by": created_by,
        "is_twin": is_twin,
        "twin_smartlead_url": twin_smartlead_url,
        "twin_last_fix": None,
        "created_at": now,
        "updated_at": now,
    }
    result = campaigns_collection().insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc
```

Then add two functions (after `mark_sync_failed`):

```python
def set_twin(campaign_id: str, is_twin: bool, twin_smartlead_url: str | None) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"is_twin": is_twin, "twin_smartlead_url": twin_smartlead_url, "updated_at": now_utc()}},
        return_document=True,
    )


def save_twin_fix(campaign_id: str, summary: dict) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"twin_last_fix": summary, "updated_at": now_utc()}},
        return_document=True,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: persist is_twin / twin_smartlead_url / twin_last_fix in store"
```

---

### Task 5: Create-twin checkbox at campaign creation

**Files:**
- Modify: `app/routes/campaigns.py` (the `create_campaign` route, lines 101-173)
- Modify: `app/templates/campaign_new.html`
- Test: `tests/test_campaign_routes.py` (add a test)

**Interfaces:**
- Consumes: `build_twin_campaign_plan` (Task 3), `store.insert_campaign(is_twin=...)` (Task 4), existing `get_workspace_config`, `validate_campaign_plan`, `_active_workspace_keys`.
- Produces: `POST /api/campaigns/new` accepts `is_twin: bool = Form(False)`; when true, plan = twin plan and the doc is flagged twin.

- [ ] **Step 1: Write the failing test** — add to `tests/test_campaign_routes.py` (reuse the existing `client` fixture):

```python
def test_create_twin_campaign_injects_fixed_sequence(client):
    resp = client.post(
        "/api/campaigns/new",
        data={"workspace_key": "darlean", "campaign_name": "Events - Twain", "is_twin": "true"},
        files={"messaging_file": ("", b"", "text/plain")},
    )
    assert resp.status_code == 303
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    from app import store
    doc = store.get_campaign(cid)
    assert doc["is_twin"] is True
    seq = doc["current_plan"]["sequence"]
    assert seq[0]["variants"][0]["subject"] == "{{Subject 1}}"
    assert "{{Step 3}}" in seq[1]["variants"][0]["body"]
```

(If the existing create tests post without a `files=` argument, drop the empty `files` arg here to match that pattern.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py::test_create_twin_campaign_injects_fixed_sequence -q`
Expected: FAIL — `doc["is_twin"]` KeyError or assertion on sequence (twin branch not wired).

- [ ] **Step 3: Write the implementation** —

(a) In `app/routes/campaigns.py`, add the import near the other `local_plan_service` import (line 38):

```python
from app.services.local_plan_service import build_campaign_plan_from_input, build_twin_campaign_plan
```

(b) Add the form param to `create_campaign` (after `selected_sequence_name`):

```python
    is_twin: bool = Form(False),
```

(c) Replace the plan-selection block (the `if imported_plan: ... else: ...` at lines 146-160) so the twin branch wins first:

```python
    if is_twin:
        plan = build_twin_campaign_plan(raw_input)
        errors = validate_campaign_plan(plan, _active_workspace_keys())
        status = None
    elif imported_plan:
        plan = imported_plan
        errors = validate_campaign_plan(plan, _active_workspace_keys())
        status = None
    elif link_only_existing_campaign:
        plan = {}
        errors = []
        status = "linked"
    else:
        plan = build_campaign_plan_from_input(
            raw_input,
            note="Plan generated deterministically from parsed messaging.",
        )
        errors = validate_campaign_plan(plan, _active_workspace_keys())
        status = None
```

(d) Pass the twin flags into `insert_campaign` (add to the existing call's kwargs):

```python
        status=status,
        is_twin=is_twin,
        twin_smartlead_url=None,
    )
```

(e) In `app/templates/campaign_new.html`, add a twin checkbox inside the "Campaign details" `field-grid` (after the campaign-name field, before the closing `</div>` of `field-grid` at line 33):

```html
        <div class="field span-2">
          <label class="checkbox-row">
            <input type="checkbox" name="is_twin" value="true">
            <span>Twin campaign (Twain-personalized) — uses the fixed Subject 1 / Step 1 / Step 3 sequence; messaging upload is ignored.</span>
          </label>
        </div>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py::test_create_twin_campaign_injects_fixed_sequence -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/campaigns.py app/templates/campaign_new.html tests/test_campaign_routes.py
git commit -m "feat: create twin campaign from new-campaign form (fixed Twain sequence)"
```

---

### Task 6: Mark-as-twin route + detail payload + UI

**Files:**
- Modify: `app/routes/campaigns.py` (new route + `_detail_payload`)
- Modify: `app/templates/campaign_detail.html`
- Test: `tests/test_campaign_routes.py`

**Interfaces:**
- Consumes: `_require_campaign`, `_redirect_to_detail`, `store.set_twin`, `_extract_smartlead_campaign_id` (existing, line ~669).
- Produces: `POST /api/campaigns/{campaign_id}/twin` with form fields `is_twin: bool = Form(False)`, `twin_smartlead_url: str = Form("")`. `_detail_payload` returns `is_twin`, `twin_smartlead_url`, `twin_last_fix`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_campaign_routes.py`:

```python
def test_mark_as_twin_persists_flag_and_url(client):
    resp = client.post(
        "/api/campaigns/new",
        data={"workspace_key": "darlean", "campaign_name": "Plain"},
    )
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    url = "https://app.smartlead.ai/app/email-campaign/777/overview"
    r2 = client.post(f"/api/campaigns/{cid}/twin", data={"is_twin": "true", "twin_smartlead_url": url})
    assert r2.status_code in (200, 303)
    from app import store
    doc = store.get_campaign(cid)
    assert doc["is_twin"] is True
    assert "777" in doc["twin_smartlead_url"]
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py::test_mark_as_twin_persists_flag_and_url -q`
Expected: FAIL — 404/405 (route not defined).

- [ ] **Step 3: Write the implementation** —

(a) Add the route in `app/routes/campaigns.py` (place near the other `/api/campaigns/{campaign_id}/...` mutation routes):

```python
@router.post("/api/campaigns/{campaign_id}/twin")
def mark_twin(
    campaign_id: str,
    request: Request,
    is_twin: bool = Form(False),
    twin_smartlead_url: str = Form(""),
) -> dict:
    _require_campaign(campaign_id)
    url = twin_smartlead_url.strip() or None
    if url and _extract_smartlead_campaign_id(url) is None:
        raise HTTPException(status_code=400, detail="Paste a valid Smartlead campaign URL or numeric ID")
    store.set_twin(campaign_id, is_twin, url)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "is_twin": is_twin})
```

(b) In `_detail_payload` (line ~831), add three keys to the returned dict (next to `smartlead_campaign_id`):

```python
        "is_twin": doc.get("is_twin", False),
        "twin_smartlead_url": doc.get("twin_smartlead_url"),
        "twin_last_fix": doc.get("twin_last_fix"),
```

(c) In `app/templates/campaign_detail.html`, add a twin badge in `detail-subline` (after the status badge, line ~10):

```html
      {% if campaign.is_twin %}<span class="badge status-synced">Twin</span>{% endif %}
```

(d) Add a twin panel near the top of `detail-main` (after the `last_sync_error` banner block, before `<div class="detail-grid">` is fine, or as the first card in `detail-main`). Insert this block right after line 45 (`{% endif %}` closing the sync-error banner):

```html
<section class="detail-card">
  <div class="card-head"><h2 class="card-title">Twin / Twain</h2></div>
  {% if campaign.is_twin %}
  <p class="card-lead">Twin campaign — bodies are Twain-personalized per-lead fields.</p>
  <form method="post" action="/api/campaigns/{{ campaign.id }}/twin-fix" class="js-single-submit twin-fix-form">
    <input class="input" name="twin_smartlead_url" placeholder="Smartlead campaign URL (optional — overrides the linked id)" value="{{ campaign.twin_smartlead_url or '' }}">
    <button type="submit" class="button">Fix Twain spacing</button>
  </form>
  {% if campaign.twin_last_fix %}
  <div class="twin-fix-summary">
    <p>Last fix: {{ campaign.twin_last_fix.leads_changed }} of {{ campaign.twin_last_fix.total_leads }} leads changed.</p>
    {% if campaign.twin_last_fix.template_repushed %}<p>Template join repaired and repushed.</p>{% endif %}
    {% if campaign.twin_last_fix.greeting_flags %}<p>Greeting flags: {{ campaign.twin_last_fix.greeting_flags | length }}.</p>{% endif %}
    {% if campaign.twin_last_fix.errors %}<p class="muted">Errors: {{ campaign.twin_last_fix.errors | length }}.</p>{% endif %}
  </div>
  {% endif %}
  {% else %}
  <form method="post" action="/api/campaigns/{{ campaign.id }}/twin" class="js-single-submit">
    <input type="hidden" name="is_twin" value="true">
    <input class="input" name="twin_smartlead_url" placeholder="Smartlead campaign URL (optional)">
    <button type="submit" class="button secondary">Mark as twin</button>
  </form>
  {% endif %}
</section>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py::test_mark_as_twin_persists_flag_and_url -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/campaigns.py app/templates/campaign_detail.html tests/test_campaign_routes.py
git commit -m "feat: mark-as-twin route, twin badge, and twin panel"
```

---

### Task 7: Twin fix worker

The core. Mirrors `app/workers/sync_campaign.py`: a sync entrypoint wrapping an async core. Fetches all leads, normalizes the three fields, flags greetings, writes only changed leads via the per-lead endpoint, audits, re-checks the template join (repushing if reverted), and persists a summary.

**Files:**
- Create: `app/workers/twin_fix.py`
- Test: `tests/test_twin_fix.py`

**Interfaces:**
- Consumes: `store.get_campaign`, `store.save_twin_fix`, `get_workspace_config`, `SmartleadService.get_leads/update_lead/get_sequences/update_sequences`, `twain_service.normalize_twain_field/audit_twain_field/flag_greeting_issues/twain_sequence_plan`, `sequence_builder.build_smartlead_sequences/smartlead_html_to_text`.
- Produces:
  - `run_twin_fix_now(campaign_id: str, override_url: str | None = None) -> None` (sync wrapper)
  - `async def _run_twin_fix(campaign_id: str, override_url: str | None) -> dict` (returns the summary; also persisted)
  - `_resolve_target_id(doc, override_url) -> int | None`
  - `_extract_campaign_id_from_url(value: str) -> int | None`

- [ ] **Step 1: Write the failing test** — create `tests/test_twin_fix.py`:

```python
import pytest

from app import store
from app.workers import twin_fix


class FakeSmartlead:
    def __init__(self, leads, sequences=None):
        self._leads = leads
        self._sequences = sequences or {"data": []}
        self.updated = []          # (lead_id, email, custom_fields)
        self.sequences_pushed = []

    async def get_leads(self, campaign_id, limit=100, offset=0):
        page = self._leads[offset:offset + limit]
        return {"total_leads": len(self._leads), "data": page}

    async def update_lead(self, campaign_id, lead_id, email, custom_fields):
        self.updated.append((lead_id, email, custom_fields))
        return {"ok": True}

    async def get_sequences(self, campaign_id):
        return self._sequences

    async def update_sequences(self, campaign_id, sequences):
        self.sequences_pushed.append(sequences)
        return {"ok": True}


def _lead(lead_id, email, **cf):
    return {"lead": {"id": lead_id, "email": email, "custom_fields": cf}}


def _twin_doc(**kw):
    return store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan={},
        validation_errors=[], is_twin=True, **kw,
    )


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_fix_updates_only_changed_leads(monkeypatch):
    doc = _twin_doc(smartlead_campaign_id=42)
    cid = str(doc["_id"])
    fake = FakeSmartlead([
        _lead(1, "a@x.com", **{"Step 1": "A<br>B"}),          # dirty -> changes
        _lead(2, "b@x.com", **{"Step 1": "Clean<br><br>Body"}),  # clean -> no change
        _lead(3, "", **{"Step 1": "A<br>B"}),                  # no email -> skipped
    ])
    monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
    monkeypatch.setattr(twin_fix, "SmartleadService", lambda key: fake)

    summary = await twin_fix._run_twin_fix(cid, None)

    assert summary["total_leads"] == 3
    assert summary["leads_changed"] == 1
    assert [u[0] for u in fake.updated] == [1]
    assert fake.updated[0][2]["Step 1"] == "A<br><br>B"
    # email is sent in the body
    assert fake.updated[0][1] == "a@x.com"
    # persisted
    assert store.get_campaign(cid)["twin_last_fix"]["leads_changed"] == 1


@pytest.mark.anyio
async def test_fix_resolves_url_over_linked_id(monkeypatch):
    doc = _twin_doc(smartlead_campaign_id=42)
    cid = str(doc["_id"])
    seen = {}
    fake = FakeSmartlead([])

    async def spy_get_leads(campaign_id, limit=100, offset=0):
        seen["campaign_id"] = campaign_id
        return {"total_leads": 0, "data": []}

    fake.get_leads = spy_get_leads
    monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
    monkeypatch.setattr(twin_fix, "SmartleadService", lambda key: fake)

    await twin_fix._run_twin_fix(cid, "https://app.smartlead.ai/app/email-campaign/999/overview")
    assert seen["campaign_id"] == 999


@pytest.mark.anyio
async def test_fix_flags_greeting_issues(monkeypatch):
    doc = _twin_doc(smartlead_campaign_id=42)
    cid = str(doc["_id"])
    fake = FakeSmartlead([
        _lead(1, "a@x.com", **{"Step 1": "Hi Mark,<br><br>Body", "Step 3": "No greeting"}),
    ])
    monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
    monkeypatch.setattr(twin_fix, "SmartleadService", lambda key: fake)

    summary = await twin_fix._run_twin_fix(cid, None)
    kinds = {f["flag"] for f in summary["greeting_flags"]}
    assert "step1_has_greeting" in kinds
    assert "step3_missing_greeting" in kinds


@pytest.mark.anyio
async def test_fix_repushes_template_when_join_reverted(monkeypatch):
    doc = _twin_doc(smartlead_campaign_id=42)
    cid = str(doc["_id"])
    # Smartlead reports a reverted lone-<br> Step 1 join.
    reverted = {"data": [
        {"seq_number": 1, "seq_variants": [{"variant_label": "A",
            "email_body": "Hi {{first_name}},<br>{{Step 1}}<br><br>%signature%", "subject": "{{Subject 1}}"}]},
        {"seq_number": 2, "seq_variants": [{"variant_label": "A",
            "email_body": "{{Step 3}}<br><br>%signature%", "subject": ""}]},
    ]}
    fake = FakeSmartlead([], sequences=reverted)
    monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
    monkeypatch.setattr(twin_fix, "SmartleadService", lambda key: fake)

    summary = await twin_fix._run_twin_fix(cid, None)
    assert summary["template_repushed"] is True
    assert fake.sequences_pushed, "expected the corrected twin sequence to be repushed"


@pytest.mark.anyio
async def test_fix_no_target_records_error(monkeypatch):
    doc = _twin_doc()  # no smartlead_campaign_id, no url
    cid = str(doc["_id"])
    monkeypatch.setattr(twin_fix, "get_workspace_config", lambda k: {"key": k, "api_key": "KEY"})
    summary = await twin_fix._run_twin_fix(cid, None)
    assert summary["errors"]
    assert summary["leads_changed"] == 0
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_twin_fix.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workers.twin_fix'`.

- [ ] **Step 3: Write the implementation** — create `app/workers/twin_fix.py`:

```python
"""Background worker: fix Twain spacing on a twin campaign's leads.

Mirrors app/workers/sync_campaign.py. Fetches every lead, normalizes the three
Twain custom fields (<br>-primary), writes only changed leads via the per-lead
endpoint, flags greeting-content issues, audits, and re-checks the template
join (repushing the corrected twin sequence if it has reverted to a lone <br>).
"""

import asyncio
import re

from app.config import get_workspace_config
from app.services.sequence_builder import build_smartlead_sequences, smartlead_html_to_text
from app.services.smartlead_service import SmartleadService
from app.services.twain_service import (
    audit_twain_field,
    flag_greeting_issues,
    normalize_twain_field,
    twain_sequence_plan,
)
from app import store

_FIELD_KEYS = ("Subject 1", "Step 1", "Step 3")
_SUBJECT_KEYS = {"Subject 1"}
_PAGE = 100
_URL_RE = re.compile(r"(?:email-campaigns-v2|email-campaigns|email-campaign|campaigns?)/(\d+)")


def run_twin_fix_now(campaign_id: str, override_url: str | None = None) -> None:
    """Synchronous entrypoint used by FastAPI BackgroundTasks."""
    asyncio.run(_run_twin_fix(campaign_id, override_url))


def _extract_campaign_id_from_url(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    m = _URL_RE.search(text)
    return int(m.group(1)) if m else None


def _resolve_target_id(doc: dict, override_url: str | None) -> int | None:
    from_url = _extract_campaign_id_from_url(override_url or doc.get("twin_smartlead_url") or "")
    if from_url:
        return from_url
    linked = doc.get("smartlead_campaign_id")
    return int(linked) if linked else None


async def _run_twin_fix(campaign_id: str, override_url: str | None) -> dict:
    summary = {
        "campaign_id": None,
        "total_leads": 0,
        "leads_changed": 0,
        "field_counts": {k: 0 for k in _FIELD_KEYS},
        "greeting_flags": [],
        "template_repushed": False,
        "residual_defects": [],
        "errors": [],
    }
    doc = store.get_campaign(campaign_id)
    if not doc:
        summary["errors"].append("Campaign not found")
        return summary

    target_id = _resolve_target_id(doc, override_url)
    if not target_id:
        summary["errors"].append("No Smartlead campaign id (paste a URL or link the campaign first)")
        store.save_twin_fix(campaign_id, summary)
        return summary
    summary["campaign_id"] = target_id

    workspace = get_workspace_config(doc.get("smartlead_workspace", ""))
    if not workspace or not workspace.get("api_key"):
        summary["errors"].append(f"Smartlead API key not configured for workspace '{doc.get('smartlead_workspace')}'")
        store.save_twin_fix(campaign_id, summary)
        return summary

    smartlead = SmartleadService(workspace["api_key"])

    try:
        await _fix_leads(smartlead, target_id, summary)
        await _recheck_template(smartlead, target_id, summary)
    except Exception as exc:  # surface, don't crash the background task
        summary["errors"].append(f"{exc.__class__.__name__}: {exc}")

    store.save_twin_fix(campaign_id, summary)
    return summary


async def _fix_leads(smartlead: SmartleadService, campaign_id: int, summary: dict) -> None:
    offset = 0
    while True:
        response = await smartlead.get_leads(campaign_id, limit=_PAGE, offset=offset)
        rows = response.get("data") or []
        if not rows:
            break
        for row in rows:
            lead = row.get("lead") or row
            summary["total_leads"] += 1
            email = (lead.get("email") or "").strip()
            if not email:
                continue  # can't update or send
            cf = dict(lead.get("custom_fields") or {})
            changed = {}
            for key in _FIELD_KEYS:
                if key not in cf:
                    continue
                original = cf[key]
                fixed = normalize_twain_field(original, is_subject=key in _SUBJECT_KEYS)
                if fixed != original:
                    changed[key] = fixed
                    summary["field_counts"][key] += 1
            flags = flag_greeting_issues(cf.get("Step 1"), cf.get("Step 3"))
            for flag in flags:
                summary["greeting_flags"].append({"lead_id": lead.get("id"), "email": email, "flag": flag})
            if changed:
                merged = {**cf, **changed}
                await smartlead.update_lead(campaign_id, lead.get("id"), email, merged)
                summary["leads_changed"] += 1
                for key, val in changed.items():
                    residual = audit_twain_field(val)
                    if residual:
                        summary["residual_defects"].append({"lead_id": lead.get("id"), "field": key, "defects": residual})
        if len(rows) < _PAGE:
            break
        offset += _PAGE


def _step1_body_from_sequences(response: object) -> str | None:
    data = response.get("data") if isinstance(response, dict) else response
    if not isinstance(data, list):
        return None
    for step in data:
        if int(step.get("seq_number") or step.get("step_number") or 0) == 1:
            variants = step.get("seq_variants") or step.get("sequence_variants") or [step]
            for v in variants:
                body = v.get("email_body") or step.get("email_body")
                if body:
                    return body
    return None


async def _recheck_template(smartlead: SmartleadService, campaign_id: int, summary: dict) -> None:
    """Re-check the Step 1 template join; repush the corrected twin sequence if reverted."""
    response = await smartlead.get_sequences(campaign_id)
    step1_body = _step1_body_from_sequences(response)
    if step1_body and audit_twain_field(step1_body):
        sequences = build_smartlead_sequences(twain_sequence_plan())
        await smartlead.update_sequences(campaign_id, sequences)
        summary["template_repushed"] = True
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_twin_fix.py -q`
Expected: PASS (5 tests). Note: `_step1_body_from_sequences` returns `Hi {{first_name}},<br>{{Step 1}}<br><br>%signature%` for the reverted fixture; `audit_twain_field` flags the lone `<br>` (lone_br), triggering the repush.

- [ ] **Step 5: Commit**

```bash
git add app/workers/twin_fix.py tests/test_twin_fix.py
git commit -m "feat: twin_fix worker (normalize lead fields, flag greetings, recheck template)"
```

---

### Task 8: Twin-fix route (background trigger)

**Files:**
- Modify: `app/routes/campaigns.py` (new route + import)
- Test: `tests/test_campaign_routes.py`

**Interfaces:**
- Consumes: `_require_campaign`, `_redirect_to_detail`, `BackgroundTasks` (already imported), `run_twin_fix_now`.
- Produces: `POST /api/campaigns/{campaign_id}/twin-fix` with `twin_smartlead_url: str = Form("")`. Rejects non-twin campaigns with 400. Schedules `run_twin_fix_now` as a background task.

- [ ] **Step 1: Write the failing test** — add to `tests/test_campaign_routes.py`:

```python
def test_twin_fix_rejected_for_non_twin(client):
    resp = client.post("/api/campaigns/new", data={"workspace_key": "darlean", "campaign_name": "Plain"})
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/api/campaigns/{cid}/twin-fix", data={})
    assert r.status_code == 400


def test_twin_fix_schedules_background_task(client, monkeypatch):
    import app.routes.campaigns as routes
    calls = {}
    monkeypatch.setattr(routes, "run_twin_fix_now", lambda cid, url=None: calls.setdefault("args", (cid, url)))
    resp = client.post(
        "/api/campaigns/new",
        data={"workspace_key": "darlean", "campaign_name": "Events - Twain", "is_twin": "true"},
    )
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/api/campaigns/{cid}/twin-fix", data={"twin_smartlead_url": ""})
    assert r.status_code in (200, 303)
    assert calls["args"][0] == cid
```

(FastAPI's `TestClient` runs background tasks synchronously after the response, so the monkeypatched `run_twin_fix_now` will have been called by the time the request returns.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py::test_twin_fix_rejected_for_non_twin tests/test_campaign_routes.py::test_twin_fix_schedules_background_task -q`
Expected: FAIL — route not defined (404/405).

- [ ] **Step 3: Write the implementation** —

(a) Add the import in `app/routes/campaigns.py` next to the sync worker import (line 46):

```python
from app.workers.sync_campaign import sync_campaign_now
from app.workers.twin_fix import run_twin_fix_now
```

(b) Add the route (near the `mark_twin` route from Task 6):

```python
@router.post("/api/campaigns/{campaign_id}/twin-fix")
def twin_fix(
    campaign_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    twin_smartlead_url: str = Form(""),
) -> dict:
    doc = _require_campaign(campaign_id)
    if not doc.get("is_twin"):
        raise HTTPException(status_code=400, detail="Not a twin campaign. Mark it as twin first.")
    url = twin_smartlead_url.strip() or None
    background_tasks.add_task(run_twin_fix_now, campaign_id, url)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "queued": True})
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py::test_twin_fix_rejected_for_non_twin tests/test_campaign_routes.py::test_twin_fix_schedules_background_task -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/routes/campaigns.py tests/test_campaign_routes.py
git commit -m "feat: twin-fix route schedules the background spacing fix"
```

---

### Task 9: Sync worker — hard-fail on a reverted Step 1 join

The twin Step 1 join has silently reverted to a lone `<br>` after sync before. After pushing sequences, GET them back and hard-fail (mark sync failed) if a twin campaign's Step 1 body carries a lone-`<br>` defect.

**Files:**
- Modify: `app/workers/sync_campaign.py`
- Test: `tests/test_sync_worker.py` (add a test)

**Interfaces:**
- Consumes: `audit_twain_field` (Task 1), existing `_smartlead_sequence_list`.
- Produces: `_assert_twin_join_intact(smartlead_sequences: list[dict]) -> None` (raises `RuntimeError` on a lone-`<br>` Step 1 join). Called inside `_sync_campaign_async` for twin docs after `_verify_smartlead_sequence_sync`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_sync_worker.py`:

```python
import pytest

from app.workers import sync_campaign


def test_assert_twin_join_intact_raises_on_lone_br():
    reverted = [
        {"seq_number": 1, "seq_variants": [{"variant_label": "A",
            "email_body": "Hi {{first_name}},<br>{{Step 1}}<br><br>%signature%"}]},
    ]
    with pytest.raises(RuntimeError):
        sync_campaign._assert_twin_join_intact(reverted)


def test_assert_twin_join_intact_passes_on_double_br():
    good = [
        {"seq_number": 1, "seq_variants": [{"variant_label": "A",
            "email_body": "Hi {{first_name}},<br><br>{{Step 1}}<br><br>%signature%"}]},
    ]
    sync_campaign._assert_twin_join_intact(good)  # no raise
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_sync_worker.py::test_assert_twin_join_intact_raises_on_lone_br tests/test_sync_worker.py::test_assert_twin_join_intact_passes_on_double_br -q`
Expected: FAIL — `AttributeError: module 'app.workers.sync_campaign' has no attribute '_assert_twin_join_intact'`.

- [ ] **Step 3: Write the implementation** — in `app/workers/sync_campaign.py`:

(a) Add the import near the top (after the `sequence_builder` import block, line ~30):

```python
from app.services.twain_service import audit_twain_field
```

(b) Add the helper (near `_verify_smartlead_sequence_sync`):

```python
def _assert_twin_join_intact(smartlead_sequences: list[dict]) -> None:
    """Hard-fail if a twin Step 1 body has reverted to a lone <br> join.

    Soft logging is insufficient — this join has silently reverted before and
    was only caught by a screenshot.
    """
    for step in smartlead_sequences:
        if int(step.get("seq_number") or step.get("step_number") or 0) != 1:
            continue
        variants = step.get("seq_variants") or step.get("sequence_variants") or [step]
        for variant in variants:
            body = variant.get("email_body") or step.get("email_body") or ""
            if "lone_br" in audit_twain_field(body):
                raise RuntimeError("Twin Step 1 join reverted to a lone <br> after sync (expected <br><br>)")
```

(c) Wire it into `_sync_campaign_async`. After the `_verify_smartlead_sequence_sync` call (line 102), add:

```python
        if doc.get("is_twin"):
            verify_response = await smartlead.get_sequences(smartlead_id)
            _assert_twin_join_intact(_smartlead_sequence_list(verify_response))
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_sync_worker.py -q`
Expected: PASS (new + existing sync tests).

- [ ] **Step 5: Commit**

```bash
git add app/workers/sync_campaign.py tests/test_sync_worker.py
git commit -m "feat: hard-fail sync when twin Step 1 join reverts to lone <br>"
```

---

### Task 10: Full-suite regression + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q`
Expected: PASS — previous 183 plus the new twin tests, 0 failures.

- [ ] **Step 2: Manual smoke (local server)**

Run: `cd precise-automator && .venv/Scripts/python.exe -m uvicorn app.main:app --reload`
Then in the browser:
1. New campaign → check "Twin campaign" → create. Confirm the detail page shows the **Twin** badge and a "Fix Twain spacing" form.
2. On a non-twin campaign, "Mark as twin" with a Smartlead URL → confirm the badge appears and the URL persists.
3. (Optional, against a real Smartlead twin) click "Fix Twain spacing", wait, reload → confirm the last-fix summary (leads changed / template repushed) renders.

- [ ] **Step 3: Commit any doc/UI polish**

```bash
git add -A
git commit -m "chore: twin campaign feature regression pass"
```

---

## Self-Review

**Spec coverage:**
- Twin sequence template → Task 1 (`twain_sequence_plan`) + Task 3 (`build_twin_campaign_plan`).
- Create at creation → Task 5. Mark existing as twin → Task 6. Fix trigger (URL-over-link) → Task 7 (`_resolve_target_id`) + Task 8 (route).
- Data model (`is_twin`/`twin_smartlead_url`/`twin_last_fix`) → Task 4.
- Fix worker steps (paginate, skip no-email, normalize, flag, write changed per-lead, audit, template re-check) → Task 7.
- `<br>`-primary normalizer + audit + paired contract → Task 1.
- Smartlead `get_leads` / per-lead `update_lead` (email required) → Task 2.
- Validation accepts twin tags → Task 3 (characterization test; no code change needed, confirmed).
- Template join hard-fail at sync → Task 9.
- UI (badge, mark form, fix button, summary, new-form checkbox) → Tasks 5 & 6.
- Tests mock Smartlead, no live calls → Tasks 2, 7, 8.

**Placeholder scan:** No TBD/TODO; every code step has full code.

**Type consistency:** `normalize_twain_field`, `audit_twain_field`, `flag_greeting_issues`, `twain_sequence_plan` names match across Tasks 1/3/7/9. `get_leads(campaign_id, limit, offset)` and `update_lead(campaign_id, lead_id, email, custom_fields)` match between Task 2 (definition) and Task 7 (FakeSmartlead + calls). `set_twin`/`save_twin_fix`/`insert_campaign(is_twin=, twin_smartlead_url=)` match between Task 4 and Tasks 5/6/7. Summary keys (`total_leads`, `leads_changed`, `greeting_flags`, `template_repushed`, `errors`) match between Task 7 (producer) and Task 6 (template consumer).

**Known follow-ups (out of this plan):** the `field_counts`/`residual_defects` summary keys are persisted but only partially surfaced in the detail template — fine for v1; richer rendering is a later polish.
