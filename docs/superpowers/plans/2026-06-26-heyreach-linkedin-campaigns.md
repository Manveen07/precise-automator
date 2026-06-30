# HeyReach LinkedIn Campaign Creation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** From a precise-automator campaign that has LinkedIn message steps, create a HeyReach DRAFT LinkedIn campaign (empty lead list, all senders attached, a proven branching sequence built from the message bodies) and surface its URL.

**Architecture:** A pure `heyreach_sequence_builder` turns 1–3 message bodies into HeyReach's branching node tree. A `HeyReachService` (httpx, mirrors `SmartleadService`) calls the HeyReach public API. A background `heyreach_create` worker (mirrors `sync_campaign`) wires list + senders + sequence into a DRAFT campaign and persists its id/url. The plan model gains a per-step `channel`; the detail page gets a LinkedIn panel + create button.

**Tech Stack:** Python 3.10+, FastAPI, Jinja2, pymongo (mongomock in tests), httpx, pytest. Tests run from `precise-automator/` with `PYTHONPATH=. .venv/Scripts/python.exe -m pytest`.

## Global Constraints

- No connection note: `CONNECTION_REQUEST` payload `messages:[""]`, `fallbackMessage:""`, `toBeWithdrawnAfterDays:25`.
- Empty lead list — the app never pushes leads.
- Auto-attach ALL LinkedIn senders in the workspace.
- Create in DRAFT — never auto-start. Return the HeyReach campaign URL.
- Message text = the campaign's `channel=="linkedin"` step bodies, in `step_number` order. Max 3.
- Merge tags: `{{first_name}}`→`{FIRST_NAME}`, `{{company}}`/`{{company_name}}`→`{COMPANY}`. `%signature%` stripped. Every MESSAGE also gets a `fallbackMessage` (tags neutralized: `there` / `your company`).
- Delay rules: every node that follows an action node has `actionDelay >= 3` (HOUR) or a DAY value. `0` only on the first node and on MESSAGE reply-exit END nodes. All paths end in END.
- HeyReach API: `https://api.heyreach.io/api/public`, header `X-API-KEY`. Exact endpoint paths/verbs are confirmed against the HeyReach public API docs at implementation; tests mock the HTTP layer so they're path-agnostic where noted.
- TDD: failing test first, watch it fail, implement, watch it pass, commit. Mock all HTTP — no live HeyReach calls in tests.

---

### Task 1: `heyreach_sequence_builder` (pure node-tree builder)

**Files:**
- Create: `app/services/heyreach_sequence_builder.py`
- Test: `tests/test_heyreach_sequence_builder.py`

**Interfaces:**
- Produces:
  - `build_linkedin_sequence(messages: list[str], *, withdraw_days: int = 25) -> dict`
  - `to_heyreach_message(body: str) -> tuple[str, str]`  # (message, fallbackMessage)

- [ ] **Step 1: Write the failing test** — create `tests/test_heyreach_sequence_builder.py`:

```python
import pytest

from app.services.heyreach_sequence_builder import (
    build_linkedin_sequence,
    to_heyreach_message,
)


def _collect_node_types(node, acc):
    if not isinstance(node, dict):
        return
    acc.append(node.get("nodeType"))
    for key in ("conditionalNode", "unconditionalNode"):
        if node.get(key):
            _collect_node_types(node[key], acc)


def _all_leaves_are_end(node):
    """Every path must terminate in an END node."""
    if not isinstance(node, dict):
        return True
    cond, uncond = node.get("conditionalNode"), node.get("unconditionalNode")
    if cond is None and uncond is None:
        return node.get("nodeType") == "END"
    return all(_all_leaves_are_end(c) for c in (cond, uncond) if c is not None)


def _delays_valid(node):
    """Post-action nodes need delay >= 3h or any DAY value; 0 only allowed on
    the first CHECK_IS_CONNECTION and on MESSAGE reply-exit END nodes."""
    def walk(n, is_root):
        if not isinstance(n, dict):
            return True
        delay = n.get("actionDelay", 0)
        unit = n.get("actionDelayUnit", "HOUR")
        zero_ok = is_root or n.get("nodeType") == "END"  # reply-exit ENDs use 0
        if not zero_ok and unit == "HOUR" and delay < 3:
            return False
        return all(walk(n.get(k), False) for k in ("conditionalNode", "unconditionalNode") if n.get(k))
    return walk(node, True)


def test_to_heyreach_message_translates_tags_and_builds_fallback():
    msg, fb = to_heyreach_message("Hi {{first_name}} at {{company}} — quick idea.%signature%")
    assert msg == "Hi {FIRST_NAME} at {COMPANY} — quick idea."
    assert fb == "Hi there at your company — quick idea."


def test_to_heyreach_message_handles_company_name_alias():
    msg, fb = to_heyreach_message("For {{company_name}}.")
    assert msg == "For {COMPANY}."
    assert fb == "For your company."


def test_single_message_sequence_shape():
    seq = build_linkedin_sequence(["Hi {{first_name}}"])
    assert seq["nodeType"] == "CHECK_IS_CONNECTION"
    assert seq["conditionalNode"]["nodeType"] == "MESSAGE"          # already connected -> message
    notc = seq["unconditionalNode"]
    assert notc["nodeType"] == "VIEW_PROFILE"
    cr = notc["unconditionalNode"]
    assert cr["nodeType"] == "CONNECTION_REQUEST"
    assert cr["payload"] == {"messages": [""], "fallbackMessage": "", "toBeWithdrawnAfterDays": 25}
    assert cr["conditionalNode"]["nodeType"] == "MESSAGE"           # accepted -> message
    # not accepted -> wait (LIKE_POST) then END
    assert cr["unconditionalNode"]["nodeType"] == "LIKE_POST"
    assert cr["unconditionalNode"]["unconditionalNode"]["nodeType"] == "END"
    assert _all_leaves_are_end(seq)
    assert _delays_valid(seq)


def test_message_node_has_reply_exit_and_payload():
    seq = build_linkedin_sequence(["Hello {{first_name}}"])
    msg = seq["conditionalNode"]  # connected branch's first MESSAGE
    assert msg["payload"]["messages"] == ["Hello {FIRST_NAME}"]
    assert msg["payload"]["fallbackMessage"] == "Hello there"
    assert msg["conditionalNode"]["nodeType"] == "END"   # replied -> END
    assert msg["unconditionalNode"]["nodeType"] == "END" # single message -> END after


def test_two_messages_interleave_like_post():
    seq = build_linkedin_sequence(["m1 {{first_name}}", "m2"])
    chain = seq["conditionalNode"]              # MESSAGE_1
    assert chain["nodeType"] == "MESSAGE"
    inter = chain["unconditionalNode"]          # interaction between 1 and 2
    assert inter["nodeType"] == "LIKE_POST"
    assert inter["unconditionalNode"]["nodeType"] == "MESSAGE"   # MESSAGE_2
    assert _all_leaves_are_end(seq)
    assert _delays_valid(seq)


def test_three_messages_use_two_interactions():
    seq = build_linkedin_sequence(["a", "b", "c"])
    chain = seq["conditionalNode"]
    types = []
    _collect_node_types(chain, types)
    assert types.count("MESSAGE") == 3
    assert "LIKE_POST" in types and "VIEW_PROFILE" in types
    assert _all_leaves_are_end(seq)
    assert _delays_valid(seq)


def test_rejects_empty_and_too_many():
    with pytest.raises(ValueError):
        build_linkedin_sequence([])
    with pytest.raises(ValueError):
        build_linkedin_sequence(["a", "b", "c", "d"])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_sequence_builder.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.heyreach_sequence_builder'`.

- [ ] **Step 3: Write the implementation** — create `app/services/heyreach_sequence_builder.py`:

```python
"""Build HeyReach LinkedIn campaign sequences (branching node trees).

Based on a proven live HeyReach template. Entry checks connection, then both the
already-connected and the newly-accepted paths run the same N-message chain.
Pure logic — no I/O.
"""

_WITHDRAW_DAYS_DEFAULT = 25


def to_heyreach_message(body: str) -> tuple[str, str]:
    """Return (message, fallbackMessage) for a HeyReach MESSAGE payload.

    Translates app merge tags to HeyReach's {FIRST_NAME}/{COMPANY}; the fallback
    neutralizes them (used when a lead field is missing). Strips %signature%.
    """
    text = (body or "")
    for sig in ("%signature%", "%Signature%", "%SIGNATURE%"):
        text = text.replace(sig, "")
    text = text.strip()

    def render(first: str, company: str) -> str:
        return (
            text.replace("{{first_name}}", first)
            .replace("{{company_name}}", company)
            .replace("{{company}}", company)
        )

    message = render("{FIRST_NAME}", "{COMPANY}")
    fallback = render("there", "your company")
    return message, fallback


def _like_post(delay: int = 2, unit: str = "DAY") -> dict:
    return {
        "nodeType": "LIKE_POST",
        "actionDelay": delay,
        "actionDelayUnit": unit,
        "payload": {
            "reactionType": "LIKE",
            "randomReaction": False,
            "reactBefore": "MONTH1",
            "skipDelayIfCannotLike": False,
        },
    }


def _view_profile(delay: int = 2, unit: str = "DAY") -> dict:
    return {"nodeType": "VIEW_PROFILE", "actionDelay": delay, "actionDelayUnit": unit}


def _end(delay: int = 0, unit: str = "HOUR") -> dict:
    return {"nodeType": "END", "actionDelay": delay, "actionDelayUnit": unit}


def _interaction(idx: int) -> dict:
    """Interaction between message idx and idx+1: alternate like / view."""
    return _like_post() if idx % 2 == 0 else _view_profile()


def _message_chain(messages: list[str], idx: int) -> dict:
    message, fallback = to_heyreach_message(messages[idx])
    node = {
        "nodeType": "MESSAGE",
        "actionDelay": 3,
        "actionDelayUnit": "HOUR",
        "payload": {"messages": [message], "fallbackMessage": fallback},
        "conditionalNode": _end(0, "HOUR"),  # replied -> exit
    }
    if idx == len(messages) - 1:
        node["unconditionalNode"] = _end(2, "DAY")
    else:
        interaction = _interaction(idx)
        interaction["unconditionalNode"] = _message_chain(messages, idx + 1)
        node["unconditionalNode"] = interaction
    return node


def build_linkedin_sequence(messages: list[str], *, withdraw_days: int = _WITHDRAW_DAYS_DEFAULT) -> dict:
    """Build the full HeyReach sequence tree from 1-3 message bodies."""
    if not messages or len(messages) > 3:
        raise ValueError("LinkedIn sequence needs 1 to 3 messages")
    return {
        "nodeType": "CHECK_IS_CONNECTION",
        "actionDelay": 0,
        "actionDelayUnit": "HOUR",
        "conditionalNode": _message_chain(messages, 0),  # already connected
        "unconditionalNode": {  # not connected
            "nodeType": "VIEW_PROFILE",
            "actionDelay": 3,
            "actionDelayUnit": "HOUR",
            "unconditionalNode": {
                "nodeType": "CONNECTION_REQUEST",
                "actionDelay": 3,
                "actionDelayUnit": "HOUR",
                "payload": {"messages": [""], "fallbackMessage": "", "toBeWithdrawnAfterDays": withdraw_days},
                "conditionalNode": _message_chain(messages, 0),  # accepted
                "unconditionalNode": {  # not accepted -> wait then end
                    **_like_post(2, "DAY"),
                    "unconditionalNode": _end(1, "DAY"),
                },
            },
        },
    }
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_sequence_builder.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/heyreach_sequence_builder.py tests/test_heyreach_sequence_builder.py
git commit -m "feat: heyreach_sequence_builder (branching LinkedIn node tree, 1-3 messages)"
```

---

### Task 2: Plan model — per-step `channel` + `linkedin_messages` helper

**Files:**
- Modify: `app/schemas/campaign_plan.py`
- Test: `tests/test_campaign_plan_schema.py` (create if absent)

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `SequenceStep.channel: Literal["email", "linkedin"] = "email"`
  - module function `linkedin_messages(plan: dict) -> list[str]` in `campaign_plan.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_campaign_plan_schema.py`:

```python
from app.schemas.campaign_plan import SequenceStep, linkedin_messages


def test_step_defaults_to_email_channel():
    step = SequenceStep(step_number=1, delay_days=0, variants=[{"body": "x"}])
    assert step.channel == "email"


def test_step_accepts_linkedin_channel():
    step = SequenceStep(step_number=1, delay_days=0, channel="linkedin", variants=[{"body": "hi"}])
    assert step.channel == "linkedin"


def test_linkedin_messages_extracts_ordered_bodies():
    plan = {
        "sequence": [
            {"step_number": 2, "delay_days": 0, "channel": "linkedin", "variants": [{"body": "second"}]},
            {"step_number": 1, "delay_days": 0, "channel": "linkedin", "variants": [{"body": "first"}]},
            {"step_number": 3, "delay_days": 0, "channel": "email", "variants": [{"body": "email body"}]},
        ]
    }
    assert linkedin_messages(plan) == ["first", "second"]


def test_linkedin_messages_empty_when_none():
    plan = {"sequence": [{"step_number": 1, "delay_days": 0, "variants": [{"body": "x"}]}]}
    assert linkedin_messages(plan) == []
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_plan_schema.py -q`
Expected: FAIL — `ImportError: cannot import name 'linkedin_messages'` (and `channel` unknown).

- [ ] **Step 3: Write the implementation** — in `app/schemas/campaign_plan.py`:

(a) Add `channel` to `SequenceStep` (after `delay_days`):

```python
class SequenceStep(BaseModel):
    step_number: int
    delay_days: int
    channel: Literal["email", "linkedin"] = "email"
    variants: list[SequenceVariant]
```

(b) Add the helper at module level (end of file):

```python
def linkedin_messages(plan: dict) -> list[str]:
    """First-variant bodies of LinkedIn-channel steps, in step_number order."""
    steps = [s for s in (plan.get("sequence") or []) if s.get("channel") == "linkedin"]
    steps.sort(key=lambda s: s.get("step_number", 0))
    out: list[str] = []
    for step in steps:
        variants = step.get("variants") or []
        if variants and variants[0].get("body"):
            out.append(variants[0]["body"])
    return out
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_plan_schema.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/schemas/campaign_plan.py tests/test_campaign_plan_schema.py
git commit -m "feat: per-step channel + linkedin_messages helper"
```

---

### Task 3: Config — per-workspace HeyReach API key

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`, `render.yaml`
- Test: `tests/test_config.py` (create if absent)

**Interfaces:**
- Consumes: existing `get_workspace_config`, `get_secret_value`, `SMARTLEAD_WORKSPACES`.
- Produces: `get_workspace_config(key)["heyreach_api_key"]` (str or None).

- [ ] **Step 1: Write the failing test** — add to `tests/test_config.py`:

```python
from app import config


def test_workspace_config_exposes_heyreach_key(monkeypatch):
    monkeypatch.setenv("HEYREACH_PRECISELEAD_API_KEY", "hr-key-123")
    # get_secret_value reads the environment; clear any cached settings if needed.
    cfg = config.get_workspace_config("preciselead")
    assert cfg is not None
    assert "heyreach_api_key" in cfg
    assert cfg["heyreach_api_key"] == "hr-key-123"


def test_workspace_config_heyreach_key_none_when_unset(monkeypatch):
    monkeypatch.delenv("HEYREACH_BELARDI_WONG_API_KEY", raising=False)
    cfg = config.get_workspace_config("belardi_wong")
    assert cfg is not None
    assert cfg.get("heyreach_api_key") in (None, "")
```

(If `get_secret_value` caches, the test may need `config.get_secret_value.cache_clear()` — check the function; if it's a plain `os.environ` read, the monkeypatch is enough.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_config.py -q`
Expected: FAIL — `KeyError`/`assert "heyreach_api_key" in cfg`.

- [ ] **Step 3: Write the implementation** —

(a) Add `heyreach_api_key_env` to each `SMARTLEAD_WORKSPACES` entry, e.g. preciselead:

```python
    {
        "key": "preciselead",
        "name": "PreciseLead",
        "api_key_env": "SMARTLEAD_PRECISELEAD_API_KEY",
        "heyreach_api_key_env": "HEYREACH_PRECISELEAD_API_KEY",
        "self_client_name": "PreciseLeads",
        "sheet_client": "PRECISE_LEADS",
    },
```

Add the analogous `heyreach_api_key_env` line to `belardi_wong` (`HEYREACH_BELARDI_WONG_API_KEY`), `darlean` (`HEYREACH_DARLEAN_API_KEY`), `mythic` (`HEYREACH_MYTHIC_API_KEY`).

(b) In `get_workspace_config`, resolve and include it:

```python
        api_key = get_secret_value(workspace["api_key_env"])
        heyreach_env = workspace.get("heyreach_api_key_env")
        heyreach_api_key = get_secret_value(heyreach_env) if heyreach_env else None
        return {
            "key": workspace["key"],
            "name": workspace["name"],
            "api_key": api_key,
            "heyreach_api_key": heyreach_api_key,
            "self_client_name": workspace.get("self_client_name") or workspace["name"],
        }
```

(c) `.env.example` — add under the Smartlead keys:

```
# HeyReach workspace API keys (per workspace)
HEYREACH_PRECISELEAD_API_KEY=replace_me
HEYREACH_BELARDI_WONG_API_KEY=replace_me
HEYREACH_DARLEAN_API_KEY=replace_me
HEYREACH_MYTHIC_API_KEY=replace_me
```

(d) `render.yaml` — add four entries alongside the Smartlead keys:

```yaml
      - key: HEYREACH_PRECISELEAD_API_KEY
        sync: false
      - key: HEYREACH_BELARDI_WONG_API_KEY
        sync: false
      - key: HEYREACH_DARLEAN_API_KEY
        sync: false
      - key: HEYREACH_MYTHIC_API_KEY
        sync: false
```

(Match the existing indentation/structure of the env list in `render.yaml`.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/config.py .env.example render.yaml tests/test_config.py
git commit -m "feat: per-workspace HeyReach API key config"
```

---

### Task 4: `HeyReachService` (REST client)

**Files:**
- Create: `app/services/heyreach_service.py`
- Test: `tests/test_heyreach_service.py`

**Interfaces:**
- Produces:
  - `HeyReachService(api_key: str)`
  - `async def get_linkedin_accounts(limit=100, offset=0) -> dict`
  - `async def create_empty_list(name: str) -> dict`
  - `async def create_campaign(name, list_id, account_ids, sequence: dict, schedule: dict | None = None) -> dict`
  - `def campaign_url(campaign_id: int) -> str`

> Endpoint paths below are the HeyReach public API best-known shapes. Confirm the exact
> path + verb against HeyReach's public API docs (the connected `mythic_heyreach` MCP proves
> them) before the live smoke test. Tests assert the endpoint string the service uses, so if
> you adjust a path, update the matching test in the same edit.

- [ ] **Step 1: Write the failing test** — create `tests/test_heyreach_service.py` (mirror `test_smartlead_service.py`'s recording-subclass + `asyncio.run` style):

```python
import asyncio
import json

from app.services.heyreach_service import HeyReachService


class RecordingHeyReach(HeyReachService):
    def __init__(self):
        super().__init__("test-key")
        self.calls = []

    async def post(self, endpoint, payload):
        self.calls.append(("post", endpoint, payload))
        return {"ok": True, "id": 555}

    async def get(self, endpoint, params=None):
        self.calls.append(("get", endpoint, params))
        return {"ok": True, "items": [{"id": 1}, {"id": 2}]}


def test_get_linkedin_accounts_call_shape():
    async def run():
        svc = RecordingHeyReach()
        await svc.get_linkedin_accounts(limit=50, offset=10)
        method, endpoint, payload = svc.calls[0]
        assert "linkedin" in endpoint.lower() or "account" in endpoint.lower()
        # limit/offset present (as params for GET or body for POST)
        blob = json.dumps(payload)
        assert "50" in blob and "10" in blob
    asyncio.run(run())


def test_create_empty_list_sends_user_list_type():
    async def run():
        svc = RecordingHeyReach()
        out = await svc.create_empty_list("My List")
        _, endpoint, payload = svc.calls[0]
        assert "list" in endpoint.lower()
        assert payload["name"] == "My List"
        assert payload["listType"] == "USER_LIST"
        assert out["id"] == 555
    asyncio.run(run())


def test_create_campaign_serializes_sequence_and_attaches_accounts():
    async def run():
        svc = RecordingHeyReach()
        seq = {"nodeType": "CHECK_IS_CONNECTION"}
        await svc.create_campaign("Camp", 732802, [101, 102], seq)
        _, endpoint, payload = svc.calls[0]
        assert "campaign" in endpoint.lower()
        assert payload["name"] == "Camp"
        assert payload["linkedInUserListId"] == 732802
        assert payload["linkedInAccountIds"] == [101, 102]
        # sequence delivered as JSON string under sequenceJson
        assert json.loads(payload["sequenceJson"]) == seq
    asyncio.run(run())


def test_campaign_url_contains_id():
    svc = HeyReachService("k")
    assert "999" in svc.campaign_url(999)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_service.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.heyreach_service'`.

- [ ] **Step 3: Write the implementation** — create `app/services/heyreach_service.py`:

```python
import json

import httpx


class HeyReachService:
    BASE_URL = "https://api.heyreach.io/api/public"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-API-KEY": api_key,
            "User-Agent": "Precise-Automator/1.0",
        }

    def url(self, endpoint: str) -> str:
        return f"{self.BASE_URL}/{endpoint}"

    def campaign_url(self, campaign_id: int) -> str:
        return f"https://app.heyreach.io/app/campaigns/{campaign_id}"

    async def post(self, endpoint: str, payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.post(self.url(endpoint), json=payload, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        async with httpx.AsyncClient(timeout=90) as client:
            response = await client.get(self.url(endpoint), params=params, headers=self.headers)
            response.raise_for_status()
            return response.json()

    async def get_linkedin_accounts(self, limit: int = 100, offset: int = 0) -> dict:
        # HeyReach public API: POST .../linkedinaccount/GetAll with paging body.
        return await self.post("linkedinaccount/GetAll", {"limit": limit, "offset": offset})

    async def create_empty_list(self, name: str) -> dict:
        return await self.post("list/CreateEmptyList", {"name": name, "listType": "USER_LIST"})

    async def create_campaign(
        self,
        name: str,
        list_id: int,
        account_ids: list[int],
        sequence: dict,
        schedule: dict | None = None,
    ) -> dict:
        payload: dict = {
            "name": name,
            "linkedInUserListId": list_id,
            "linkedInAccountIds": account_ids,
            "sequenceJson": json.dumps(sequence),
        }
        if schedule is not None:
            payload["schedule"] = schedule
        return await self.post("campaign/CreateCampaign", payload)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_service.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/services/heyreach_service.py tests/test_heyreach_service.py
git commit -m "feat: HeyReachService (linkedin accounts, list, create campaign)"
```

---

### Task 5: Store — HeyReach fields + helpers

**Files:**
- Modify: `app/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: existing `insert_campaign`, `campaigns_collection`, `to_object_id`, `now_utc`, `get_campaign`.
- Produces:
  - `insert_campaign` doc gains `heyreach_campaign_id: None`, `heyreach_campaign_url: None`, `heyreach_status: None`, `heyreach_creating: False`, `heyreach_last_error: None`.
  - `set_heyreach_creating(campaign_id: str, creating: bool) -> dict | None`
  - `save_heyreach_result(campaign_id: str, *, campaign_id_value: int | None, url: str | None, status: str, error: str | None = None) -> dict | None`

- [ ] **Step 1: Write the failing test** — add to `tests/test_store.py`:

```python
def test_insert_defaults_heyreach_fields():
    doc = store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan={},
        validation_errors=[],
    )
    assert doc["heyreach_campaign_id"] is None
    assert doc["heyreach_creating"] is False
    assert doc["heyreach_status"] is None


def test_set_heyreach_creating_and_save_result():
    doc = store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan={}, validation_errors=[],
    )
    cid = str(doc["_id"])
    assert store.set_heyreach_creating(cid, True)["heyreach_creating"] is True
    done = store.save_heyreach_result(
        cid, campaign_id_value=472000, url="https://app.heyreach.io/app/campaigns/472000",
        status="draft_created",
    )
    assert done["heyreach_campaign_id"] == 472000
    assert "472000" in done["heyreach_campaign_url"]
    assert done["heyreach_status"] == "draft_created"
    assert done["heyreach_creating"] is False


def test_save_heyreach_result_records_error():
    doc = store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan={}, validation_errors=[],
    )
    cid = str(doc["_id"])
    done = store.save_heyreach_result(cid, campaign_id_value=None, url=None, status="failed", error="no senders")
    assert done["heyreach_status"] == "failed"
    assert done["heyreach_last_error"] == "no senders"
    assert done["heyreach_creating"] is False
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_store.py -q`
Expected: FAIL — `KeyError: 'heyreach_campaign_id'` / missing functions.

- [ ] **Step 3: Write the implementation** — in `app/store.py`:

(a) Add the five fields to the `doc` dict inside `insert_campaign` (next to the twin fields):

```python
        "heyreach_campaign_id": None,
        "heyreach_campaign_url": None,
        "heyreach_status": None,
        "heyreach_creating": False,
        "heyreach_last_error": None,
```

(b) Add two functions after `save_twin_fix`:

```python
def set_heyreach_creating(campaign_id: str, creating: bool) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"heyreach_creating": creating, "updated_at": now_utc()}},
        return_document=True,
    )


def save_heyreach_result(
    campaign_id: str,
    *,
    campaign_id_value: int | None,
    url: str | None,
    status: str,
    error: str | None = None,
) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {
            "$set": {
                "heyreach_campaign_id": campaign_id_value,
                "heyreach_campaign_url": url,
                "heyreach_status": status,
                "heyreach_last_error": error,
                "heyreach_creating": False,
                "updated_at": now_utc(),
            }
        },
        return_document=True,
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_store.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/store.py tests/test_store.py
git commit -m "feat: store HeyReach campaign id/url/status/creating fields"
```

---

### Task 6: `heyreach_create` worker

**Files:**
- Create: `app/workers/heyreach_create.py`
- Test: `tests/test_heyreach_create.py`

**Interfaces:**
- Consumes: `store.get_campaign/set_heyreach_creating/save_heyreach_result`, `get_workspace_config`,
  `HeyReachService`, `heyreach_sequence_builder.build_linkedin_sequence`,
  `campaign_plan.linkedin_messages`.
- Produces:
  - `create_heyreach_campaign_now(campaign_id: str) -> None` (sync wrapper)
  - `async def _create_async(campaign_id: str) -> dict` (returns summary; also persisted)

- [ ] **Step 1: Write the failing test** — create `tests/test_heyreach_create.py`:

```python
import asyncio

import pytest

from app import store
from app.workers import heyreach_create


class FakeHeyReach:
    def __init__(self):
        self.created = None
        self.list_name = None

    async def get_linkedin_accounts(self, limit=100, offset=0):
        return {"items": [{"id": 101}, {"id": 102}]}

    async def create_empty_list(self, name):
        self.list_name = name
        return {"id": 9001}

    async def create_campaign(self, name, list_id, account_ids, sequence, schedule=None):
        self.created = {"name": name, "list_id": list_id, "account_ids": account_ids, "sequence": sequence}
        return {"id": 472000}

    def campaign_url(self, cid):
        return f"https://app.heyreach.io/app/campaigns/{cid}"


def _doc_with_linkedin(messages, **kw):
    seq = [
        {"step_number": i + 1, "delay_days": 0, "channel": "linkedin", "variants": [{"body": m}]}
        for i, m in enumerate(messages)
    ]
    return store.insert_campaign(
        workspace_key="darlean", campaign_name="LI Camp", raw_input={},
        plan={"sequence": seq}, validation_errors=[], **kw,
    )


@pytest.fixture
def patched(monkeypatch):
    fake = FakeHeyReach()
    monkeypatch.setattr(heyreach_create, "get_workspace_config", lambda k: {"key": k, "heyreach_api_key": "KEY"})
    monkeypatch.setattr(heyreach_create, "HeyReachService", lambda key: fake)
    return fake


def test_creates_draft_with_all_senders_and_sequence(patched):
    doc = _doc_with_linkedin(["Hi {{first_name}}", "Follow up"])
    cid = str(doc["_id"])
    summary = asyncio.run(heyreach_create._create_async(cid))
    assert summary["status"] == "draft_created"
    assert patched.created["account_ids"] == [101, 102]            # all senders
    assert patched.created["sequence"]["nodeType"] == "CHECK_IS_CONNECTION"
    saved = store.get_campaign(cid)
    assert saved["heyreach_campaign_id"] == 472000
    assert "472000" in saved["heyreach_campaign_url"]
    assert saved["heyreach_creating"] is False


def test_no_linkedin_steps_errors(patched):
    doc = store.insert_campaign(
        workspace_key="darlean", campaign_name="Email only", raw_input={},
        plan={"sequence": [{"step_number": 1, "delay_days": 0, "variants": [{"body": "x"}]}]},
        validation_errors=[],
    )
    cid = str(doc["_id"])
    summary = asyncio.run(heyreach_create._create_async(cid))
    assert summary["status"] == "failed"
    assert patched.created is None


def test_no_key_errors(monkeypatch):
    monkeypatch.setattr(heyreach_create, "get_workspace_config", lambda k: {"key": k, "heyreach_api_key": None})
    doc = _doc_with_linkedin(["Hi"])
    cid = str(doc["_id"])
    summary = asyncio.run(heyreach_create._create_async(cid))
    assert summary["status"] == "failed"
    assert store.get_campaign(cid)["heyreach_status"] == "failed"


def test_no_senders_errors(monkeypatch):
    class NoSenders(FakeHeyReach):
        async def get_linkedin_accounts(self, limit=100, offset=0):
            return {"items": []}
    fake = NoSenders()
    monkeypatch.setattr(heyreach_create, "get_workspace_config", lambda k: {"key": k, "heyreach_api_key": "KEY"})
    monkeypatch.setattr(heyreach_create, "HeyReachService", lambda key: fake)
    doc = _doc_with_linkedin(["Hi"])
    summary = asyncio.run(heyreach_create._create_async(str(doc["_id"])))
    assert summary["status"] == "failed"
    assert fake.created is None
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_create.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.workers.heyreach_create'`.

- [ ] **Step 3: Write the implementation** — create `app/workers/heyreach_create.py`:

```python
"""Background worker: create a HeyReach LinkedIn DRAFT campaign from a plan's
LinkedIn steps. Mirrors app/workers/sync_campaign.py.

Creates an empty USER_LIST, attaches ALL LinkedIn senders, builds the sequence
from the LinkedIn message bodies, and creates the campaign in DRAFT. Never starts
it and never pushes leads.
"""

import asyncio

from app.config import get_workspace_config
from app.schemas.campaign_plan import linkedin_messages
from app.services.heyreach_sequence_builder import build_linkedin_sequence
from app.services.heyreach_service import HeyReachService
from app import store


def create_heyreach_campaign_now(campaign_id: str) -> None:
    """Synchronous entrypoint for FastAPI BackgroundTasks."""
    asyncio.run(_create_async(campaign_id))


def _account_ids(accounts_response: dict) -> list[int]:
    items = accounts_response.get("items") or accounts_response.get("data") or []
    ids = []
    for item in items:
        aid = item.get("id") if isinstance(item, dict) else None
        if aid is not None:
            ids.append(int(aid))
    return ids


async def _create_async(campaign_id: str) -> dict:
    summary = {"status": "failed", "errors": [], "heyreach_campaign_id": None, "url": None}
    doc = store.get_campaign(campaign_id)
    if not doc:
        summary["errors"].append("Campaign not found")
        return summary

    plan = doc.get("current_plan") or {}
    messages = linkedin_messages(plan)
    if not messages:
        summary["errors"].append("No LinkedIn steps in this campaign")
        store.save_heyreach_result(campaign_id, campaign_id_value=None, url=None, status="failed",
                                   error=summary["errors"][-1])
        return summary
    if len(messages) > 3:
        summary["errors"].append("LinkedIn templates support at most 3 messages")
        store.save_heyreach_result(campaign_id, campaign_id_value=None, url=None, status="failed",
                                   error=summary["errors"][-1])
        return summary

    workspace = get_workspace_config(doc.get("smartlead_workspace", ""))
    if not workspace or not workspace.get("heyreach_api_key"):
        summary["errors"].append(f"HeyReach API key not configured for '{doc.get('smartlead_workspace')}'")
        store.save_heyreach_result(campaign_id, campaign_id_value=None, url=None, status="failed",
                                   error=summary["errors"][-1])
        return summary

    heyreach = HeyReachService(workspace["heyreach_api_key"])
    try:
        accounts = await heyreach.get_linkedin_accounts(limit=100, offset=0)
        account_ids = _account_ids(accounts)
        if not account_ids:
            raise RuntimeError("No LinkedIn sender accounts in this workspace")

        created_list = await heyreach.create_empty_list(doc.get("campaign_name") or "LinkedIn campaign")
        list_id = int(created_list.get("id") or created_list.get("listId"))

        sequence = build_linkedin_sequence(messages)
        created = await heyreach.create_campaign(
            doc.get("campaign_name") or "LinkedIn campaign", list_id, account_ids, sequence
        )
        hr_id = int(created.get("id") or created.get("campaignId"))
        url = heyreach.campaign_url(hr_id)

        summary.update({"status": "draft_created", "heyreach_campaign_id": hr_id, "url": url})
        store.save_heyreach_result(campaign_id, campaign_id_value=hr_id, url=url, status="draft_created")
    except Exception as exc:  # surface, never crash the background task
        summary["errors"].append(f"{exc.__class__.__name__}: {exc}")
        store.save_heyreach_result(campaign_id, campaign_id_value=None, url=None, status="failed",
                                   error=summary["errors"][-1])
    return summary
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_heyreach_create.py -q`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/workers/heyreach_create.py tests/test_heyreach_create.py
git commit -m "feat: heyreach_create worker (empty list + all senders + DRAFT campaign)"
```

---

### Task 7: Routes — save LinkedIn messages + create + payload/status

**Files:**
- Modify: `app/routes/campaigns.py`
- Test: `tests/test_campaign_routes.py`

**Interfaces:**
- Consumes: `_require_campaign`, `_redirect_to_detail`, `BackgroundTasks`, `store.update_plan`,
  `store.set_heyreach_creating`, `create_heyreach_campaign_now`, `validate_campaign_plan`,
  `_active_workspace_keys`, `linkedin_messages`.
- Produces:
  - `POST /api/campaigns/{id}/linkedin-messages` (Form `messages: list[str]`) — replaces the
    plan's `channel=="linkedin"` steps with the submitted bodies.
  - `POST /api/campaigns/{id}/heyreach-create` — rejects when no LinkedIn steps; sets
    `heyreach_creating=True`; schedules `create_heyreach_campaign_now`.
  - `_detail_payload` gains `heyreach_campaign_id/url/status/creating/last_error` and
    `linkedin_messages`.
  - `campaign_status` gains `heyreach_creating`, `heyreach_campaign_url`.

- [ ] **Step 1: Write the failing test** — add to `tests/test_campaign_routes.py`:

```python
def test_save_linkedin_messages_sets_channel_steps(client):
    resp = client.post("/api/campaigns/new", data={"workspace_key": "darlean", "campaign_name": "LI"})
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/api/campaigns/{cid}/linkedin-messages",
                    data=[("messages", "Hi {{first_name}}"), ("messages", "Follow up")])
    assert r.status_code in (200, 303)
    from app.schemas.campaign_plan import linkedin_messages
    from app import store
    plan = store.get_campaign(cid)["current_plan"]
    assert linkedin_messages(plan) == ["Hi {{first_name}}", "Follow up"]


def test_heyreach_create_rejected_without_linkedin_steps(client):
    resp = client.post("/api/campaigns/new", data={"workspace_key": "darlean", "campaign_name": "Email only",
                       "messaging_text": "Subject Line Options:\n1. T\n\nEmail 1\nV1\nBody"})
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/api/campaigns/{cid}/heyreach-create", data={})
    assert r.status_code == 400


def test_heyreach_create_schedules_and_flags(client, monkeypatch):
    import app.routes.campaigns as routes
    calls = {}
    monkeypatch.setattr(routes, "create_heyreach_campaign_now", lambda cid: calls.setdefault("cid", cid))
    resp = client.post("/api/campaigns/new", data={"workspace_key": "darlean", "campaign_name": "LI"})
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    client.post(f"/api/campaigns/{cid}/linkedin-messages", data=[("messages", "Hi")])
    r = client.post(f"/api/campaigns/{cid}/heyreach-create", data={})
    assert r.status_code in (200, 303)
    assert calls["cid"] == cid
    assert client.get(f"/api/campaigns/{cid}/status").json()["heyreach_creating"] is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py -k "linkedin or heyreach" -q`
Expected: FAIL — routes 404/405.

- [ ] **Step 3: Write the implementation** —

(a) Imports in `app/routes/campaigns.py`:

```python
from app.schemas.campaign_plan import linkedin_messages
from app.workers.heyreach_create import create_heyreach_campaign_now
```

(b) Add both routes near the twin routes:

```python
@router.post("/api/campaigns/{campaign_id}/linkedin-messages")
def save_linkedin_messages(campaign_id: str, request: Request, messages: list[str] = Form(default=[])) -> dict:
    doc = _require_campaign(campaign_id)
    plan = doc.get("current_plan") or {}
    bodies = [m.strip() for m in messages if m and m.strip()][:3]
    # Replace existing linkedin steps; keep email steps. New linkedin step_numbers continue after email.
    email_steps = [s for s in (plan.get("sequence") or []) if s.get("channel") != "linkedin"]
    base = max([s.get("step_number", 0) for s in email_steps], default=0)
    linkedin_steps = [
        {"step_number": base + i + 1, "delay_days": 0, "channel": "linkedin",
         "variants": [{"variant_label": "A", "subject": "", "body": body}]}
        for i, body in enumerate(bodies)
    ]
    plan["sequence"] = email_steps + linkedin_steps
    errors = validate_campaign_plan(plan, _active_workspace_keys())
    store.update_plan(campaign_id, plan, errors)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "linkedin_messages": bodies})


@router.post("/api/campaigns/{campaign_id}/heyreach-create")
def heyreach_create(campaign_id: str, request: Request, background_tasks: BackgroundTasks) -> dict:
    doc = _require_campaign(campaign_id)
    if not linkedin_messages(doc.get("current_plan") or {}):
        raise HTTPException(status_code=400, detail="No LinkedIn steps. Add LinkedIn messages first.")
    store.set_heyreach_creating(campaign_id, True)
    background_tasks.add_task(create_heyreach_campaign_now, campaign_id)
    return _redirect_to_detail(request, campaign_id, {"ok": True, "queued": True})
```

(c) `_detail_payload` — add keys (next to the twin keys):

```python
        "heyreach_campaign_id": doc.get("heyreach_campaign_id"),
        "heyreach_campaign_url": doc.get("heyreach_campaign_url"),
        "heyreach_status": doc.get("heyreach_status"),
        "heyreach_creating": doc.get("heyreach_creating", False),
        "heyreach_last_error": doc.get("heyreach_last_error"),
        "linkedin_messages": linkedin_messages(doc.get("current_plan") or {}),
```

(d) `campaign_status` — add keys:

```python
        "heyreach_creating": doc.get("heyreach_creating", False),
        "heyreach_campaign_url": doc.get("heyreach_campaign_url"),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py -k "linkedin or heyreach" -q`
Expected: PASS. Then run the full route file to confirm no regression:
`cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest tests/test_campaign_routes.py -q`

- [ ] **Step 5: Commit**

```bash
git add app/routes/campaigns.py tests/test_campaign_routes.py
git commit -m "feat: routes to save LinkedIn messages and create the HeyReach campaign"
```

---

### Task 8: UI — LinkedIn (HeyReach) panel + poller

**Files:**
- Modify: `app/templates/campaign_detail.html`
- Test: render check (manual + the existing route suite renders the page)

**Interfaces:**
- Consumes: `campaign.linkedin_messages`, `campaign.heyreach_creating`, `campaign.heyreach_campaign_url`,
  `campaign.heyreach_status`, `campaign.heyreach_last_error` from `_detail_payload`.

- [ ] **Step 1: Add the LinkedIn panel** — in `app/templates/campaign_detail.html`, after the
  `{% if campaign.is_twin %}…{% endif %}` Twain panel block (top section), add:

```html
<section class="detail-card">
  <div class="card-head"><h2 class="card-title">LinkedIn (HeyReach)</h2></div>
  <p class="card-lead">Build a HeyReach LinkedIn DRAFT campaign from 1–3 messages. Leads are added in HeyReach after.</p>
  <form method="post" action="/api/campaigns/{{ campaign.id }}/linkedin-messages" class="js-single-submit linkedin-msg-form">
    {% set msgs = campaign.linkedin_messages or [] %}
    {% for i in range(3) %}
    <label class="field-label">Message {{ i + 1 }}{% if i > 0 %} <span class="label-note">— optional</span>{% endif %}</label>
    <textarea class="input mono" name="messages" rows="3" placeholder="LinkedIn message {{ i + 1 }} (merge tags: {{ '{{first_name}}' }}, {{ '{{company}}' }})">{{ msgs[i] if msgs|length > i else "" }}</textarea>
    {% endfor %}
    <button type="submit" class="button secondary">Save LinkedIn messages</button>
  </form>

  {% if campaign.linkedin_messages %}
  <div class="linkedin-create">
    {% if campaign.heyreach_creating %}
    <div class="twin-fix-running" role="status">
      <span class="twin-spinner" aria-hidden="true"></span>
      <span>Creating the HeyReach campaign… this refreshes when done.</span>
    </div>
    {% else %}
    <form method="post" action="/api/campaigns/{{ campaign.id }}/heyreach-create" class="js-single-submit">
      <button type="submit" class="button">Create LinkedIn campaign in HeyReach</button>
    </form>
    {% endif %}
    {% if campaign.heyreach_campaign_url %}
    <p class="twin-fix-summary">Created in HeyReach (DRAFT) — <a href="{{ campaign.heyreach_campaign_url }}" target="_blank" rel="noopener">open campaign</a>. Add leads and start it there.</p>
    {% endif %}
    {% if campaign.heyreach_status == "failed" and campaign.heyreach_last_error %}
    <p class="muted">Last attempt failed: {{ campaign.heyreach_last_error }}</p>
    {% endif %}
  </div>
  {% endif %}
</section>
```

- [ ] **Step 2: Add the poller** — after the twin-fix poller `{% endif %}` in the scripts block:

```html
{% if campaign.heyreach_creating %}
<script>
  (() => {
    const statusUrl = "/api/campaigns/{{ campaign.id }}/status";
    let attempts = 0;
    const poll = async () => {
      attempts += 1;
      try {
        const r = await fetch(statusUrl, { headers: { Accept: "application/json" } });
        if (r.ok) {
          const p = await r.json();
          if (!p.heyreach_creating) { window.location.reload(); return; }
        }
      } catch (_) {}
      if (attempts < 120) window.setTimeout(poll, 3000);  // ~6 min cap
    };
    window.setTimeout(poll, 2000);
  })();
</script>
{% endif %}
```

- [ ] **Step 3: Render check**

Run (from `precise-automator`):
```bash
PYTHONPATH=. APP_USERNAME=test-user APP_PASSWORD=test-password .venv/Scripts/python.exe - <<'PY'
import mongomock, app.store as store_mod
_m=mongomock.MongoClient(); store_mod._client=lambda:_m
from fastapi.testclient import TestClient
from app.main import app
from app import store
c=TestClient(app); c.auth=("test-user","test-password")
RI={"parsed_messaging":{}}
plan={"sequence":[{"step_number":1,"delay_days":0,"channel":"linkedin","variants":[{"variant_label":"A","subject":"","body":"Hi {{first_name}}"}]}],
      "schedule":{"max_new_leads_per_day":100,"start_hour":"09:00","end_hour":"18:00","timezone":"America/New_York"}}
doc=store.insert_campaign(workspace_key="darlean",campaign_name="LI",raw_input=RI,plan=plan,validation_errors=[])
cid=str(doc["_id"])
print("panel:", "LinkedIn (HeyReach)" in c.get(f"/campaigns/{cid}").text)
store.set_heyreach_creating(cid,True)
print("creating spinner:", "Creating the HeyReach campaign" in c.get(f"/campaigns/{cid}").text)
store.save_heyreach_result(cid, campaign_id_value=472000, url="https://app.heyreach.io/app/campaigns/472000", status="draft_created")
print("url shown:", "472000" in c.get(f"/campaigns/{cid}").text)
PY
```
Expected: all three print `True`.

- [ ] **Step 4: Commit**

```bash
git add app/templates/campaign_detail.html
git commit -m "feat: LinkedIn (HeyReach) panel — messages, create button, progress, URL"
```

---

### Task 9: Full-suite regression + manual smoke

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

Run: `cd precise-automator && PYTHONPATH=. .venv/Scripts/python.exe -m pytest -q`
Expected: PASS — prior tests plus all new HeyReach tests, 0 failures.

- [ ] **Step 2: Manual smoke (needs a real HeyReach key)**

Set `HEYREACH_<WORKSPACE>_API_KEY` in `.env`, run the server **without `--reload`** (background tasks survive):
`cd precise-automator && .venv/Scripts/python.exe -m uvicorn app.main:app`
Then: open a campaign → LinkedIn panel → enter 1–3 messages → Save → "Create LinkedIn campaign in HeyReach" → wait → confirm the HeyReach DRAFT campaign link appears and the campaign exists in HeyReach (DRAFT, empty list, all senders attached, sequence correct). Verify the sequence renders as connection-check → request → messages in HeyReach.

- [ ] **Step 3: Commit any smoke fixes**

```bash
git add -A && git commit -m "chore: HeyReach creation regression pass"
```

---

## Self-Review

**Spec coverage:**
- Sequence builder (templates 1/2/3, no note, merge tags, fallback, delays, END leaves) → Task 1.
- Plan `channel` + `linkedin_messages` → Task 2.
- Per-workspace HeyReach key (config + .env + render) → Task 3.
- HeyReachService (senders, list, create) → Task 4.
- Store fields/helpers → Task 5.
- Worker (empty list, all senders, DRAFT, persist id/url, error paths) → Task 6.
- Routes (save messages, create, payload, status) → Task 7.
- UI (panel, create button, spinner, poller, URL) → Task 8.
- Regression + smoke → Task 9.
- Monitoring → explicitly out of scope (infra-bot).

**Placeholder scan:** No TBD/TODO. Endpoint paths in Task 4 carry an explicit "confirm against HeyReach docs; tests are path-agnostic / update test if you change the path" instruction — concrete code provided, not a placeholder.

**Type consistency:** `build_linkedin_sequence(messages, *, withdraw_days)` / `to_heyreach_message` match T1↔T6. `linkedin_messages(plan)` matches T2↔T6↔T7. `HeyReachService.get_linkedin_accounts/create_empty_list/create_campaign/campaign_url` match T4↔T6 (FakeHeyReach mirrors the signatures). `set_heyreach_creating` / `save_heyreach_result(campaign_id, *, campaign_id_value, url, status, error)` match T5↔T6↔T7. Detail-payload keys (`heyreach_campaign_url`, `heyreach_creating`, `linkedin_messages`, `heyreach_status`, `heyreach_last_error`) match T7↔T8. Status keys (`heyreach_creating`) match T7↔T8 poller.

**Known follow-up:** exact HeyReach REST endpoint paths/verbs verified at the live smoke (Task 9); if a path differs from Task 4's assumption, adjust the service + its test together.
