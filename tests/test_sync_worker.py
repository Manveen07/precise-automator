import httpx
import pytest

from app.config import infer_smartlead_client
from app.workers.sync_campaign import (
    _error_text,
    _extract_campaign_id,
    _html_to_compare_text,
    _resolve_client_id,
    _sequence_sync_mismatches,
)


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


def test_html_to_compare_text_preserves_visible_spacing_from_smartlead_html():
    html = "Hi {{first_name}},<br><br>1. &nbsp;&lt;&gt;<br>&nbsp;&nbsp;indented &nbsp;words"

    assert _html_to_compare_text(html) == "Hi {{first_name}},\n\n1.  <>\n  indented  words"


def test_sequence_sync_mismatches_accepts_equivalent_smartlead_html():
    plan_sequence = [
        {
            "step_number": 1,
            "variants": [
                {
                    "variant_label": "A",
                    "subject": "New Movers",
                    "body": "Hi {{first_name}},\n\n1.  <>\n%signature%",
                }
            ],
        }
    ]
    smartlead_sequences = [
        {
            "seq_number": 1,
            "sequence_variants": [
                {
                    "variant_label": "A",
                    "subject": "New Movers",
                    "email_body": "Hi {{first_name}},<br><br>1. &nbsp;&lt;&gt;<br><br>%signature%",
                }
            ],
        }
    ]

    assert _sequence_sync_mismatches(plan_sequence, smartlead_sequences) == []


def test_sequence_sync_mismatches_reports_missing_or_changed_copy():
    plan_sequence = [
        {
            "step_number": 2,
            "variants": [{"variant_label": "A", "subject": "", "body": "Line one\n\nLine two"}],
        }
    ]
    smartlead_sequences = [
        {
            "seq_number": 2,
            "sequence_variants": [{"variant_label": "A", "subject": "", "email_body": "Line one<br>Line two"}],
        }
    ]

    assert _sequence_sync_mismatches(plan_sequence, smartlead_sequences) == [
        "Email 2 variant A body changed or lost spacing"
    ]


@pytest.mark.parametrize(
    ("campaign_name", "expected_client_id"),
    [
        ("Melior - Q2 outbound", 12256),
        ("OSC benchmark", 145916),
        ("Staff AI follow-up", 145916),
        ("SVSG net-new", 145916),
        ("Sri reactivation", 145916),
        ("Avenge pilot", 88657),
        ("Avench sequence", 88657),
    ],
)
def test_infer_smartlead_client_from_campaign_name(campaign_name, expected_client_id):
    assert infer_smartlead_client("preciselead", campaign_name)["client_id"] == expected_client_id


def test_infer_smartlead_client_returns_none_for_preciseleads_self_campaign():
    assert infer_smartlead_client("preciselead", "PreciseLeads internal campaign") is None


def test_resolve_client_id_uses_stored_campaign_client():
    client_id = _resolve_client_id(
        {"smartlead_client_id": "12256", "smartlead_client_name": "Ryan Markman / Melior"},
        {"key": "preciselead"},
    )
    assert client_id == 12256


def test_resolve_client_id_infers_for_older_docs_without_stored_client():
    client_id = _resolve_client_id({"campaign_name": "Staff AI - April"}, {"key": "preciselead"})
    assert client_id == 145916


def test_resolve_client_id_returns_none_when_campaign_has_no_client_match():
    client_id = _resolve_client_id({"campaign_name": "PreciseLeads internal"}, {"key": "preciselead"})
    assert client_id is None


def test_resolve_client_id_rejects_invalid_stored_client_id():
    with pytest.raises(RuntimeError, match="stored Smartlead client.*invalid id"):
        _resolve_client_id({"smartlead_client_id": "not-a-number"}, {"key": "preciselead"})
