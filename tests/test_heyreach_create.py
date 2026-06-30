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
    assert patched.created["account_ids"] == [101, 102]
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
