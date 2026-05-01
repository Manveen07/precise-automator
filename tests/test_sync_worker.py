import httpx
import pytest

from app.workers.sync_campaign import _error_text, _extract_campaign_id


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
    request = httpx.Request("POST", "https://server.smartlead.ai/api/v1/campaigns/create")
    response = httpx.Response(500, request=request, text="temporary failure")
    error = httpx.HTTPStatusError("server error", request=request, response=response)

    text = _error_text(error)

    assert "HTTP 500" in text
    assert "temporary failure" in text


def test_error_text_handles_non_http_exceptions():
    text = _error_text(RuntimeError("boom"))
    assert "RuntimeError" in text
    assert "boom" in text
