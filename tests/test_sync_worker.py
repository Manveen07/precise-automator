import httpx
import pytest

from app.workers.sync_campaign import _client_name_key, _error_text, _extract_campaign_id, _resolve_client_id


class FakeSmartlead:
    def __init__(self, clients, error=None):
        self.clients = clients
        self.error = error
        self.get_clients_called = False

    async def get_clients(self):
        self.get_clients_called = True
        if self.error:
            raise self.error
        return {"ok": True, "data": self.clients}


def test_extract_campaign_id_accepts_top_level_and_wrapped_responses():
    assert _extract_campaign_id({"id": 123}) == 123
    assert _extract_campaign_id({"campaign_id": "456"}) == 456
    assert _extract_campaign_id({"data": {"id": 789}}) == 789


def test_extract_campaign_id_raises_clear_error_for_missing_or_invalid_id():
    with pytest.raises(RuntimeError, match="missing id/campaign_id"):
        _extract_campaign_id({"ok": True})

    with pytest.raises(RuntimeError, match="invalid id"):
        _extract_campaign_id({"id": 0})


def test_http_status_error_text_keeps_status_and_response_body():
    request = httpx.Request("POST", "https://server.smartlead.ai/api/v1/campaigns/create?api_key=secret")
    response = httpx.Response(500, request=request, text="temporary failure")
    error = httpx.HTTPStatusError("server error", request=request, response=response)

    text = _error_text(error)

    assert "HTTP 500" in text
    assert "temporary failure" in text
    assert "api_key=[redacted]" in text
    assert "secret" not in text


def test_error_text_handles_non_http_exceptions():
    text = _error_text(RuntimeError("boom"))
    assert "RuntimeError" in text
    assert "boom" in text


def test_client_name_key_normalizes_spacing_and_case():
    assert _client_name_key("Precise Lead") == _client_name_key("preciselead")


def test_resolve_client_id_uses_env_override_without_fetching_clients():
    async def run():
        smartlead = FakeSmartlead([{"id": 999, "name": "PreciseLead"}])
        client_id = await _resolve_client_id(
            smartlead,
            {"client_id": 123, "client_name": "PreciseLead"},
        )
        assert client_id == 123
        assert smartlead.get_clients_called is False

    import asyncio

    asyncio.run(run())


def test_resolve_client_id_fetches_by_normalized_client_name():
    async def run():
        smartlead = FakeSmartlead([{"id": "456", "name": "Precise Lead"}])
        client_id = await _resolve_client_id(
            smartlead,
            {"client_id": None, "client_name": "PreciseLead"},
        )
        assert client_id == 456
        assert smartlead.get_clients_called is True

    import asyncio

    asyncio.run(run())


def test_resolve_client_id_fails_clear_when_name_not_found():
    async def run():
        smartlead = FakeSmartlead([{"id": 456, "name": "Other Client"}])
        with pytest.raises(RuntimeError, match="PreciseLead.*not found"):
            await _resolve_client_id(
                smartlead,
                {"client_id": None, "client_name": "PreciseLead"},
            )

    import asyncio

    asyncio.run(run())


def test_resolve_client_id_falls_back_when_client_list_is_unauthorized():
    async def run():
        request = httpx.Request("GET", "https://server.smartlead.ai/api/v1/client/?api_key=secret")
        response = httpx.Response(401, request=request)
        smartlead = FakeSmartlead(
            [],
            httpx.HTTPStatusError("unauthorized", request=request, response=response),
        )
        client_id = await _resolve_client_id(
            smartlead,
            {"client_id": None, "client_name": "PreciseLead"},
        )
        assert client_id is None
        assert smartlead.get_clients_called is True

    import asyncio

    asyncio.run(run())
