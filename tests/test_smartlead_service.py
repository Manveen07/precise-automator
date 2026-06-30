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
        await service.add_leads(123, [{"email": "lead@example.com"}])
        await service.get_clients()
        await service.set_campaign_status(123, "ACTIVE")

        assert ("patch", "campaigns/123/status", {"status": "ARCHIVED"}) in service.calls
        assert ("post", "campaigns/123/status", {"status": "ACTIVE"}) in service.calls
        assert ("delete", "campaigns/123", None) in service.calls
        assert ("get", "campaigns/123/analytics", None) in service.calls
        assert ("get", "campaigns/123/statistics", {"limit": 50, "offset": 10}) in service.calls
        assert ("post", "campaigns/123/leads", {"lead_list": [{"email": "lead@example.com"}]}) in service.calls
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
        assert ("get", "client/", None) in service.calls

    asyncio.run(run())


def test_resume_campaign_tries_status_payload_variants_until_one_succeeds():
    async def run():
        class ResumeRecordingService(RecordingSmartleadService):
            async def post(self, endpoint: str, payload: dict) -> dict:
                self.calls.append(("post", endpoint, payload))
                if payload == {"status": "START"}:
                    return {"ok": True}
                raise _smartlead_status_error(400, "bad status")

            async def patch(self, endpoint: str, payload: dict) -> dict:
                self.calls.append(("patch", endpoint, payload))
                raise _smartlead_status_error(400, "bad status")

        service = ResumeRecordingService()
        response = await service.resume_campaign(123)

        assert response == {"ok": True}
        assert service.calls == [
            ("post", "campaigns/123/status", {"status": "ACTIVE"}),
            ("patch", "campaigns/123/status", {"status": "ACTIVE"}),
            ("post", "campaigns/123/status", {"status": "START"}),
        ]

    asyncio.run(run())


def _smartlead_status_error(status_code: int, body: str):
    import httpx

    request = httpx.Request("POST", "https://server.smartlead.ai/api/v1/campaigns/123/status?api_key=secret")
    response = httpx.Response(status_code, request=request, text=body)
    return httpx.HTTPStatusError("bad status", request=request, response=response)


def test_url_includes_api_key_and_extra_query_params():
    service = SmartleadService("secret")
    assert (
        service.url("analytics/campaign/overall-stats", {"campaign_ids": "123", "timezone": "America/New_York"})
        == "https://server.smartlead.ai/api/v1/analytics/campaign/overall-stats?api_key=secret&campaign_ids=123&timezone=America%2FNew_York"
    )


def test_campaign_url_points_to_email_campaign_overview():
    service = SmartleadService("secret")
    assert service.campaign_url(123) == "https://app.smartlead.ai/app/email-campaign/123/overview"
    assert service.headers["User-Agent"] == "Precise-Automator/1.0"


def test_get_leads_hits_campaign_leads_endpoint():
    async def run():
        svc = RecordingSmartleadService()
        out = await svc.get_leads(123, limit=50, offset=100)
        assert ("get", "campaigns/123/leads", {"limit": 50, "offset": 100}) in svc.calls
        assert out["ok"] is True

    asyncio.run(run())


def test_update_lead_posts_per_lead_endpoint_with_email_and_custom_fields():
    async def run():
        svc = RecordingSmartleadService()
        await svc.update_lead(123, 5, "a@x.com", {"Step 1": "A<br><br>B"})
        assert ("post", "campaigns/123/leads/5", {"email": "a@x.com", "custom_fields": {"Step 1": "A<br><br>B"}}) in svc.calls

    asyncio.run(run())
