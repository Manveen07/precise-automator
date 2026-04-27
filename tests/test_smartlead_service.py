import asyncio

from app.services.smartlead_service import SmartleadService


class RecordingSmartleadService(SmartleadService):
    def __init__(self):
        super().__init__("test-key")
        self.calls = []

    async def post(self, endpoint: str, payload: dict) -> dict:
        self.calls.append(("post", endpoint, payload))
        return {"ok": True, "endpoint": endpoint}

    async def patch(self, endpoint: str, payload: dict) -> dict:
        self.calls.append(("patch", endpoint, payload))
        return {"ok": True, "endpoint": endpoint}

    async def delete(self, endpoint: str) -> dict:
        self.calls.append(("delete", endpoint, None))
        return {"ok": True, "endpoint": endpoint}

    async def get(self, endpoint: str, params: dict | None = None) -> dict:
        self.calls.append(("get", endpoint, params))
        return {"ok": True, "endpoint": endpoint}


def test_create_and_edit_campaign_call_shapes():
    async def run():
        service = RecordingSmartleadService()
        await service.create_campaign("Test Campaign", client_id=42)
        await service.apply_v1_settings(123)
        await service.update_schedule(123, {"timezone": "America/New_York"})
        await service.update_sequences(123, [{"seq_number": 1, "seq_variants": []}])

        assert service.calls[0] == ("post", "campaigns/create", {"name": "Test Campaign", "client_id": 42})
        setting_calls = [call for call in service.calls if call[1] == "campaigns/123/settings"]
        assert len(setting_calls) == 3
        assert setting_calls[-1][2] == {"send_as_plain_text": True, "force_plain_text": True}
        assert ("post", "campaigns/123/schedule", {"timezone": "America/New_York"}) in service.calls
        assert ("post", "campaigns/123/sequences", {"sequences": [{"seq_number": 1, "seq_variants": []}]}) in service.calls

    asyncio.run(run())


def test_delete_archive_and_analytics_call_shapes():
    async def run():
        service = RecordingSmartleadService()
        await service.archive_campaign(123)
        await service.delete_campaign(123)
        await service.get_campaign_analytics(123)
        await service.get_campaign_statistics(123, limit=50, offset=10)
        await service.get_campaign_performance("2026-04-01", "2026-04-27", campaign_ids=[123])

        assert ("patch", "campaigns/123/status", {"status": "ARCHIVED"}) in service.calls
        assert ("delete", "campaigns/123", None) in service.calls
        assert ("get", "campaigns/123/analytics", None) in service.calls
        assert ("get", "campaigns/123/statistics", {"limit": 50, "offset": 10}) in service.calls
        assert (
            "get",
            "analytics/campaign/overall-stats",
            {
                "start_date": "2026-04-01",
                "end_date": "2026-04-27",
                "timezone": "America/New_York",
                "campaign_ids": "123",
            },
        ) in service.calls

    asyncio.run(run())


def test_url_includes_api_key_and_extra_query_params():
    service = SmartleadService("secret")
    assert (
        service.url("analytics/campaign/overall-stats", {"campaign_ids": "123", "timezone": "America/New_York"})
        == "https://server.smartlead.ai/api/v1/analytics/campaign/overall-stats?api_key=secret&campaign_ids=123&timezone=America%2FNew_York"
    )
