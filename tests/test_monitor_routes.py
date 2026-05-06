from fastapi.testclient import TestClient

from app.main import app
from app.routes import monitor
from app.services.smartlead_monitor_service import BounceClassification, CampaignAccount


def test_smartlead_monitor_rejects_bad_secret(monkeypatch):
    monkeypatch.setattr(monitor.settings, "SMARTLEAD_WEBHOOK_SECRET", "secret")
    client = TestClient(app)

    response = client.post("/api/monitor/smartlead?secret=wrong", json={"campaign_id": 123, "status": "PAUSED"})

    assert response.status_code == 401


def test_smartlead_monitor_classifies_pause_and_posts_slack(monkeypatch):
    posted = {}

    async def fake_find_account(campaign_id):
        assert campaign_id == 2939345
        return CampaignAccount("belardi_wong", "Belardi Wong", "key"), {"name": "Integrated Digital", "status": "PAUSED"}

    async def fake_classify_pause(*, campaign_id, account, webhook_payload):
        return BounceClassification("likely_bounce_protection", 0.0828, 157, 13)

    async def fake_post_alert(**kwargs):
        posted.update(kwargs)

    monkeypatch.setattr(monitor.settings, "SMARTLEAD_WEBHOOK_SECRET", "")
    monkeypatch.setattr(monitor, "find_account_for_campaign", fake_find_account)
    monkeypatch.setattr(monitor, "classify_pause", fake_classify_pause)
    monkeypatch.setattr(monitor, "post_campaign_status_alert", fake_post_alert)

    response = TestClient(app).post(
        "/api/monitor/smartlead",
        json={"campaign_id": 2939345, "status": "PAUSED", "campaign_name": "Integrated Digital"},
    )

    assert response.status_code == 200
    assert response.json()["classification"] == "likely_bounce_protection"
    assert posted["campaign_id"] == 2939345
    assert posted["classification"].kind == "likely_bounce_protection"


def test_slack_resume_action_calls_smartlead(monkeypatch):
    resumed = {}

    async def fake_find_account(campaign_id):
        return CampaignAccount("belardi_wong", "Belardi Wong", "key"), {"name": "Campaign"}

    async def fake_resume(campaign_id, account):
        resumed["campaign_id"] = campaign_id
        resumed["account"] = account.workspace_name
        return {"ok": True}

    async def fake_response(payload, text):
        resumed["response_text"] = text

    monkeypatch.setattr(monitor, "verify_slack_signature", lambda raw, headers: True)
    monkeypatch.setattr(monitor, "find_account_for_campaign", fake_find_account)
    monkeypatch.setattr(monitor, "resume_campaign", fake_resume)
    monkeypatch.setattr(monitor, "_post_slack_response", fake_response)

    payload = {
        "user": {"name": "Manveen"},
        "response_url": "https://slack.example/response",
        "actions": [
            {
                "action_id": "campaign_action_select",
                "selected_option": {
                    "value": '{"action":"ACTIVE","campaign_id":2939345,"campaign_name":"Integrated Digital"}'
                },
            }
        ],
    }
    response = TestClient(app).post("/api/monitor/slack/actions", data={"payload": __import__("json").dumps(payload)})

    assert response.status_code == 200
    assert resumed["campaign_id"] == 2939345
    assert resumed["account"] == "Belardi Wong"
    assert "resumed" in resumed["response_text"]


def test_slack_resume_action_failure_still_returns_200_and_posts_error(monkeypatch):
    result = {}

    async def fake_find_account(campaign_id):
        return CampaignAccount("belardi_wong", "Belardi Wong", "key"), {"name": "Campaign"}

    async def fake_resume(campaign_id, account):
        raise RuntimeError("Smartlead POST failed: api_key=secret-token")

    async def fake_response(payload, text):
        result["response_text"] = text

    monkeypatch.setattr(monitor, "verify_slack_signature", lambda raw, headers: True)
    monkeypatch.setattr(monitor, "find_account_for_campaign", fake_find_account)
    monkeypatch.setattr(monitor, "resume_campaign", fake_resume)
    monkeypatch.setattr(monitor, "_post_slack_response", fake_response)

    payload = {
        "user": {"name": "Manveen"},
        "response_url": "https://slack.example/response",
        "actions": [
            {
                "action_id": "campaign_action_select",
                "selected_option": {
                    "value": '{"action":"ACTIVE","campaign_id":2939345,"campaign_name":"Integrated Digital"}'
                },
            }
        ],
    }

    response = TestClient(app).post("/api/monitor/slack/actions", data={"payload": __import__("json").dumps(payload)})

    assert response.status_code == 200
    assert "Could not apply action" in result["response_text"]
    assert "api_key=[redacted]" in result["response_text"]
    assert "secret-token" not in result["response_text"]
