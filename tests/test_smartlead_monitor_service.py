import pytest

from app.services import smartlead_monitor_service
from app.services.smartlead_monitor_service import (
    CampaignAccount,
    classify_pause,
    explicit_pause_reason,
)


@pytest.mark.anyio
async def test_classify_pause_marks_likely_bounce_protection_from_analytics(monkeypatch):
    async def fake_analytics(campaign_id, api_key):
        return {"unique_sent_count": "157", "bounce_count": "13"}

    monkeypatch.setattr(smartlead_monitor_service, "_safe_campaign_analytics", fake_analytics)

    classification = await classify_pause(
        campaign_id=2939345,
        account=CampaignAccount("belardi_wong", "Belardi Wong", "key"),
        webhook_payload={"status": "PAUSED"},
    )

    assert classification.kind == "likely_bounce_protection"
    assert classification.sent == 157
    assert classification.bounced == 13
    assert round(classification.bounce_rate * 100, 2) == 8.28


@pytest.mark.anyio
async def test_classify_pause_marks_confirmed_when_payload_has_bounce_reason(monkeypatch):
    async def fake_analytics(campaign_id, api_key):
        return {"unique_sent_count": 20, "bounce_count": 1}

    monkeypatch.setattr(smartlead_monitor_service, "_safe_campaign_analytics", fake_analytics)

    classification = await classify_pause(
        campaign_id=10,
        account=CampaignAccount("preciselead", "PreciseLead", "key"),
        webhook_payload={"pause_reason": "Auto-paused because bounce protection was triggered"},
    )

    assert classification.kind == "confirmed_bounce_protection"
    assert classification.explicit_reason == "Auto-paused because bounce protection was triggered"


def test_explicit_pause_reason_checks_common_payload_fields():
    assert explicit_pause_reason({"status_reason": "Bounce protection triggered"}) == "Bounce protection triggered"
    assert explicit_pause_reason({"metadata": {"note": "auto pause from bounce rate"}}) == "auto pause from bounce rate"
    assert explicit_pause_reason({"message": ""}) is None
