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
        assert json.loads(payload["sequenceJson"]) == seq
    asyncio.run(run())


def test_campaign_url_contains_id():
    svc = HeyReachService("k")
    assert "999" in svc.campaign_url(999)
