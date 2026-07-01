import asyncio
import json

import pytest
from bson import ObjectId

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
    monkeypatch.setattr(heyreach_create, "get_heyreach_api_key_for_client", lambda w, c: "KEY")
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
    monkeypatch.setattr(heyreach_create, "get_heyreach_api_key_for_client", lambda w, c: None)
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
    monkeypatch.setattr(heyreach_create, "get_heyreach_api_key_for_client", lambda w, c: "KEY")
    monkeypatch.setattr(heyreach_create, "HeyReachService", lambda key: fake)
    doc = _doc_with_linkedin(["Hi"])
    summary = asyncio.run(heyreach_create._create_async(str(doc["_id"])))
    assert summary["status"] == "failed"
    assert fake.created is None


def test_create_heyreach_uses_client_account_mapping(monkeypatch):
    """When client mapping exists, only mapped account IDs are attached."""
    from unittest.mock import AsyncMock, patch, MagicMock

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
    mock_svc.create_campaign = AsyncMock(return_value={"id": 888})

    with patch("app.workers.heyreach_create.store") as mock_store, \
         patch("app.workers.heyreach_create.HeyReachService", return_value=mock_svc), \
         patch("app.workers.heyreach_create.get_heyreach_api_key_for_client", return_value="test-key"):
        mock_store.get_campaign.return_value = doc
        mock_store.save_heyreach_result = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()

        asyncio.run(heyreach_create._create_async(str(doc["_id"])))

    call_args = mock_svc.create_campaign.call_args
    account_ids_used = call_args[1].get("account_ids") or call_args[0][2]
    assert sorted(account_ids_used) == [201, 202]
    assert 999 not in account_ids_used


def test_create_heyreach_uses_all_accounts_when_no_mapping(monkeypatch):
    """When no client mapping, all accounts are attached."""
    from unittest.mock import AsyncMock, patch, MagicMock

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
         patch("app.workers.heyreach_create.get_heyreach_api_key_for_client", return_value="test-key"):
        mock_store.get_campaign.return_value = doc
        mock_store.save_heyreach_result = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()

        asyncio.run(heyreach_create._create_async(str(doc["_id"])))

    call_args = mock_svc.create_campaign.call_args
    account_ids_used = call_args[1].get("account_ids") or call_args[0][2]
    assert sorted(account_ids_used) == [101, 102]
