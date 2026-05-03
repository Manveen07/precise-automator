"""Route-level tests using mongomock and FastAPI's TestClient.

The fresh_mongomock fixture in conftest.py auto-applies, so every test gets
an empty in-memory Mongo. These tests cover the wire-up — happy paths and
the most important error cases. They do not call the real Smartlead or
Anthropic APIs.
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app
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


def test_dashboard_renders_empty_state_when_no_campaigns(client):
    response = client.get("/app")
    assert response.status_code == 200
    assert "No campaigns yet" in response.text


def test_new_campaign_page_lists_all_three_workspaces(client):
    response = client.get("/campaigns/new")
    assert response.status_code == 200
    assert 'value="preciselead"' in response.text
    assert 'value="belardi_wong"' in response.text
    assert 'value="darlean"' in response.text


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
    assert doc["raw_input"]["max_new_leads_per_day"] == 50


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
