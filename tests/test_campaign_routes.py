"""Route-level tests using mongomock and FastAPI's TestClient.

The fresh_mongomock fixture in conftest.py auto-applies, so every test gets
an empty in-memory Mongo. These tests cover the wire-up — happy paths and
the most important error cases. They do not call the real Smartlead or
Anthropic APIs.
"""

from copy import deepcopy

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.routes import campaigns
from app import store


@pytest.fixture
def client():
    client = TestClient(app, follow_redirects=False)
    client.auth = ("test-user", "test-password")
    return client


@pytest.fixture
def anonymous_client():
    return TestClient(app, follow_redirects=False)


# ---- Page rendering ---- #


def test_root_redirects_to_app(client):
    response = client.get("/")
    assert response.status_code == 303
    assert response.headers["location"] == "/app"


def test_protected_html_routes_redirect_to_login(anonymous_client):
    response = anonymous_client.get("/app")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/app"


def test_wrong_basic_auth_is_rejected(anonymous_client):
    response = anonymous_client.get("/app", auth=("test-user", "wrong-password"))
    assert response.status_code == 401


def test_health_is_public(anonymous_client):
    response = anonymous_client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_login_page_renders(anonymous_client):
    response = anonymous_client.get("/login")
    assert response.status_code == 200
    assert "Precise Automator" in response.text
    assert 'name="username"' in response.text


def test_login_form_sets_session_cookie_and_redirects(anonymous_client):
    response = anonymous_client.post(
        "/login",
        data={"username": "test-user", "password": "test-password", "next": "/app"},
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/app"
    assert "precise_automator_session=" in response.headers["set-cookie"]


def test_login_form_rejects_bad_credentials(anonymous_client):
    response = anonymous_client.post(
        "/login",
        data={"username": "test-user", "password": "bad-password", "next": "/app"},
    )
    assert response.status_code == 401
    assert "Invalid username or password" in response.text


def test_session_post_requires_csrf_token(anonymous_client):
    login = anonymous_client.post(
        "/login",
        data={"username": "test-user", "password": "test-password", "next": "/app"},
    )
    assert login.status_code == 303

    response = anonymous_client.post(
        "/api/campaigns/new",
        data={
            "workspace_key": "preciselead",
            "campaign_name": "CSRF Blocked",
            "messaging_text": "Subject Line Options:\n1. Test\n\nEmail 1\nV1\nBody",
        },
    )

    assert response.status_code == 403


def test_session_post_accepts_csrf_token(anonymous_client):
    login = anonymous_client.post(
        "/login",
        data={"username": "test-user", "password": "test-password", "next": "/app"},
    )
    assert login.status_code == 303
    csrf_token = anonymous_client.cookies.get("precise_automator_session")

    response = anonymous_client.post(
        "/api/campaigns/new",
        data={
            "csrf_token": csrf_token,
            "workspace_key": "preciselead",
            "campaign_name": "CSRF Allowed",
            "messaging_text": "Subject Line Options:\n1. Test\n\nEmail 1\nV1\nBody",
        },
    )

    assert response.status_code == 303


def test_dashboard_renders_empty_state_when_no_campaigns(client):
    response = client.get("/app")
    assert response.status_code == 200
    assert "No campaigns yet" in response.text
    assert 'href="/campaigns/new#existing-smartlead-target"' in response.text
    assert "Edit Existing Smartlead Campaign" not in response.text


def test_new_campaign_page_lists_all_three_workspaces(client):
    response = client.get("/campaigns/new")
    assert response.status_code == 200
    assert 'value="preciselead"' in response.text
    assert 'value="belardi_wong"' in response.text
    assert 'value="darlean"' in response.text
    assert 'name="smartlead_campaign_ref"' in response.text
    assert 'name="campaign_name" required' not in response.text


def test_campaign_detail_404_for_unknown_id(client):
    response = client.get("/campaigns/507f1f77bcf86cd799439011")
    assert response.status_code == 404


# ---- Campaign creation ---- #


def test_create_campaign_with_pasted_messaging_persists_doc_and_redirects(client):
    response = client.post(
        "/api/campaigns/new",
        data={
            "workspace_key": "preciselead",
            "campaign_name": "Test Campaign",
            "max_new_leads_per_day": "50",
            "messaging_text": "Subject Line Options:\n1. Quick test\n\nEmail 1\nV1\nHi {{first_name}}, test body.\n",
            "selected_sequence_name": "",
        },
    )
    assert response.status_code == 303
    assert response.headers["location"].startswith("/campaigns/")

    docs = store.list_recent_campaigns()
    assert len(docs) == 1
    doc = docs[0]
    assert doc["campaign_name"] == "Test Campaign"
    assert doc["smartlead_workspace"] == "preciselead"
    assert doc["smartlead_campaign_id"] is None
    assert doc["smartlead_client_id"] is None
    assert doc["raw_input"]["max_new_leads_per_day"] == 50


def test_create_campaign_infers_smartlead_client_from_campaign_name(client):
    response = client.post(
        "/api/campaigns/new",
        data={
            "workspace_key": "preciselead",
            "campaign_name": "Melior - May outbound",
            "max_new_leads_per_day": "50",
            "messaging_text": "Subject Line Options:\n1. Quick test\n\nEmail 1\nV1\nHi {{first_name}}, test body.\n",
            "selected_sequence_name": "",
        },
    )
    assert response.status_code == 303

    doc = store.list_recent_campaigns()[0]
    assert doc["smartlead_client_id"] == 12256
    assert doc["smartlead_client_name"] == "Ryan Markman / Melior"
    assert doc["smartlead_client_match"] == "melior"
    assert doc["raw_input"]["smartlead_client"]["client_id"] == 12256


def test_create_campaign_can_target_existing_smartlead_campaign(client):
    response = client.post(
        "/api/campaigns/new",
        data={
            "workspace_key": "preciselead",
            "campaign_name": "SVSG - existing target",
            "max_new_leads_per_day": "50",
            "smartlead_campaign_ref": "https://app.smartlead.ai/app/email-campaign/77777/overview",
            "messaging_text": "Subject Line Options:\n1. Quick test\n\nEmail 1\nV1\nHi {{first_name}}, test body.\n",
            "selected_sequence_name": "",
        },
    )
    assert response.status_code == 303

    doc = store.list_recent_campaigns()[0]
    assert doc["smartlead_campaign_id"] == 77777
    assert doc["smartlead_client_id"] == 145916
    assert doc["status"] == "ready"
    assert doc["raw_input"]["smartlead_campaign_id"] == 77777


def test_create_campaign_imports_existing_smartlead_v2_link_without_campaign_name(client, monkeypatch):
    async def fake_import_existing_smartlead_plan(workspace, smartlead_campaign_id, max_new_leads_per_day):
        assert smartlead_campaign_id == 3141346
        return {
            "workspace_key": workspace["key"],
            "client_key": None,
            "campaign_name": "Solo Practitioners Fractional CFO",
            "template_family": "smartlead_import_v1",
            "goal": "book_meeting",
            "lead_source": {"type": "none", "expected_count": None},
            "schedule": {
                "timezone": "America/New_York",
                "days_of_the_week": [1, 2, 3, 4, 5],
                "start_hour": "09:00",
                "end_hour": "18:00",
                "min_time_btw_emails": 17,
                "max_new_leads_per_day": max_new_leads_per_day,
            },
            "settings": {
                "send_as_plain_text": True,
                "track_opens": False,
                "track_clicks": False,
                "stop_on_reply": True,
                "enable_ai_esp_matching": True,
                "auto_pause_domain_leads_on_reply": True,
                "ooo_restart_delay_days": 10,
            },
            "inbox_selection": {"mode": "skip", "email_account_ids": [], "provider_mix": {"gmail": 0.7, "outlook": 0.3}},
            "sequence": [
                {
                    "step_number": 1,
                    "delay_days": 1,
                    "variants": [{"variant_label": "A", "subject": "Referral Dependence", "body": "Hi {{first_name}}, test body."}],
                }
            ],
            "approval_required": True,
            "notes_for_operator": ["Imported from an existing Smartlead campaign."],
        }

    monkeypatch.setattr(campaigns, "_try_import_existing_smartlead_plan", fake_import_existing_smartlead_plan)

    response = client.post(
        "/api/campaigns/new",
        data={
            "workspace_key": "preciselead",
            "max_new_leads_per_day": "50",
            "smartlead_campaign_ref": "https://app.smartlead.ai/app/email-campaigns-v2/3141346/analytics",
            "selected_sequence_name": "",
        },
    )
    assert response.status_code == 303

    doc = store.list_recent_campaigns()[0]
    assert doc["campaign_name"] == "Solo Practitioners Fractional CFO"
    assert doc["smartlead_campaign_id"] == 3141346
    assert doc["current_plan"]["sequence"][0]["variants"][0]["subject"] == "Referral Dependence"
    assert doc["validation_errors"] == []
    assert doc["status"] == "ready"
    assert doc["raw_input"]["campaign_name"] == "Solo Practitioners Fractional CFO"


def test_link_only_existing_campaign_detail_does_not_show_broken_plan_preview(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Smartlead Campaign 3141346",
        raw_input={
            "workspace_key": "preciselead",
            "campaign_name": "Smartlead Campaign 3141346",
            "messaging_text": "",
            "parsed_messaging": {"source_format": "unparsed", "subjects": [], "steps": [], "warnings": []},
        },
        plan={},
        validation_errors=[],
        smartlead_campaign_id=3141346,
        status="linked",
    )

    response = client.get(f"/campaigns/{doc['_id']}")

    assert response.status_code == 200
    assert "Linked to Smartlead" in response.text
    assert "Plan Preview" not in response.text
    assert "Enable Spintax" not in response.text
    assert "sequence needs at least one step" not in response.text
    assert f"/api/campaigns/{doc['_id']}/smartlead" in response.text
    assert "Use Existing Smartlead Campaign" not in response.text
    assert "Inspect" in response.text
    assert "Analytics" in response.text
    assert "Archive" in response.text


def test_create_campaign_rejects_invalid_existing_smartlead_campaign_ref(client):
    response = client.post(
        "/api/campaigns/new",
        data={
            "workspace_key": "preciselead",
            "campaign_name": "Bad existing target",
            "smartlead_campaign_ref": "not a campaign",
            "messaging_text": "Subject Line Options:\n1. Quick test\n\nEmail 1\nV1\nHi {{first_name}}, test body.\n",
        },
    )
    assert response.status_code == 400


def test_create_campaign_rejects_unknown_workspace(client):
    response = client.post(
        "/api/campaigns/new",
        data={
            "workspace_key": "does_not_exist",
            "campaign_name": "x",
            "messaging_text": "1. subject\n\nEmail 1\nV1\nbody\n",
        },
    )
    assert response.status_code == 400


def test_revise_preserves_current_plan_when_claude_returns_empty_sequence(client, monkeypatch):
    current_plan = {
        "workspace_key": "preciselead",
        "campaign_name": "Revision Safety",
        "sequence": [
            {
                "step_number": 1,
                "delay_days": 1,
                "variants": [{"variant_label": "A", "subject": "Quick test", "body": "Hi {{first_name}}"}],
            }
        ],
    }
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Revision Safety",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Revision Safety", "parsed_messaging": {}},
        plan=deepcopy(current_plan),
        validation_errors=[],
    )

    class FakeAnthropicCampaignService:
        def revise_campaign_plan(self, **kwargs):
            invalid_plan = deepcopy(kwargs["latest_plan"])
            invalid_plan["sequence"] = []
            return invalid_plan

    monkeypatch.setattr(campaigns, "_has_configured_anthropic_key", lambda: True)
    monkeypatch.setattr(campaigns, "AnthropicCampaignService", FakeAnthropicCampaignService)

    response = client.post(
        f"/api/campaigns/{doc['_id']}/revise",
        data={"revision_instruction": "remove the sequence"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "plan unchanged" in response.json()["errors"][0]
    assert "sequence: sequence needs at least one step" in response.json()["errors"]
    refreshed = store.get_campaign(str(doc["_id"]))
    assert refreshed["current_plan"] == current_plan


def test_spintax_preserves_current_plan_when_generated_body_is_not_smartlead_safe(client, monkeypatch):
    current_plan = {
        "workspace_key": "preciselead",
        "campaign_name": "Spintax Safety",
        "template_family": "cold_email_standard_v1",
        "goal": "book_meeting",
        "lead_source": {"type": "none", "expected_count": None},
        "schedule": {
            "timezone": "America/New_York",
            "days_of_the_week": [1, 2, 3, 4, 5],
            "start_hour": "09:00",
            "end_hour": "18:00",
            "min_time_btw_emails": 17,
            "max_new_leads_per_day": 100,
        },
        "settings": {
            "send_as_plain_text": True,
            "track_opens": False,
            "track_clicks": False,
            "stop_on_reply": True,
            "enable_ai_esp_matching": True,
            "auto_pause_domain_leads_on_reply": True,
            "ooo_restart_delay_days": 10,
        },
        "inbox_selection": {"mode": "skip", "email_account_ids": []},
        "sequence": [
            {
                "step_number": 1,
                "delay_days": 1,
                "variants": [{"variant_label": "A", "subject": "Quick test", "body": "Hi {{first_name}}, plain body."}],
            }
        ],
        "approval_required": True,
        "notes_for_operator": [],
    }
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Spintax Safety",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Spintax Safety", "parsed_messaging": {}},
        plan=deepcopy(current_plan),
        validation_errors=[],
    )

    def fake_apply_spintax_to_plan(plan, client):
        invalid_plan = deepcopy(plan)
        invalid_plan["sequence"][0]["variants"][0]["body"] = (
            "Hi {{first_name}}, I can share {a quick example|a few ideas for {{company_name}}}."
        )
        return invalid_plan, {"generated": 1, "skipped_already_spun": 0, "unique_calls": 1}

    monkeypatch.setattr(campaigns, "_has_configured_anthropic_key", lambda: True)
    monkeypatch.setattr(campaigns, "apply_spintax_to_plan", fake_apply_spintax_to_plan)

    response = client.post(f"/api/campaigns/{doc['_id']}/spintax")

    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert "previous plan preserved" in response.json()["errors"][0]
    assert any("merge tag inside a spintax block" in error for error in response.json()["errors"])
    refreshed = store.get_campaign(str(doc["_id"]))
    assert refreshed["current_plan"] == current_plan


def test_campaign_detail_shows_immediate_first_email_and_delay_form_for_followups(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Delay UI",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Delay UI", "parsed_messaging": {}},
        plan={
            "workspace_key": "preciselead",
            "campaign_name": "Delay UI",
            "template_family": "cold_email_standard_v1",
            "schedule": {},
            "settings": {},
            "inbox_selection": {"mode": "skip", "email_account_ids": []},
            "sequence": [
                {"step_number": 1, "delay_days": 0, "variants": [{"variant_label": "A", "subject": "Hi", "body": "Body"}]},
                {"step_number": 2, "delay_days": 3, "variants": [{"variant_label": "A", "subject": "", "body": "Follow up"}]},
            ],
            "approval_required": True,
            "notes_for_operator": [],
        },
        validation_errors=[],
    )

    response = client.get(f"/campaigns/{doc['_id']}")

    assert response.status_code == 200
    assert "Sends immediately" in response.text
    assert f'/api/campaigns/{doc["_id"]}/delay' in response.text
    assert 'name="delay_days"' in response.text
    assert 'value="3"' in response.text


def test_update_sequence_delay_changes_followup_delay(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Delay Update",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Delay Update", "parsed_messaging": {}},
        plan={
            "workspace_key": "preciselead",
            "campaign_name": "Delay Update",
            "template_family": "cold_email_standard_v1",
            "schedule": {},
            "settings": {},
            "inbox_selection": {"mode": "skip", "email_account_ids": []},
            "sequence": [
                {"step_number": 1, "delay_days": 0, "variants": [{"variant_label": "A", "subject": "Hi", "body": "Body"}]},
                {"step_number": 2, "delay_days": 3, "variants": [{"variant_label": "A", "subject": "", "body": "Follow up"}]},
            ],
            "approval_required": True,
            "notes_for_operator": [],
        },
        validation_errors=[],
    )
    campaign_id = str(doc["_id"])

    response = client.post(
        f"/api/campaigns/{campaign_id}/delay",
        data={"step_number": "2", "delay_days": "5"},
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    refreshed = store.get_campaign(campaign_id)
    assert refreshed["current_plan"]["sequence"][1]["delay_days"] == 5


def test_update_sequence_delay_rejects_first_email_delay(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Delay Reject",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Delay Reject", "parsed_messaging": {}},
        plan={
            "workspace_key": "preciselead",
            "campaign_name": "Delay Reject",
            "template_family": "cold_email_standard_v1",
            "schedule": {},
            "settings": {},
            "inbox_selection": {"mode": "skip", "email_account_ids": []},
            "sequence": [
                {"step_number": 1, "delay_days": 0, "variants": [{"variant_label": "A", "subject": "Hi", "body": "Body"}]},
            ],
            "approval_required": True,
            "notes_for_operator": [],
        },
        validation_errors=[],
    )

    response = client.post(
        f"/api/campaigns/{doc['_id']}/delay",
        data={"step_number": "1", "delay_days": "5"},
    )

    assert response.status_code == 400


# ---- Smartlead linking ---- #


def test_link_existing_smartlead_campaign_updates_doc(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Link Test",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Link Test", "parsed_messaging": {}},
        plan={"sequence": [], "schedule": {}, "settings": {}, "workspace_key": "preciselead"},
        validation_errors=[],
    )
    campaign_id = str(doc["_id"])

    response = client.post(
        f"/api/campaigns/{campaign_id}/smartlead/link",
        data={"smartlead_campaign_ref": "https://app.smartlead.ai/app/email-campaign/77777/overview"},
    )
    assert response.status_code in (200, 303)

    refreshed = store.get_campaign(campaign_id)
    assert refreshed["smartlead_campaign_id"] == 77777
    assert refreshed["status"] == "synced"


def test_link_rejects_invalid_smartlead_ref(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Bad Link",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Bad Link", "parsed_messaging": {}},
        plan={},
        validation_errors=[],
    )
    response = client.post(
        f"/api/campaigns/{doc['_id']}/smartlead/link",
        data={"smartlead_campaign_ref": "this is not a number or url"},
    )
    assert response.status_code == 400


def test_dashboard_renders_local_delete_action_for_draft(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Test Local MCB2",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Test Local MCB2", "parsed_messaging": {}},
        plan={"sequence": [], "schedule": {}, "settings": {}, "workspace_key": "preciselead"},
        validation_errors=[],
    )

    response = client.get("/app")

    assert response.status_code == 200
    assert f'/api/campaigns/{doc["_id"]}/local-delete' in response.text
    assert 'aria-label="Delete local draft"' in response.text
    assert ">Open<" not in response.text


def test_local_delete_removes_draft_without_smartlead_call(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Delete Local Draft",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Delete Local Draft", "parsed_messaging": {}},
        plan={},
        validation_errors=[],
    )
    campaign_id = str(doc["_id"])

    response = client.post(f"/api/campaigns/{campaign_id}/local-delete")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert store.get_campaign(campaign_id) is None


def test_campaign_detail_renders_local_delete_for_draft(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Detail Local Draft",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Detail Local Draft", "parsed_messaging": {}},
        plan={"sequence": [], "schedule": {}, "settings": {}, "workspace_key": "preciselead"},
        validation_errors=[],
    )

    response = client.get(f"/campaigns/{doc['_id']}")

    assert response.status_code == 200
    assert "Delete local draft" in response.text
    assert f'/api/campaigns/{doc["_id"]}/local-delete' in response.text


def test_smartlead_snapshot_html_renders_campaign_name(client, monkeypatch):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="HTML Snapshot Test",
        raw_input={"workspace_key": "preciselead", "campaign_name": "HTML Snapshot Test", "parsed_messaging": {}},
        plan={},
        validation_errors=[],
        smartlead_campaign_id=123,
        status="linked",
    )

    class FakeSmartleadReport:
        def campaign_url(self, campaign_id: int) -> str:
            return f"https://app.smartlead.ai/app/email-campaign/{campaign_id}/overview"

        async def get_campaign(self, campaign_id: int) -> dict:
            return {"id": campaign_id, "name": "Remote Campaign"}

        async def get_sequences(self, campaign_id: int) -> list[dict]:
            return []

    monkeypatch.setattr(campaigns, "_smartlead_for_doc", lambda doc: FakeSmartleadReport())

    response = client.get(
        f"/api/campaigns/{doc['_id']}/smartlead",
        headers={"accept": "text/html"},
    )

    assert response.status_code == 200
    assert "Smartlead ID 123" in response.text
    assert "HTML Snapshot Test" in response.text
    assert "Campaign" in response.text


def test_campaign_status_endpoint_returns_current_sync_state(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Status Test",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Status Test", "parsed_messaging": {}},
        plan={"sequence": [], "schedule": {}, "settings": {}, "workspace_key": "preciselead"},
        validation_errors=[],
    )
    store.campaigns_collection().update_one(
        {"_id": doc["_id"]},
        {"$set": {"status": "syncing", "smartlead_campaign_id": 12345}},
    )

    response = client.get(f"/api/campaigns/{doc['_id']}/status")

    assert response.status_code == 200
    assert response.json()["status"] == "syncing"
    assert response.json()["smartlead_campaign_id"] == 12345


def test_syncing_campaign_detail_includes_status_polling(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Polling Test",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Polling Test", "parsed_messaging": {}},
        plan={"sequence": [], "schedule": {}, "settings": {}, "workspace_key": "preciselead"},
        validation_errors=[],
    )
    store.campaigns_collection().update_one({"_id": doc["_id"]}, {"$set": {"status": "syncing"}})

    response = client.get(f"/campaigns/{doc['_id']}")

    assert response.status_code == 200
    assert f"/api/campaigns/{doc['_id']}/status" in response.text


class FakeSmartleadLifecycle:
    def __init__(self, *, delete_error: httpx.HTTPStatusError | None = None, archive_error: httpx.HTTPStatusError | None = None):
        self.delete_error = delete_error
        self.archive_error = archive_error

    async def delete_campaign(self, campaign_id: int) -> dict:
        if self.delete_error:
            raise self.delete_error
        return {"ok": True, "deleted_id": campaign_id}

    async def archive_campaign(self, campaign_id: int) -> dict:
        if self.archive_error:
            raise self.archive_error
        return {"ok": True, "archived_id": campaign_id}


def _smartlead_http_error(status_code: int, body: str = "") -> httpx.HTTPStatusError:
    request = httpx.Request("DELETE", "https://server.smartlead.ai/api/v1/campaigns/123?api_key=secret")
    response = httpx.Response(status_code, request=request, text=body)
    return httpx.HTTPStatusError("smartlead error", request=request, response=response)


def test_delete_removes_local_doc_when_smartlead_delete_succeeds(client, monkeypatch):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Delete Success",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Delete Success", "parsed_messaging": {}},
        plan={},
        validation_errors=[],
    )
    campaign_id = str(doc["_id"])
    store.attach_smartlead(campaign_id, 123)
    monkeypatch.setattr(campaigns, "_smartlead_for_doc", lambda doc: FakeSmartleadLifecycle())

    response = client.post(f"/api/campaigns/{campaign_id}/smartlead/delete")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert store.get_campaign(campaign_id) is None


def test_delete_removes_local_doc_when_smartlead_campaign_is_already_gone(client, monkeypatch):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Delete Already Gone",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Delete Already Gone", "parsed_messaging": {}},
        plan={},
        validation_errors=[],
    )
    campaign_id = str(doc["_id"])
    store.attach_smartlead(campaign_id, 123)
    monkeypatch.setattr(
        campaigns,
        "_smartlead_for_doc",
        lambda doc: FakeSmartleadLifecycle(delete_error=_smartlead_http_error(404, "not found")),
    )

    response = client.post(f"/api/campaigns/{campaign_id}/smartlead/delete")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert "already missing" in response.json()["note"]
    assert store.get_campaign(campaign_id) is None


def test_delete_smartlead_error_marks_failed_instead_of_500(client, monkeypatch):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Delete Failure",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Delete Failure", "parsed_messaging": {}},
        plan={},
        validation_errors=[],
    )
    campaign_id = str(doc["_id"])
    store.attach_smartlead(campaign_id, 123)
    monkeypatch.setattr(
        campaigns,
        "_smartlead_for_doc",
        lambda doc: FakeSmartleadLifecycle(delete_error=_smartlead_http_error(500, "server failed")),
    )

    response = client.post(f"/api/campaigns/{campaign_id}/smartlead/delete")

    assert response.status_code == 200
    assert response.json()["ok"] is False
    refreshed = store.get_campaign(campaign_id)
    assert refreshed["status"] == "failed"
    assert "HTTP 500" in refreshed["last_sync_error"]
    assert "api_key" not in refreshed["last_sync_error"]


def test_archive_removes_local_doc_when_smartlead_campaign_is_already_gone(client, monkeypatch):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Archive Already Gone",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Archive Already Gone", "parsed_messaging": {}},
        plan={},
        validation_errors=[],
    )
    campaign_id = str(doc["_id"])
    store.attach_smartlead(campaign_id, 123)
    monkeypatch.setattr(
        campaigns,
        "_smartlead_for_doc",
        lambda doc: FakeSmartleadLifecycle(archive_error=_smartlead_http_error(404, "not found")),
    )

    response = client.post(f"/api/campaigns/{campaign_id}/smartlead/archive")

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert store.get_campaign(campaign_id) is None


# ---- Sync gating ---- #


def test_sync_rejects_when_validation_errors_exist(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Bad Plan",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Bad Plan", "parsed_messaging": {}},
        plan={},
        validation_errors=["something is wrong"],
    )
    response = client.post(f"/api/campaigns/{doc['_id']}/sync")
    assert response.status_code == 400


# ---- Inbox selection ---- #


def _inbox_row(**overrides):
    base = {
        "Client": "PRECISE_LEADS",
        "Email": "a@preciselead.in",
        "Provider": "Gmail",
        "Account ID": "1001",
        "Availability": "FREE",
        "Busy Reason": "",
        "# Campaigns": 0,
        "Avail. Capacity": 10,
        "Capacity Left": 10,
        "Warmup State": "ramped",
        "Warmup Rep %": "100%",
        "Test Status": "inbox",
        "Test Date": "2026-06-18",
    }
    base.update(overrides)
    return base


def _inbox_campaign():
    return store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Inbox Test",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Inbox Test", "parsed_messaging": {}},
        plan={
            "workspace_key": "preciselead",
            "campaign_name": "Inbox Test",
            "template_family": "cold_email_standard_v1",
            "schedule": {"max_new_leads_per_day": 25},
            "settings": {},
            "inbox_selection": {"mode": "skip", "email_account_ids": []},
            "sequence": [
                {"step_number": 1, "delay_days": 0, "variants": [{"variant_label": "A", "subject": "Hi", "body": "Body"}]},
            ],
            "approval_required": True,
            "notes_for_operator": [],
        },
        validation_errors=[],
    )


def test_get_inboxes_recommends_only_the_campaign_client(client, monkeypatch):
    rows = [
        _inbox_row(Email="mine@preciselead.in", Client="PRECISE_LEADS", **{"Account ID": "1001"}),
        _inbox_row(Email="other@x.com", Client="DARLEAN", **{"Account ID": "2002"}),
    ]
    monkeypatch.setattr(campaigns, "fetch_inbox_rows", lambda *a, **k: rows)
    doc = _inbox_campaign()

    response = client.get(f"/api/campaigns/{doc['_id']}/inboxes")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["client"] == "PRECISE_LEADS"
    emails = {r["email"] for r in body["free_pool"]}
    assert emails == {"mine@preciselead.in"}


def test_get_inboxes_respects_provider_mix(client, monkeypatch):
    rows = [
        _inbox_row(Email="g1@preciselead.in", Client="PRECISE_LEADS", Provider="Gmail", **{"Account ID": "1001", "Avail. Capacity": 50}),
        _inbox_row(Email="o1@preciselead.in", Client="PRECISE_LEADS", Provider="Outlook", **{"Account ID": "1002", "Avail. Capacity": 50}),
    ]
    monkeypatch.setattr(campaigns, "fetch_inbox_rows", lambda *a, **k: rows)
    
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Inbox Test Ratio",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Inbox Test Ratio", "parsed_messaging": {}},
        plan={
            "workspace_key": "preciselead",
            "campaign_name": "Inbox Test Ratio",
            "template_family": "cold_email_standard_v1",
            "schedule": {"max_new_leads_per_day": 100},
            "settings": {},
            "inbox_selection": {
                "mode": "recommend",
                "email_account_ids": [],
                "provider_mix": {"gmail": 0.5, "outlook": 0.5}
            },
            "sequence": [
                {"step_number": 1, "delay_days": 0, "variants": [{"variant_label": "A", "subject": "Hi", "body": "Body"}]},
            ],
            "approval_required": True,
            "notes_for_operator": [],
        },
        validation_errors=[],
    )
    
    response = client.get(f"/api/campaigns/{doc['_id']}/inboxes")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    emails = {r["email"] for r in body["recommended"]}
    assert emails == {"g1@preciselead.in", "o1@preciselead.in"}
    assert body["provider_counts"] == {"gmail": 1, "outlook": 1}


def test_get_inboxes_reports_sheet_error_without_crashing(client, monkeypatch):

    from app.services.inbox_sheet_service import InboxSheetError

    def boom(*a, **k):
        raise InboxSheetError("sheet down")

    monkeypatch.setattr(campaigns, "fetch_inbox_rows", boom)
    doc = _inbox_campaign()

    response = client.get(f"/api/campaigns/{doc['_id']}/inboxes")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert "sheet down" in body["error"]


def test_post_inbox_selection_persists_account_ids(client, monkeypatch):
    rows = [
        _inbox_row(Email="a@preciselead.in", **{"Account ID": "1001"}),
        _inbox_row(Email="b@preciselead.in", **{"Account ID": "1002"}),
    ]
    monkeypatch.setattr(campaigns, "fetch_inbox_rows", lambda *a, **k: rows)
    doc = _inbox_campaign()
    campaign_id = str(doc["_id"])

    response = client.post(
        f"/api/campaigns/{campaign_id}/inbox-selection",
        data={"account_ids": ["1001", "1002"]},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    refreshed = store.get_campaign(campaign_id)
    selection = refreshed["current_plan"]["inbox_selection"]
    assert selection["mode"] == "manual_ids"
    assert selection["email_account_ids"] == [1001, 1002]


def test_post_inbox_selection_rejects_ineligible_account(client, monkeypatch):
    rows = [_inbox_row(Email="a@preciselead.in", **{"Account ID": "1001"})]
    monkeypatch.setattr(campaigns, "fetch_inbox_rows", lambda *a, **k: rows)
    doc = _inbox_campaign()

    response = client.post(
        f"/api/campaigns/{doc['_id']}/inbox-selection",
        data={"account_ids": ["9999"]},  # not in the client's FREE pool
    )
    assert response.status_code == 400


def test_campaign_detail_renders_inbox_panel(client):
    doc = _inbox_campaign()
    response = client.get(f"/campaigns/{doc['_id']}")
    assert response.status_code == 200
    assert 'id="inbox-panel"' in response.text
    assert f'/api/campaigns/{doc["_id"]}/inboxes' in response.text
    assert 'id="inbox-form"' in response.text


def test_campaign_detail_uses_variant_tabs_for_multi_variant_step(client):
    doc = store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Tabs Test",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Tabs Test", "parsed_messaging": {}},
        plan={
            "workspace_key": "preciselead",
            "campaign_name": "Tabs Test",
            "template_family": "cold_email_standard_v1",
            "schedule": {"max_new_leads_per_day": 25},
            "settings": {},
            "inbox_selection": {"mode": "skip", "email_account_ids": []},
            "sequence": [
                {"step_number": 1, "delay_days": 0, "variants": [
                    {"variant_label": "A", "subject": "Subject One", "body": "Shared body"},
                    {"variant_label": "B", "subject": "Subject Two", "body": "Shared body"},
                ]},
            ],
            "approval_required": True,
            "notes_for_operator": [],
        },
        validation_errors=[],
    )
    response = client.get(f"/campaigns/{doc['_id']}")
    assert response.status_code == 200
    assert 'class="variant-tabs"' in response.text
    assert 'class="variant-panel' in response.text
    assert "Subject One" in response.text and "Subject Two" in response.text


def test_dashboard_renders_stat_cards_and_filters(client):
    store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Dash Redesign",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Dash Redesign", "parsed_messaging": {}},
        plan={"sequence": [], "schedule": {}, "settings": {}, "workspace_key": "preciselead"},
        validation_errors=[],
    )
    response = client.get("/app")
    assert response.status_code == 200
    assert "stat-cards" in response.text
    assert 'class="filter-chip' in response.text
    assert 'class="grid-row"' in response.text
    assert "campaign-search" in response.text


def test_new_campaign_renders_toggle_and_dropzone(client):
    response = client.get("/campaigns/new")
    assert response.status_code == 200
    assert 'class="segmented"' in response.text
    assert 'class="dropzone"' in response.text
    assert 'id="messaging-file"' in response.text
    assert 'name="messaging_text"' in response.text
    assert 'name="workspace_key"' in response.text
    assert 'id="existing-smartlead-target"' in response.text


def test_campaign_detail_renders_two_column_layout(client):
    doc = _inbox_campaign()
    response = client.get(f"/campaigns/{doc['_id']}")
    assert response.status_code == 200
    assert 'class="detail-grid"' in response.text
    assert 'class="detail-sidebar"' in response.text
    assert "Sequence plan" in response.text
    assert "danger-zone" in response.text
    assert "Overview" in response.text


def test_edit_sequence_variant_updates_subject_and_body(client):
    doc = _inbox_campaign()
    cid = str(doc["_id"])
    response = client.post(
        f"/api/campaigns/{cid}/sequence-edit",
        data={"step_number": "1", "variant_index": "0", "subject": "New subject", "body": "Edited body line."},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    variant = store.get_campaign(cid)["current_plan"]["sequence"][0]["variants"][0]
    assert variant["subject"] == "New subject"
    assert variant["body"] == "Edited body line."


def test_edit_sequence_variant_rejects_unknown_step(client):
    doc = _inbox_campaign()
    response = client.post(
        f"/api/campaigns/{doc['_id']}/sequence-edit",
        data={"step_number": "9", "variant_index": "0", "body": "x"},
    )
    assert response.status_code == 404


def test_get_inboxes_respects_gmail_ratio_override(client, monkeypatch):
    rows = [
        _inbox_row(Email="g1@preciselead.in", Provider="Gmail", **{"Account ID": "1", "Avail. Capacity": 50}),
        _inbox_row(Email="g2@preciselead.in", Provider="Gmail", **{"Account ID": "2", "Avail. Capacity": 50}),
        _inbox_row(Email="o1@preciselead.in", Provider="Outlook", **{"Account ID": "3", "Avail. Capacity": 50}),
    ]
    monkeypatch.setattr(campaigns, "fetch_inbox_rows", lambda *a, **k: rows)
    doc = _inbox_campaign()
    cid = str(doc["_id"])

    gmail_only = client.get(f"/api/campaigns/{cid}/inboxes?gmail_ratio=1.0").json()
    assert gmail_only["recommended"]
    assert gmail_only["provider_counts"]["outlook"] == 0
    assert gmail_only["provider_mix"] == {"gmail": 1.0, "outlook": 0.0}

    outlook_only = client.get(f"/api/campaigns/{cid}/inboxes?gmail_ratio=0.0").json()
    assert outlook_only["recommended"]
    assert outlook_only["provider_counts"]["gmail"] == 0


def test_post_inbox_selection_persists_provider_mix(client, monkeypatch):
    rows = [_inbox_row(Email="a@preciselead.in", **{"Account ID": "1001"})]
    monkeypatch.setattr(campaigns, "fetch_inbox_rows", lambda *a, **k: rows)
    doc = _inbox_campaign()
    cid = str(doc["_id"])

    response = client.post(
        f"/api/campaigns/{cid}/inbox-selection",
        data={"account_ids": ["1001"], "gmail_ratio": "0.5"},
    )
    assert response.status_code == 200
    selection = store.get_campaign(cid)["current_plan"]["inbox_selection"]
    assert selection["provider_mix"] == {"gmail": 0.5, "outlook": 0.5}


def test_campaign_detail_renders_provider_mix_selector(client):
    doc = _inbox_campaign()
    response = client.get(f"/campaigns/{doc['_id']}")
    assert response.status_code == 200
    assert 'id="inbox-mix"' in response.text
    assert 'name="gmail_ratio"' in response.text


def _two_variant_campaign():
    return store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Dist Test",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Dist Test", "parsed_messaging": {}},
        plan={
            "workspace_key": "preciselead",
            "campaign_name": "Dist Test",
            "template_family": "cold_email_standard_v1",
            "schedule": {}, "settings": {},
            "inbox_selection": {"mode": "skip", "email_account_ids": []},
            "sequence": [{"step_number": 1, "delay_days": 0, "variants": [
                {"variant_label": "A", "subject": "S1", "body": "b1"},
                {"variant_label": "B", "subject": "S2", "body": "b2"},
            ]}],
            "approval_required": True, "notes_for_operator": [],
        },
        validation_errors=[],
    )


def test_variant_distribution_persists_percentages(client):
    doc = _two_variant_campaign()
    cid = str(doc["_id"])
    response = client.post(
        f"/api/campaigns/{cid}/variant-distribution",
        data={"step_number": "1", "percentages": ["70", "30"]},
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True
    variants = store.get_campaign(cid)["current_plan"]["sequence"][0]["variants"]
    assert variants[0]["distribution_percentage"] == 70
    assert variants[1]["distribution_percentage"] == 30


def test_variant_distribution_rejects_non_100_sum(client):
    doc = _two_variant_campaign()
    response = client.post(
        f"/api/campaigns/{doc['_id']}/variant-distribution",
        data={"step_number": "1", "percentages": ["70", "20"]},
    )
    assert response.status_code == 400


def test_variant_distribution_rejects_count_mismatch(client):
    doc = _two_variant_campaign()
    response = client.post(
        f"/api/campaigns/{doc['_id']}/variant-distribution",
        data={"step_number": "1", "percentages": ["100"]},
    )
    assert response.status_code == 400


def test_create_twin_campaign_injects_fixed_sequence(client):
    resp = client.post(
        "/api/campaigns/new",
        data={"workspace_key": "darlean", "campaign_name": "Events - Twain", "is_twin": "true"},
    )
    assert resp.status_code == 303
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    from app import store
    doc = store.get_campaign(cid)
    assert doc["is_twin"] is True
    seq = doc["current_plan"]["sequence"]
    assert seq[0]["variants"][0]["subject"] == "{{Subject 1}}"
    assert "{{Step 3}}" in seq[1]["variants"][0]["body"]


def test_mark_as_twin_persists_flag_and_url(client):
    resp = client.post(
        "/api/campaigns/new",
        data={"workspace_key": "darlean", "campaign_name": "Plain"},
    )
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    url = "https://app.smartlead.ai/app/email-campaign/777/overview"
    r2 = client.post(f"/api/campaigns/{cid}/twin", data={"is_twin": "true", "twin_smartlead_url": url})
    assert r2.status_code in (200, 303)
    from app import store
    doc = store.get_campaign(cid)
    assert doc["is_twin"] is True
    assert "777" in doc["twin_smartlead_url"]


def test_twin_fix_rejected_for_non_twin(client):
    resp = client.post("/api/campaigns/new", data={"workspace_key": "darlean", "campaign_name": "Plain"})
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/api/campaigns/{cid}/twin-fix", data={})
    assert r.status_code == 400


def test_twin_fix_schedules_background_task(client, monkeypatch):
    import app.routes.campaigns as routes
    calls = {}
    monkeypatch.setattr(routes, "run_twin_fix_now", lambda cid, url=None: calls.setdefault("args", (cid, url)))
    resp = client.post(
        "/api/campaigns/new",
        data={"workspace_key": "darlean", "campaign_name": "Events - Twain", "is_twin": "true"},
    )
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/api/campaigns/{cid}/twin-fix", data={"twin_smartlead_url": ""})
    assert r.status_code in (200, 303)
    assert calls["args"][0] == cid
    # The route flags the campaign as running so the UI can show progress.
    status = client.get(f"/api/campaigns/{cid}/status").json()
    assert status["twin_fix_running"] is True


def test_detail_renders_when_raw_input_missing_parsed_messaging(client):
    """A twin campaign with an empty raw_input must still render (no 500)."""
    plan = {
        "sequence": [{"step_number": 1, "delay_days": 0,
                      "variants": [{"variant_label": "A", "subject": "{{Subject 1}}", "body": "x"}]}],
        "schedule": {"max_new_leads_per_day": 100, "start_hour": "09:00",
                     "end_hour": "18:00", "timezone": "America/New_York"},
    }
    doc = store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan=plan,
        validation_errors=[], is_twin=True, smartlead_campaign_id=999,
    )
    r = client.get(f"/campaigns/{str(doc['_id'])}")
    assert r.status_code == 200
    assert ">Twain<" in r.text


def test_save_linkedin_messages_sets_channel_steps(client):
    resp = client.post("/api/campaigns/new", data={"workspace_key": "darlean", "campaign_name": "LI"})
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    # Send multi-value form field as explicit urlencoded body (httpx list-in-dict is unreliable)
    import urllib.parse
    body = urllib.parse.urlencode([("messages", "Hi {{first_name}}"), ("messages", "Follow up")])
    r = client.post(
        f"/api/campaigns/{cid}/linkedin-messages",
        content=body,
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code in (200, 303)
    from app.schemas.campaign_plan import linkedin_messages
    from app import store
    plan = store.get_campaign(cid)["current_plan"]
    assert linkedin_messages(plan) == ["Hi {{first_name}}", "Follow up"]


def test_heyreach_create_rejected_without_linkedin_steps(client):
    resp = client.post("/api/campaigns/new", data={"workspace_key": "darlean", "campaign_name": "Email only",
                       "messaging_text": "Subject Line Options:\n1. T\n\nEmail 1\nV1\nBody"})
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    r = client.post(f"/api/campaigns/{cid}/heyreach-create", data={})
    assert r.status_code == 400


def test_heyreach_create_schedules_and_flags(client, monkeypatch):
    import app.routes.campaigns as routes
    calls = {}
    monkeypatch.setattr(routes, "create_heyreach_campaign_now", lambda cid: calls.setdefault("cid", cid))
    resp = client.post("/api/campaigns/new", data={"workspace_key": "darlean", "campaign_name": "LI"})
    cid = resp.headers["location"].rsplit("/", 1)[-1]
    import urllib.parse
    body = urllib.parse.urlencode([("messages", "Hi")])
    client.post(f"/api/campaigns/{cid}/linkedin-messages", content=body,
                headers={"content-type": "application/x-www-form-urlencoded"})
    r = client.post(f"/api/campaigns/{cid}/heyreach-create", data={})
    assert r.status_code in (200, 303)
    assert calls["cid"] == cid
    assert client.get(f"/api/campaigns/{cid}/status").json()["heyreach_creating"] is True


@pytest.fixture
def sample_campaign_doc():
    """A campaign with a minimal email sequence (no LinkedIn steps)."""
    return store.insert_campaign(
        workspace_key="preciselead",
        campaign_name="Sample Campaign",
        raw_input={"workspace_key": "preciselead", "campaign_name": "Sample Campaign", "parsed_messaging": {}},
        plan={
            "workspace_key": "preciselead",
            "campaign_name": "Sample Campaign",
            "template_family": "cold_email_standard_v1",
            "schedule": {"max_new_leads_per_day": 50, "start_hour": "09:00", "end_hour": "18:00", "timezone": "America/New_York"},
            "settings": {"send_as_plain_text": True, "track_opens": False, "track_clicks": False, "stop_on_reply": True,
                         "enable_ai_esp_matching": True, "auto_pause_domain_leads_on_reply": True, "ooo_restart_delay_days": 10},
            "inbox_selection": {"mode": "skip", "email_account_ids": []},
            "sequence": [
                {"step_number": 1, "delay_days": 0, "variants": [{"variant_label": "A", "subject": "Hi", "body": "Body"}]},
            ],
            "approval_required": False,
            "notes_for_operator": [],
        },
        validation_errors=[],
    )


def test_campaign_status_includes_has_linkedin_steps(client, sample_campaign_doc):
    """Status endpoint reports has_linkedin_steps=True when plan has LinkedIn steps."""
    # Insert campaign with LinkedIn steps in plan
    from app import store as app_store
    plan_with_li = dict(sample_campaign_doc.get("current_plan") or {})
    plan_with_li["sequence"] = plan_with_li.get("sequence", []) + [{
        "step_number": 99, "channel": "linkedin", "delay_days": 0,
        "linkedin_subtype": "dm", "variants": [{"body": "DM body"}]
    }]
    campaign_id = str(sample_campaign_doc["_id"])
    app_store.update_plan(campaign_id, plan_with_li, [])

    resp = client.get(f"/api/campaigns/{campaign_id}/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_linkedin_steps"] is True
