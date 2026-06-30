# Auto LinkedIn Detection & Dual-Campaign Creation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When a messaging file is uploaded, auto-detect LinkedIn vs email steps from step headers, create both a Smartlead (email) and HeyReach (LinkedIn) campaign in one sync action, with per-workspace/client LinkedIn sender account mapping.

**Architecture:** Extend `parser_service.py` to emit LinkedIn steps (currently skipped) with `channel="linkedin"` and a `linkedin_subtype` of `connection_request` or `dm`. Extend `local_plan_service.py` to include LinkedIn steps in the plan. Extend `sync_campaign.py` to run HeyReach campaign creation immediately after Smartlead, in the same background task. Add per-workspace JSON env var for client→account-ID mapping. Remove the manual "Create LinkedIn campaign" button — sync triggers everything.

**Tech Stack:** FastAPI, Python, MongoDB (mongomock in tests), HeyReach REST API, Smartlead REST API, httpx, Pydantic, Jinja2.

## Global Constraints

- Branch: `feat/twin-campaign-twain-fix` (already active — commit to this branch)
- Run tests with: `PYTHONPATH=. .venv/Scripts/python.exe -m pytest` (Windows)
- All tests must pass after every task (currently 289 passing)
- No new dependencies
- TDD: write failing test first, then implement
- `channel: Literal["email", "linkedin"]` already exists on `SequenceStep` (do not re-add)
- `linkedin_messages(plan)` already exists in `app/schemas/campaign_plan.py` (do not re-add)
- HeyReach Phase 1 code already exists: `heyreach_service.py`, `heyreach_create.py`, `heyreach_sequence_builder.py`, store fields `heyreach_campaign_id/url/status/creating/last_error`
- The existing `heyreach_create.py` worker fetches ALL LinkedIn accounts — Phase 2 adds per-client filtering on top

---

## File Map

| File | Action | What changes |
|------|--------|-------------|
| `app/services/parser_service.py` | Modify | `_parse_step_channel_format` keeps LinkedIn steps instead of warning-skipping them; adds `linkedin_subtype` field; new `_extract_linkedin_body` helper |
| `app/services/local_plan_service.py` | Modify | `build_campaign_plan_from_input` passes LinkedIn steps into the plan with `channel="linkedin"` |
| `app/schemas/campaign_plan.py` | Modify | `SequenceStep` gains `linkedin_subtype: Literal["connection_request", "dm"] \| None`; `sequence_limits` validator relaxes to allow mixed email+LinkedIn without the 4-step email cap applying to LinkedIn steps |
| `app/config.py` | Modify | New helper `get_heyreach_account_ids_for_client(workspace_key, client_name)` reads `HEYREACH_{WORKSPACE}_CLIENT_ACCOUNTS` JSON env var |
| `app/workers/heyreach_create.py` | Modify | `_create_async` accepts optional `client_name` param; filters account IDs via config helper when mapping exists; falls back to all accounts |
| `app/workers/sync_campaign.py` | Modify | After Smartlead sync succeeds, if LinkedIn steps exist → call HeyReach create inline (same background task); set/clear `heyreach_creating` flag |
| `app/routes/campaigns.py` | Modify | Remove manual `/heyreach-create` button route (or keep but auto-trigger hides it); `_detail_payload` shows HeyReach status from sync result; `campaign_status` gains `has_linkedin_steps` |
| `app/templates/campaign_detail.html` | Modify | Remove manual HeyReach create button/panel; show HeyReach status inline with sync result; poller already polls `heyreach_creating` |
| `tests/test_parser_service.py` | Modify | New tests for LinkedIn step detection |
| `tests/test_local_plan_service.py` | Modify | New test: LinkedIn steps appear in plan with correct channel/subtype |
| `tests/test_sync_campaign.py` | Create/Modify | New tests: HeyReach triggered after Smartlead sync; skipped gracefully if no LinkedIn steps or no HeyReach key |
| `tests/test_campaign_routes.py` | Modify | Sync route test verifies `heyreach_creating` set when LinkedIn steps present |

---

## Task 1: Parser — Emit LinkedIn Steps

**Files:**
- Modify: `app/services/parser_service.py`
- Test: `tests/test_parser_service.py`

**Interfaces:**
- Produces: `_parse_step_channel_format` returns steps list that now includes LinkedIn entries:
  ```python
  {
      "step_number": 2,          # original file step number
      "channel": "linkedin",
      "linkedin_subtype": "connection_request",  # or "dm"
      "body_variants": [{"variant_label": "A", "body": "..."}],
      "day": 0,
  }
  # email steps gain "channel": "email" (were previously implicitly email)
  ```
- Email steps still use sequential `step_number` (1..N) for Smartlead; LinkedIn steps keep their **original** step number from the file so ordering is preserved when both are in the plan.

**Background:** `_parse_step_channel_format` at line 114 currently warns-and-skips any non-email step. The regex `STEP_CHANNEL_RE` at line 13 already matches `Step N — LinkedIn ...` headers. The `channel_keyword` check at line 134 branches on `channel_keyword.lower().startswith("email")`. We need to handle LinkedIn in the `else` branch instead of warning.

**LinkedIn subtype detection rules (from real file format):**
- Header contains `Connection Request` (case-insensitive) → `linkedin_subtype = "connection_request"`
- Header contains `DM` (any number suffix) → `linkedin_subtype = "dm"`
- Anything else LinkedIn → `linkedin_subtype = "dm"` (safe default)

- [ ] **Step 1: Write failing tests**

In `tests/test_parser_service.py` add:

```python
def test_parse_step_channel_linkedin_connection_request():
    text = """Campaign Title

Step 1 — Email (Day 0)
Hey {{first_name}},

Step 2 — LinkedIn (Connection Request - Optional) (Day 0)
Hi {{first_name}}, I'd love to connect!

Step 3 — LinkedIn DM#1 (3 Hours after Connection)
Thanks for connecting {{first_name}}!
"""
    result = parse_messaging_file(text)
    steps = result["steps"]
    email_steps = [s for s in steps if s.get("channel", "email") == "email"]
    linkedin_steps = [s for s in steps if s.get("channel") == "linkedin"]
    assert len(email_steps) == 1
    assert email_steps[0]["step_number"] == 1
    assert len(linkedin_steps) == 2
    cr_step = next(s for s in linkedin_steps if s["linkedin_subtype"] == "connection_request")
    dm_step = next(s for s in linkedin_steps if s["linkedin_subtype"] == "dm")
    assert "connect" in cr_step["body_variants"][0]["body"].lower()
    assert "thanks" in dm_step["body_variants"][0]["body"].lower()


def test_parse_step_channel_linkedin_produces_no_warning():
    text = """Title

Step 1 — Email (Day 0)
Body.

Step 2 — LinkedIn DM#1 (Day 3)
DM body.
"""
    result = parse_messaging_file(text)
    assert not any("Skipped" in w for w in result.get("warnings", []))


def test_parse_step_channel_email_step_numbering_unaffected():
    """Email steps still get sequential numbers 1..N for Smartlead."""
    text = """Title

Step 1 — LinkedIn (Connection Request) (Day 0)
Connect note.

Step 2 — Email (Day 1)
First email body.

Step 3 — LinkedIn DM#1 (Day 3)
Follow-up DM.

Step 4 — Email (Day 5)
Second email body.
"""
    result = parse_messaging_file(text)
    email_steps = [s for s in result["steps"] if s.get("channel", "email") == "email"]
    assert [s["step_number"] for s in email_steps] == [1, 2]
```

- [ ] **Step 2: Run tests to verify they fail**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_parser_service.py::test_parse_step_channel_linkedin_connection_request tests/test_parser_service.py::test_parse_step_channel_linkedin_produces_no_warning tests/test_parser_service.py::test_parse_step_channel_email_step_numbering_unaffected -v
```

Expected: FAIL (LinkedIn steps not emitted yet)

- [ ] **Step 3: Implement parser changes**

In `app/services/parser_service.py`, add a helper after `_strip_channel_tail`:

```python
def _linkedin_subtype(header_text: str) -> str:
    """Detect connection_request vs dm from a LinkedIn step header."""
    lower = header_text.lower()
    if "connection" in lower and "request" in lower:
        return "connection_request"
    return "dm"
```

In `_parse_step_channel_format`, replace the `else` branch (lines ~142-146) with:

```python
        else:
            # LinkedIn or other channel — emit as linkedin step with original step number
            raw_step_number = int(match.group(1))
            linkedin_sub = _linkedin_subtype(match.group(0))
            block_body = text[start:end]
            variants = _split_variants(block_body)
            day_match = DAY_RE.search(match.group(0))
            day = int(day_match.group(1)) if day_match else None
            steps.append({
                "step_number": raw_step_number,
                "channel": "linkedin",
                "linkedin_subtype": linkedin_sub,
                "day": day,
                "body_variants": variants,
            })
```

And add `"channel": "email"` to the email step dict (line ~139) so all steps have an explicit channel:

```python
            steps.append({"step_number": email_number, "channel": "email", "day": day, "body_variants": variants})
```

- [ ] **Step 4: Run tests to verify they pass**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_parser_service.py -v
```

Expected: all pass

- [ ] **Step 5: Run full suite**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest -v
```

Expected: 289+ tests pass, none regress

- [ ] **Step 6: Commit**

```bash
git add app/services/parser_service.py tests/test_parser_service.py
git commit -m "feat(parser): emit linkedin steps with channel and linkedin_subtype"
```

---

## Task 2: Plan Builder — Include LinkedIn Steps

**Files:**
- Modify: `app/services/local_plan_service.py`
- Modify: `app/schemas/campaign_plan.py`
- Test: `tests/test_local_plan_service.py`

**Interfaces:**
- Consumes: parsed result from Task 1 — steps list has `channel` and `linkedin_subtype`
- Produces: `CampaignPlan.sequence` contains both `channel="email"` and `channel="linkedin"` steps in the same list; LinkedIn steps carry `linkedin_subtype` in the step dict; `SequenceStep` gains `linkedin_subtype: Literal["connection_request", "dm"] | None = None`
- `sequence_limits` validator: email step cap is 4; LinkedIn cap is 3; total cap is 7; mixed is fine

**Background:** `local_plan_service.py::build_campaign_plan_from_input` calls `_build_step_variants` per step. LinkedIn steps need the same treatment but channel forwarded. `CampaignPlan.sequence_limits` currently rejects `> 4` steps — must be updated.

- [ ] **Step 1: Write failing test**

In `tests/test_local_plan_service.py`:

```python
def test_build_plan_includes_linkedin_steps():
    from app.services.local_plan_service import build_campaign_plan_from_input

    parsed = {
        "source_format": "repository",
        "selected_campaign": "Test Campaign",
        "subjects": ["Subject 1"],
        "steps": [
            {
                "step_number": 1,
                "channel": "email",
                "body_variants": [{"variant_label": "A", "body": "Email body one."}],
            },
            {
                "step_number": 2,
                "channel": "linkedin",
                "linkedin_subtype": "connection_request",
                "body_variants": [{"variant_label": "A", "body": "Connect with me!"}],
            },
            {
                "step_number": 3,
                "channel": "linkedin",
                "linkedin_subtype": "dm",
                "body_variants": [{"variant_label": "A", "body": "Thanks for connecting!"}],
            },
        ],
        "campaigns": [],
        "warnings": [],
    }
    plan, errors = build_campaign_plan_from_input(
        parsed_result=parsed,
        workspace_key="preciselead",
        campaign_name="Test Campaign",
    )
    assert not errors
    sequence = plan["sequence"]
    email_steps = [s for s in sequence if s["channel"] == "email"]
    linkedin_steps = [s for s in sequence if s["channel"] == "linkedin"]
    assert len(email_steps) == 1
    assert len(linkedin_steps) == 2
    cr = next(s for s in linkedin_steps if s["linkedin_subtype"] == "connection_request")
    dm = next(s for s in linkedin_steps if s["linkedin_subtype"] == "dm")
    assert cr["variants"][0]["body"] == "Connect with me!"
    assert dm["variants"][0]["body"] == "Thanks for connecting!"
```

- [ ] **Step 2: Run test to verify it fails**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_local_plan_service.py::test_build_plan_includes_linkedin_steps -v
```

Expected: FAIL

- [ ] **Step 3: Add `linkedin_subtype` to SequenceStep schema**

In `app/schemas/campaign_plan.py`, update `SequenceStep`:

```python
class SequenceStep(BaseModel):
    step_number: int
    delay_days: int
    channel: Literal["email", "linkedin"] = "email"
    linkedin_subtype: Literal["connection_request", "dm"] | None = None
    variants: list[SequenceVariant]
```

And update `sequence_limits` validator to allow mixed campaigns:

```python
    @field_validator("sequence")
    @classmethod
    def sequence_limits(cls, value: list[SequenceStep]) -> list[SequenceStep]:
        if not value:
            raise ValueError("sequence needs at least one step")
        email_steps = [s for s in value if s.channel == "email"]
        linkedin_steps = [s for s in value if s.channel == "linkedin"]
        if len(email_steps) > 4:
            raise ValueError("V1 supports at most 4 email sequence steps")
        if len(linkedin_steps) > 3:
            raise ValueError("LinkedIn sequence supports at most 3 steps")
        return value
```

- [ ] **Step 4: Update `local_plan_service.py` to forward linkedin steps**

Read `app/services/local_plan_service.py` first, then find where steps are built (around `_build_step_variants` loop). The existing loop filters to email steps implicitly. Change it to:

1. Build email steps as before (sequential numbering for Smartlead)
2. Append LinkedIn steps with `channel="linkedin"`, `linkedin_subtype`, and `delay_days=0` (delays are HeyReach-managed)

In `build_campaign_plan_from_input`, after building the email sequence, add:

```python
    # Append LinkedIn steps from parsed result
    for step in parsed_result.get("steps", []):
        if step.get("channel") == "linkedin":
            variants = [
                {"variant_label": v.get("variant_label", "A"), "subject": "", "body": v.get("body", "")}
                for v in (step.get("body_variants") or [])
                if v.get("body", "").strip()
            ]
            if variants:
                sequence_steps.append({
                    "step_number": step["step_number"],
                    "delay_days": 0,
                    "channel": "linkedin",
                    "linkedin_subtype": step.get("linkedin_subtype") or "dm",
                    "variants": variants,
                })
```

- [ ] **Step 5: Run tests**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_local_plan_service.py -v
```

Expected: all pass

- [ ] **Step 6: Run full suite**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest -v
```

Expected: 289+ pass

- [ ] **Step 7: Commit**

```bash
git add app/schemas/campaign_plan.py app/services/local_plan_service.py tests/test_local_plan_service.py
git commit -m "feat(plan): include linkedin steps in campaign plan with channel and subtype"
```

---

## Task 3: Config — Per-Client HeyReach Account Mapping

**Files:**
- Modify: `app/config.py`
- Test: `tests/test_config.py` (create if not exists, else add to existing)

**Interfaces:**
- Produces: `get_heyreach_account_ids_for_client(workspace_key: str, client_name: str | None) -> list[int] | None`
  - Returns list of int account IDs when a mapping exists for the client, `None` when no mapping → caller uses all accounts
  - Reads env var `HEYREACH_{WORKSPACE_KEY_UPPER}_CLIENT_ACCOUNTS` as JSON: `{"ClientName": [101, 102]}`
  - `workspace_key="mythic"` → env var `HEYREACH_MYTHIC_CLIENT_ACCOUNTS`

- [ ] **Step 1: Write failing test**

Create `tests/test_config.py` (or add to existing):

```python
import os
import json
import importlib
import sys

def test_get_heyreach_account_ids_for_client_returns_ids(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    mapping = {"Mythic": [101, 102, 103], "OSC": [201]}
    monkeypatch.setenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", json.dumps(mapping))
    result = get_heyreach_account_ids_for_client("mythic", "Mythic")
    assert result == [101, 102, 103]

def test_get_heyreach_account_ids_for_client_case_insensitive(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    mapping = {"mythic": [101, 102]}
    monkeypatch.setenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", json.dumps(mapping))
    result = get_heyreach_account_ids_for_client("mythic", "MYTHIC")
    assert result == [101, 102]

def test_get_heyreach_account_ids_for_client_none_when_no_match(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    mapping = {"Mythic": [101]}
    monkeypatch.setenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", json.dumps(mapping))
    result = get_heyreach_account_ids_for_client("mythic", "Unknown Client")
    assert result is None

def test_get_heyreach_account_ids_for_client_none_when_no_env(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    monkeypatch.delenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", raising=False)
    result = get_heyreach_account_ids_for_client("mythic", "Mythic")
    assert result is None

def test_get_heyreach_account_ids_none_client_name(monkeypatch):
    from app.config import get_heyreach_account_ids_for_client
    mapping = {"Mythic": [101]}
    monkeypatch.setenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", json.dumps(mapping))
    result = get_heyreach_account_ids_for_client("mythic", None)
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_config.py -v
```

Expected: FAIL (function not defined)

- [ ] **Step 3: Implement in config.py**

Add after `_compact_match_key`:

```python
def get_heyreach_account_ids_for_client(workspace_key: str, client_name: str | None) -> list[int] | None:
    """Return HeyReach account IDs for a named client, or None to use all accounts.

    Reads HEYREACH_{WORKSPACE}_CLIENT_ACCOUNTS env var as JSON dict:
    {"ClientName": [101, 102], "OtherClient": [201]}
    Returns None if env var absent, client_name is None, or client not in mapping.
    """
    if not client_name:
        return None
    env_var = f"HEYREACH_{workspace_key.upper()}_CLIENT_ACCOUNTS"
    raw = get_secret_value(env_var)
    if not raw:
        return None
    try:
        mapping: dict = json.loads(raw)
    except (ValueError, TypeError):
        return None
    client_lower = client_name.lower()
    for key, ids in mapping.items():
        if key.lower() == client_lower:
            return [int(i) for i in ids]
    return None
```

Add `import json` at the top of `app/config.py` (check if already imported first).

- [ ] **Step 4: Run tests**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_config.py -v
```

Expected: all 5 pass

- [ ] **Step 5: Run full suite**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest -v
```

Expected: 294+ pass

- [ ] **Step 6: Commit**

```bash
git add app/config.py tests/test_config.py
git commit -m "feat(config): per-client HeyReach account ID mapping from JSON env var"
```

---

## Task 4: HeyReach Worker — Use Client Account Mapping

**Files:**
- Modify: `app/workers/heyreach_create.py`
- Test: `tests/test_heyreach_create.py`

**Interfaces:**
- Consumes: `get_heyreach_account_ids_for_client(workspace_key, client_name)` from Task 3
- `_create_async(campaign_id)` resolves client name from `doc.get("smartlead_client_name")`, calls mapping helper, filters or uses all accounts

**Background:** Phase 1 `heyreach_create.py` fetches all LinkedIn accounts with `get_linkedin_accounts` and attaches all. Phase 2 adds: after fetching all accounts, call `get_heyreach_account_ids_for_client`; if returns non-None list, filter `all_ids` to the intersection of known IDs; if returns None, keep all.

- [ ] **Step 1: Read current heyreach_create.py**

Read `app/workers/heyreach_create.py` before editing.

- [ ] **Step 2: Write failing test**

In `tests/test_heyreach_create.py`, add:

```python
@pytest.mark.asyncio
async def test_create_heyreach_uses_client_account_mapping(monkeypatch):
    """When client mapping exists, only mapped account IDs are attached."""
    import json
    from unittest.mock import AsyncMock, patch, MagicMock
    from app.workers import heyreach_create

    doc = {
        "_id": ObjectId(),
        "campaign_name": "Mythic Test Campaign",
        "smartlead_workspace": "mythic",
        "smartlead_client_name": "Mythic",
        "current_plan": {
            "sequence": [
                {"step_number": 1, "channel": "linkedin", "linkedin_subtype": "dm",
                 "delay_days": 0, "variants": [{"body": "Hey {{first_name}}!"}]},
            ]
        },
        "heyreach_campaign_id": None,
    }

    mapping = {"Mythic": [201, 202]}
    monkeypatch.setenv("HEYREACH_MYTHIC_API_KEY", "test-key")
    monkeypatch.setenv("HEYREACH_MYTHIC_CLIENT_ACCOUNTS", json.dumps(mapping))

    mock_svc = MagicMock()
    mock_svc.get_linkedin_accounts = AsyncMock(return_value={
        "items": [{"id": 201}, {"id": 202}, {"id": 999}]
    })
    mock_svc.create_empty_list = AsyncMock(return_value={"id": 55})
    mock_svc.create_campaign = AsyncMock(return_value={"id": 999})

    with patch("app.workers.heyreach_create.store") as mock_store, \
         patch("app.workers.heyreach_create.HeyReachService", return_value=mock_svc), \
         patch("app.workers.heyreach_create.get_workspace_config", return_value={
             "key": "mythic", "heyreach_api_key": "test-key"
         }):
        mock_store.get_campaign.return_value = doc
        mock_store.save_heyreach_result = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()

        await heyreach_create._create_async(str(doc["_id"]))

    call_args = mock_svc.create_campaign.call_args
    account_ids_used = call_args[1].get("account_ids") or call_args[0][2]
    assert sorted(account_ids_used) == [201, 202]
    assert 999 not in account_ids_used


@pytest.mark.asyncio
async def test_create_heyreach_uses_all_accounts_when_no_mapping(monkeypatch):
    """When no client mapping, all accounts are attached."""
    from unittest.mock import AsyncMock, patch, MagicMock
    from app.workers import heyreach_create

    doc = {
        "_id": ObjectId(),
        "campaign_name": "No Mapping Campaign",
        "smartlead_workspace": "preciselead",
        "smartlead_client_name": None,
        "current_plan": {
            "sequence": [
                {"step_number": 1, "channel": "linkedin", "linkedin_subtype": "dm",
                 "delay_days": 0, "variants": [{"body": "Hey there!"}]},
            ]
        },
        "heyreach_campaign_id": None,
    }

    monkeypatch.setenv("HEYREACH_PRECISELEAD_API_KEY", "test-key")
    monkeypatch.delenv("HEYREACH_PRECISELEAD_CLIENT_ACCOUNTS", raising=False)

    mock_svc = MagicMock()
    mock_svc.get_linkedin_accounts = AsyncMock(return_value={
        "items": [{"id": 101}, {"id": 102}]
    })
    mock_svc.create_empty_list = AsyncMock(return_value={"id": 10})
    mock_svc.create_campaign = AsyncMock(return_value={"id": 42})

    with patch("app.workers.heyreach_create.store") as mock_store, \
         patch("app.workers.heyreach_create.HeyReachService", return_value=mock_svc), \
         patch("app.workers.heyreach_create.get_workspace_config", return_value={
             "key": "preciselead", "heyreach_api_key": "test-key"
         }):
        mock_store.get_campaign.return_value = doc
        mock_store.save_heyreach_result = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()

        await heyreach_create._create_async(str(doc["_id"]))

    call_args = mock_svc.create_campaign.call_args
    account_ids_used = call_args[1].get("account_ids") or call_args[0][2]
    assert sorted(account_ids_used) == [101, 102]
```

- [ ] **Step 3: Run tests to verify they fail**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_create.py::test_create_heyreach_uses_client_account_mapping tests/test_heyreach_create.py::test_create_heyreach_uses_all_accounts_when_no_mapping -v
```

Expected: FAIL

- [ ] **Step 4: Implement account filtering in heyreach_create.py**

After `all_ids = _account_ids(accounts_response)` in `_create_async`, add:

```python
        from app.config import get_heyreach_account_ids_for_client
        client_name = doc.get("smartlead_client_name")
        workspace_key = doc.get("smartlead_workspace", "")
        mapped_ids = get_heyreach_account_ids_for_client(workspace_key, client_name)
        if mapped_ids is not None:
            # Filter to intersection: only use IDs that are both mapped AND exist in HeyReach
            all_ids = [i for i in all_ids if i in mapped_ids]
            if not all_ids:
                # Mapped IDs don't exist in this workspace — fall back to all rather than erroring
                all_ids = _account_ids(accounts_response)
```

- [ ] **Step 5: Run tests**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_create.py -v
```

Expected: all pass

- [ ] **Step 6: Run full suite**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest -v
```

Expected: 296+ pass

- [ ] **Step 7: Commit**

```bash
git add app/workers/heyreach_create.py tests/test_heyreach_create.py
git commit -m "feat(heyreach): filter LinkedIn senders by per-client account mapping"
```

---

## Task 5: Sync Worker — Auto-Create HeyReach After Smartlead

**Files:**
- Modify: `app/workers/sync_campaign.py`
- Test: `tests/test_sync_campaign.py` (create if not exists)

**Interfaces:**
- Consumes: `linkedin_messages(plan)` from `app/schemas/campaign_plan.py`, `create_heyreach_campaign_now` from `app/workers/heyreach_create.py`, `store.set_heyreach_creating`
- After `store.attach_smartlead(campaign_id, smartlead_id)` succeeds, call `_maybe_create_heyreach(campaign_id, plan)` — inline, same background task
- `_maybe_create_heyreach(campaign_id, plan)`: if `linkedin_messages(plan)` non-empty, sets `heyreach_creating=True`, then calls `create_heyreach_campaign_now(campaign_id)` synchronously (it's already `asyncio.run(...)` wrapped, so call `await _create_async(campaign_id)` directly from async context)
- If no LinkedIn steps → do nothing (no error, no flag set)
- If HeyReach creation fails → store `heyreach_last_error` but do NOT mark Smartlead sync as failed

- [ ] **Step 1: Write failing tests**

Create `tests/test_sync_campaign.py`:

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock, call
from bson import ObjectId


def _make_doc(with_linkedin: bool = True):
    steps = [
        {"step_number": 1, "channel": "email", "delay_days": 0,
         "variants": [{"variant_label": "A", "subject": "Sub", "body": "Email body."}]},
    ]
    if with_linkedin:
        steps.append({
            "step_number": 2, "channel": "linkedin", "linkedin_subtype": "dm",
            "delay_days": 0, "variants": [{"variant_label": "A", "body": "LinkedIn DM!"}],
        })
    return {
        "_id": ObjectId(),
        "campaign_name": "Test Campaign",
        "smartlead_workspace": "preciselead",
        "smartlead_client_name": None,
        "smartlead_campaign_id": None,
        "current_plan": {
            "workspace_key": "preciselead",
            "campaign_name": "Test Campaign",
            "sequence": steps,
            "schedule": {
                "timezone": "America/New_York",
                "days_of_the_week": [1, 2, 3, 4, 5],
                "start_hour": "09:00",
                "end_hour": "18:00",
                "min_time_btw_emails": 17,
                "max_new_leads_per_day": 100,
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
        },
        "is_twin": False,
    }


@pytest.mark.asyncio
async def test_sync_triggers_heyreach_when_linkedin_steps_present():
    """Smartlead sync auto-triggers HeyReach creation when plan has LinkedIn steps."""
    from app.workers.sync_campaign import _sync_campaign_async

    doc = _make_doc(with_linkedin=True)
    campaign_id = str(doc["_id"])

    mock_smartlead = MagicMock()
    mock_smartlead.create_campaign = AsyncMock(return_value={"id": 123})
    mock_smartlead.apply_v1_settings = AsyncMock()
    mock_smartlead.update_schedule = AsyncMock()
    mock_smartlead.update_sequences = AsyncMock()
    mock_smartlead.get_sequences = AsyncMock(return_value={"data": [
        {"seq_number": 1, "sequence_variants": [{"variant_label": "A", "subject": "Sub", "email_body": "Email body."}]}
    ]})
    mock_smartlead.attach_email_accounts = AsyncMock()

    heyreach_called_with = []

    async def fake_heyreach_async(cid):
        heyreach_called_with.append(cid)

    with patch("app.workers.sync_campaign.store") as mock_store, \
         patch("app.workers.sync_campaign.SmartleadService", return_value=mock_smartlead), \
         patch("app.workers.sync_campaign.get_workspace_config", return_value={
             "key": "preciselead", "api_key": "sl-key", "self_client_name": "PreciseLeads"
         }), \
         patch("app.workers.sync_campaign.validate_campaign_plan", return_value=[]), \
         patch("app.workers.sync_campaign._create_async", side_effect=fake_heyreach_async):

        mock_store.get_campaign.return_value = doc
        mock_store.campaigns_collection.return_value.update_one = MagicMock()
        mock_store.campaigns_collection.return_value.find_one_and_update = MagicMock()
        mock_store.attach_smartlead = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()
        mock_store.now_utc = MagicMock()

        await _sync_campaign_async(campaign_id)

    assert heyreach_called_with == [campaign_id]
    mock_store.set_heyreach_creating.assert_called_once_with(campaign_id, True)


@pytest.mark.asyncio
async def test_sync_skips_heyreach_when_no_linkedin_steps():
    """Smartlead sync does NOT trigger HeyReach when plan has no LinkedIn steps."""
    from app.workers.sync_campaign import _sync_campaign_async

    doc = _make_doc(with_linkedin=False)
    campaign_id = str(doc["_id"])

    mock_smartlead = MagicMock()
    mock_smartlead.create_campaign = AsyncMock(return_value={"id": 456})
    mock_smartlead.apply_v1_settings = AsyncMock()
    mock_smartlead.update_schedule = AsyncMock()
    mock_smartlead.update_sequences = AsyncMock()
    mock_smartlead.get_sequences = AsyncMock(return_value={"data": [
        {"seq_number": 1, "sequence_variants": [{"variant_label": "A", "subject": "Sub", "email_body": "Email body."}]}
    ]})

    heyreach_called = []

    async def fake_heyreach_async(cid):
        heyreach_called.append(cid)

    with patch("app.workers.sync_campaign.store") as mock_store, \
         patch("app.workers.sync_campaign.SmartleadService", return_value=mock_smartlead), \
         patch("app.workers.sync_campaign.get_workspace_config", return_value={
             "key": "preciselead", "api_key": "sl-key", "self_client_name": "PreciseLeads"
         }), \
         patch("app.workers.sync_campaign.validate_campaign_plan", return_value=[]), \
         patch("app.workers.sync_campaign._create_async", side_effect=fake_heyreach_async):

        mock_store.get_campaign.return_value = doc
        mock_store.campaigns_collection.return_value.update_one = MagicMock()
        mock_store.campaigns_collection.return_value.find_one_and_update = MagicMock()
        mock_store.attach_smartlead = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()
        mock_store.now_utc = MagicMock()

        await _sync_campaign_async(campaign_id)

    assert heyreach_called == []
    mock_store.set_heyreach_creating.assert_not_called()


@pytest.mark.asyncio
async def test_sync_heyreach_failure_does_not_fail_smartlead():
    """HeyReach creation error doesn't mark the Smartlead sync as failed."""
    from app.workers.sync_campaign import _sync_campaign_async

    doc = _make_doc(with_linkedin=True)
    campaign_id = str(doc["_id"])

    mock_smartlead = MagicMock()
    mock_smartlead.create_campaign = AsyncMock(return_value={"id": 789})
    mock_smartlead.apply_v1_settings = AsyncMock()
    mock_smartlead.update_schedule = AsyncMock()
    mock_smartlead.update_sequences = AsyncMock()
    mock_smartlead.get_sequences = AsyncMock(return_value={"data": [
        {"seq_number": 1, "sequence_variants": [{"variant_label": "A", "subject": "Sub", "email_body": "Email body."}]}
    ]})

    async def boom(cid):
        raise RuntimeError("HeyReach API down")

    with patch("app.workers.sync_campaign.store") as mock_store, \
         patch("app.workers.sync_campaign.SmartleadService", return_value=mock_smartlead), \
         patch("app.workers.sync_campaign.get_workspace_config", return_value={
             "key": "preciselead", "api_key": "sl-key", "self_client_name": "PreciseLeads"
         }), \
         patch("app.workers.sync_campaign.validate_campaign_plan", return_value=[]), \
         patch("app.workers.sync_campaign._create_async", side_effect=boom):

        mock_store.get_campaign.return_value = doc
        mock_store.campaigns_collection.return_value.update_one = MagicMock()
        mock_store.campaigns_collection.return_value.find_one_and_update = MagicMock()
        mock_store.attach_smartlead = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()
        mock_store.save_heyreach_result = MagicMock()
        mock_store.mark_sync_failed = MagicMock()
        mock_store.now_utc = MagicMock()

        await _sync_campaign_async(campaign_id)

    # Smartlead attach still called (sync succeeded)
    mock_store.attach_smartlead.assert_called_once()
    # Smartlead sync NOT marked failed
    mock_store.mark_sync_failed.assert_not_called()
    # HeyReach error stored separately
    mock_store.save_heyreach_result.assert_called_once()
    err_kwarg = mock_store.save_heyreach_result.call_args[1].get("error") or mock_store.save_heyreach_result.call_args[0]
    assert err_kwarg  # error text populated
```

- [ ] **Step 2: Run tests to verify they fail**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_sync_campaign.py -v
```

Expected: FAIL (imports missing, logic not implemented)

- [ ] **Step 3: Implement in sync_campaign.py**

Add imports at top of `app/workers/sync_campaign.py`:

```python
from app.schemas.campaign_plan import linkedin_messages
from app.workers.heyreach_create import _create_async
```

Add helper after `_sync_campaign_async`:

```python
async def _maybe_create_heyreach(campaign_id: str, plan: dict) -> None:
    """If the plan has LinkedIn steps, run HeyReach campaign creation inline.

    Errors here do NOT propagate — Smartlead sync is already complete.
    HeyReach errors are stored via save_heyreach_result.
    """
    messages = linkedin_messages(plan)
    if not messages:
        return
    store.set_heyreach_creating(campaign_id, True)
    try:
        await _create_async(campaign_id)
    except Exception as exc:
        store.save_heyreach_result(
            campaign_id,
            campaign_id_value=None,
            url=None,
            status="failed",
            error=_error_text(exc),
        )
```

In `_sync_campaign_async`, after `store.attach_smartlead(campaign_id, smartlead_id)` and before the end of the `try` block, add:

```python
        await _maybe_create_heyreach(campaign_id, plan)
```

- [ ] **Step 4: Run tests**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_sync_campaign.py -v
```

Expected: all 3 pass

- [ ] **Step 5: Run full suite**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest -v
```

Expected: 299+ pass

- [ ] **Step 6: Commit**

```bash
git add app/workers/sync_campaign.py tests/test_sync_campaign.py
git commit -m "feat(sync): auto-create HeyReach campaign after Smartlead sync when LinkedIn steps present"
```

---

## Task 6: UI — Remove Manual HeyReach Panel, Show Status in Sync Result

**Files:**
- Modify: `app/templates/campaign_detail.html`
- Modify: `app/routes/campaigns.py`
- Test: `tests/test_campaign_routes.py`

**Interfaces:**
- Consumes: `doc["heyreach_campaign_url"]`, `doc["heyreach_status"]`, `doc["heyreach_last_error"]`, `doc["heyreach_creating"]` (all already in store and `_detail_payload`)
- `campaign_status` endpoint gains `has_linkedin_steps: bool` so the poller knows to wait for HeyReach too
- Manual "Create LinkedIn campaign" button and LinkedIn message textarea panel are removed
- After sync, if campaign has LinkedIn steps, the sync result area shows:
  - While `heyreach_creating=True`: spinner "Creating HeyReach campaign..."
  - On completion: link to HeyReach campaign URL or error message
- Poller already polls `campaign_status` every 3s and reloads on `heyreach_creating → false`

- [ ] **Step 1: Read current routes and template**

Read `app/routes/campaigns.py` and `app/templates/campaign_detail.html` before editing.

- [ ] **Step 2: Write route test**

In `tests/test_campaign_routes.py` add:

```python
def test_campaign_status_includes_has_linkedin_steps(client, sample_campaign_doc):
    """Status endpoint reports has_linkedin_steps=True when plan has LinkedIn steps."""
    # Insert campaign with LinkedIn steps in plan
    from app import store as app_store
    plan_with_li = dict(sample_campaign_doc.get("current_plan") or {})
    plan_with_li["sequence"] = plan_with_li.get("sequence", []) + [{
        "step_number": 99, "channel": "linkedin", "delay_days": 0,
        "linkedin_subtype": "dm", "variants": [{"body": "DM body"}]
    }]
    campaign_id = str(sample_campaign_doc["_id"])
    app_store.update_plan(campaign_id, plan_with_li, [])

    resp = client.get(f"/api/campaigns/{campaign_id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_linkedin_steps"] is True
```

- [ ] **Step 3: Update `campaign_status` route**

In `app/routes/campaigns.py`, in the `campaign_status` function, add to the returned dict:

```python
    "has_linkedin_steps": bool(linkedin_messages(doc.get("current_plan") or {})),
```

(Import `linkedin_messages` is already present from Phase 1.)

- [ ] **Step 4: Update template**

In `app/templates/campaign_detail.html`:

1. **Remove** the entire LinkedIn (HeyReach) panel section (the `<div>` containing the 3 message textareas, save form, and create button). This was added in Phase 1. The manual `POST /api/campaigns/{id}/heyreach-create` button is gone.

2. **Add** a HeyReach status block inside the sync result area. After the Smartlead sync success message, insert:

```html
{% if payload.has_linkedin_steps %}
<div id="heyreach-status-block" class="heyreach-status-row">
  {% if payload.heyreach_creating %}
    <span class="spinner"></span> Creating HeyReach campaign&hellip;
  {% elif payload.heyreach_campaign_url %}
    <span class="status-ok">HeyReach campaign created (DRAFT)</span>
    &mdash; <a href="{{ payload.heyreach_campaign_url }}" target="_blank" rel="noopener">Open in HeyReach</a>
    &mdash; add leads and start it there.
  {% elif payload.heyreach_last_error %}
    <span class="status-error">HeyReach failed:</span> {{ payload.heyreach_last_error }}
  {% else %}
    <span class="status-muted">LinkedIn steps detected &mdash; HeyReach campaign will be created on sync.</span>
  {% endif %}
</div>
{% endif %}
```

3. The existing HeyReach poller script (polls status every 3s, reloads when `heyreach_creating → false`) already handles the auto-refresh. Confirm it references `heyreach_creating` from the status payload — if it does, no changes needed to the JS.

- [ ] **Step 5: Run tests**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py -v
```

Expected: all pass

- [ ] **Step 6: Run full suite**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest -v
```

Expected: 300+ pass

- [ ] **Step 7: Commit**

```bash
git add app/templates/campaign_detail.html app/routes/campaigns.py tests/test_campaign_routes.py
git commit -m "feat(ui): replace manual HeyReach panel with auto-triggered status block on sync"
```

---

## Task 7: Sequence Builder — Support Connection Request Note

**Files:**
- Modify: `app/services/heyreach_sequence_builder.py`
- Modify: `app/workers/heyreach_create.py`
- Test: `tests/test_heyreach_sequence_builder.py`

**Background:** When a LinkedIn step has `linkedin_subtype="connection_request"`, its body should be used as the connection request note (the brief text that accompanies the connection invite). Currently `CONNECTION_REQUEST.payload.messages` is always `[""]`. Phase 2 passes the note through.

**Interfaces:**
- `build_linkedin_sequence(messages, *, connection_note="", withdraw_days=25)` — new optional `connection_note` param; sets `CONNECTION_REQUEST.payload.messages[0]` to the note (after merge-tag translation); blank string = no note (existing behavior)
- `heyreach_create._create_async` extracts connection_note from the `connection_request` subtype step and passes it to `build_linkedin_sequence`

- [ ] **Step 1: Write failing tests**

In `tests/test_heyreach_sequence_builder.py`:

```python
def test_build_linkedin_sequence_connection_note_used():
    from app.services.heyreach_sequence_builder import build_linkedin_sequence
    tree = build_linkedin_sequence(
        ["Follow-up message"],
        connection_note="Hi {{first_name}}, I'd love to connect!"
    )
    # Find the CONNECTION_REQUEST node
    cr_node = tree["unconditionalNode"]["unconditionalNode"]
    assert cr_node["nodeType"] == "CONNECTION_REQUEST"
    note = cr_node["payload"]["messages"][0]
    assert "{FIRST_NAME}" in note
    assert note != ""


def test_build_linkedin_sequence_blank_note_default():
    from app.services.heyreach_sequence_builder import build_linkedin_sequence
    tree = build_linkedin_sequence(["DM body"])
    cr_node = tree["unconditionalNode"]["unconditionalNode"]
    assert cr_node["payload"]["messages"] == [""]
```

- [ ] **Step 2: Run tests to verify they fail**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_sequence_builder.py::test_build_linkedin_sequence_connection_note_used tests/test_heyreach_sequence_builder.py::test_build_linkedin_sequence_blank_note_default -v
```

Expected: FAIL (no `connection_note` param)

- [ ] **Step 3: Update sequence builder**

In `app/services/heyreach_sequence_builder.py`, update `build_linkedin_sequence`:

```python
def build_linkedin_sequence(
    messages: list[str],
    *,
    connection_note: str = "",
    withdraw_days: int = _WITHDRAW_DAYS_DEFAULT,
) -> dict:
    if not messages or len(messages) > 3:
        raise ValueError("LinkedIn sequence needs 1 to 3 messages")
    note_text, _ = to_heyreach_message(connection_note) if connection_note.strip() else ("", "")
    return {
        "nodeType": "CHECK_IS_CONNECTION",
        "actionDelay": 0,
        "actionDelayUnit": "HOUR",
        "conditionalNode": _message_chain(messages, 0),
        "unconditionalNode": {
            "nodeType": "VIEW_PROFILE",
            "actionDelay": 3,
            "actionDelayUnit": "HOUR",
            "unconditionalNode": {
                "nodeType": "CONNECTION_REQUEST",
                "actionDelay": 3,
                "actionDelayUnit": "HOUR",
                "payload": {
                    "messages": [note_text],
                    "fallbackMessage": "",
                    "toBeWithdrawnAfterDays": withdraw_days,
                },
                "conditionalNode": _message_chain(messages, 0),
                "unconditionalNode": {
                    **_like_post(2, "DAY"),
                    "unconditionalNode": _end(1, "DAY"),
                },
            },
        },
    }
```

- [ ] **Step 4: Update heyreach_create._create_async to pass connection_note**

In `app/workers/heyreach_create.py`, in `_create_async`, before calling `build_linkedin_sequence`, extract connection note:

```python
        from app.schemas.campaign_plan import linkedin_messages
        # Extract DM messages (exclude connection_request step from message chain)
        sequence_steps = (plan.get("sequence") or [])
        dm_steps = sorted(
            [s for s in sequence_steps if s.get("channel") == "linkedin" and s.get("linkedin_subtype") != "connection_request"],
            key=lambda s: s.get("step_number", 0)
        )
        cr_steps = [s for s in sequence_steps if s.get("channel") == "linkedin" and s.get("linkedin_subtype") == "connection_request"]
        dm_messages = [
            (s.get("variants") or [{}])[0].get("body", "")
            for s in dm_steps
            if (s.get("variants") or [{}])[0].get("body", "").strip()
        ]
        connection_note = ""
        if cr_steps:
            cr_body = (cr_steps[0].get("variants") or [{}])[0].get("body", "")
            connection_note = cr_body.strip()

        if not dm_messages:
            store.save_heyreach_result(
                campaign_id, campaign_id_value=None, url=None,
                status="failed", error="No LinkedIn DM steps found in plan"
            )
            return

        sequence = build_linkedin_sequence(dm_messages, connection_note=connection_note)
```

Note: this replaces the existing `linkedin_messages(plan)` call in `_create_async`. The old helper only returned bodies of ALL linkedin steps; now we need to separate CR from DM.

- [ ] **Step 5: Run tests**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_sequence_builder.py tests/test_heyreach_create.py -v
```

Expected: all pass

- [ ] **Step 6: Run full suite**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest -v
```

Expected: 302+ pass

- [ ] **Step 7: Commit**

```bash
git add app/services/heyreach_sequence_builder.py app/workers/heyreach_create.py tests/test_heyreach_sequence_builder.py
git commit -m "feat(heyreach): pass connection request note from step body to sequence builder"
```

---

## Task 8: Sequence Builder — Email-Only Steps Still Go to Smartlead Only

**Files:**
- Modify: `app/services/sequence_builder.py` (existing Smartlead sequence builder)
- Test: `tests/test_sequence_builder.py`

**Background:** `build_smartlead_sequences(plan["sequence"])` in `sync_campaign.py` currently receives the full sequence list. With LinkedIn steps now in the plan, we must skip them — Smartlead only processes email steps.

**Interfaces:**
- `build_smartlead_sequences(sequence: list[dict]) -> list` — already exists; add filter: skip steps where `step.get("channel") == "linkedin"`

- [ ] **Step 1: Read sequence_builder.py**

Read `app/services/sequence_builder.py` to find `build_smartlead_sequences`.

- [ ] **Step 2: Write failing test**

In `tests/test_sequence_builder.py`:

```python
def test_build_smartlead_sequences_skips_linkedin_steps():
    from app.services.sequence_builder import build_smartlead_sequences
    sequence = [
        {"step_number": 1, "channel": "email", "delay_days": 0,
         "variants": [{"variant_label": "A", "subject": "Sub", "body": "Email body."}]},
        {"step_number": 2, "channel": "linkedin", "delay_days": 0,
         "linkedin_subtype": "dm", "variants": [{"variant_label": "A", "body": "DM body."}]},
        {"step_number": 3, "channel": "email", "delay_days": 3,
         "variants": [{"variant_label": "A", "subject": "FU", "body": "Follow-up."}]},
    ]
    result = build_smartlead_sequences(sequence)
    # Only email steps should produce Smartlead sequences
    assert len(result) == 2
    seq_numbers = [s.get("seq_number") or s.get("step_number") for s in result]
    assert 2 not in seq_numbers  # LinkedIn step excluded
```

- [ ] **Step 3: Run test to verify it fails**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_sequence_builder.py::test_build_smartlead_sequences_skips_linkedin_steps -v
```

Expected: FAIL (LinkedIn step included, len == 3)

- [ ] **Step 4: Add filter in sequence_builder.py**

In `build_smartlead_sequences`, add at the top:

```python
    sequence = [step for step in sequence if step.get("channel", "email") == "email"]
```

- [ ] **Step 5: Run full suite**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest -v
```

Expected: 303+ pass, none regress

- [ ] **Step 6: Commit**

```bash
git add app/services/sequence_builder.py tests/test_sequence_builder.py
git commit -m "fix(sequence_builder): skip linkedin steps when building Smartlead sequences"
```

---

## Task 9: End-to-End Regression + .env.example Update

**Files:**
- Modify: `.env.example` (or `.env.sample` — check which exists)
- Verify: all tests pass

**Background:** New env vars need documentation so future ops know to set them.

- [ ] **Step 1: Update .env.example**

Find `.env.example` or `.env.sample`:

```
GLOB: .env*
```

Add new keys (in the HeyReach section):

```
# HeyReach API keys per workspace
HEYREACH_PRECISELEAD_API_KEY=
HEYREACH_BELARDI_WONG_API_KEY=
HEYREACH_DARLEAN_API_KEY=
HEYREACH_MYTHIC_API_KEY=

# Per-workspace client→LinkedIn account ID mapping (JSON)
# Format: {"ClientName": [accountId1, accountId2], "OtherClient": [accountId3]}
HEYREACH_PRECISELEAD_CLIENT_ACCOUNTS=
HEYREACH_BELARDI_WONG_CLIENT_ACCOUNTS=
HEYREACH_DARLEAN_CLIENT_ACCOUNTS=
HEYREACH_MYTHIC_CLIENT_ACCOUNTS=
```

- [ ] **Step 2: Run complete test suite**

```
PYTHONPATH=. .venv/Scripts/python.exe -m pytest -v
```

Expected: 303+ pass, 0 fail

- [ ] **Step 3: Final commit**

```bash
git add .env.example  # or .env.sample
git commit -m "docs: add HeyReach env vars and client account mapping keys to .env.example"
```

---

## Spec Self-Review Checklist

**Spec coverage:**
- [x] Parser detects LinkedIn vs email from step headers → Task 1
- [x] `connection_request` vs `dm` subtype → Task 1 + Task 7
- [x] LinkedIn steps in plan → Task 2
- [x] Per-client account mapping via JSON env → Task 3 + Task 4
- [x] Auto-create HeyReach on sync → Task 5
- [x] HeyReach failure doesn't fail Smartlead → Task 5 test 3
- [x] Email steps not sent to HeyReach, LinkedIn not sent to Smartlead → Task 8
- [x] UI shows HeyReach status alongside sync → Task 6
- [x] Manual create button removed → Task 6
- [x] .env.example updated → Task 9
- [x] Connection note from CR step → Task 7

**Type consistency:** `linkedin_subtype` used consistently as `"connection_request"` / `"dm"` string literals across parser, schema, worker. `build_linkedin_sequence` param name `connection_note` used in Task 7 throughout.

**Placeholder scan:** None found. All steps have actual code.
