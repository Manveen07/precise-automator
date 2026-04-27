import uuid
from json import JSONDecodeError
from types import SimpleNamespace

from app.models import CampaignDraft, CampaignRequest, ConversationSession, SmartleadWorkspace
from app.routes import campaigns


class FakeQuery:
    def __init__(self, rows):
        self.rows = rows

    def filter_by(self, **kwargs):
        filtered = []
        for row in self.rows:
            if all(getattr(row, key, None) == value for key, value in kwargs.items()):
                filtered.append(row)
        return FakeQuery(filtered)

    def order_by(self, *_args):
        return self

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class FakeDb:
    def __init__(self, campaign, latest_draft=None):
        self.campaign = campaign
        self.latest_draft = latest_draft
        self.workspaces = [SimpleNamespace(workspace_key="smartlead_mcp", active=True)]
        self.conversation_sessions = []
        self.added = []
        self.commits = 0

    def get(self, model, item_id):
        if model is CampaignRequest and item_id == self.campaign.id:
            return self.campaign
        return None

    def query(self, model):
        if model is SmartleadWorkspace:
            return FakeQuery(self.workspaces)
        if model is CampaignDraft:
            return FakeQuery([self.latest_draft] if self.latest_draft else [])
        if model is ConversationSession:
            return FakeQuery(self.conversation_sessions)
        return FakeQuery([])

    def add(self, item):
        if getattr(item, "id", None) is None:
            item.id = uuid.uuid4()
        self.added.append(item)
        if isinstance(item, ConversationSession):
            self.conversation_sessions.append(item)

    def commit(self):
        self.commits += 1


def make_campaign():
    return SimpleNamespace(
        id=uuid.uuid4(),
        raw_input_json={
            "workspace_key": "smartlead_mcp",
            "template_key": "cold_email_standard_v1",
            "campaign_name": "Darlean Benchmark",
            "max_new_leads_per_day": 100,
            "parsed_messaging": {
                "selected_campaign": "Benchmark",
                "subjects": ["Quick Benchmark"],
                "steps": [
                    {
                        "step_number": 1,
                        "body_variants": [{"variant_label": "A", "body": "Hi {{first_name}}\n%signature%"}],
                    }
                ],
            },
        },
        template=SimpleNamespace(schema_version="campaign_plan_v1", system_prompt="", example_block=""),
        status="drafting",
    )


def json_request():
    return SimpleNamespace(headers={"accept": "application/json"})


def test_generate_draft_is_deterministic_and_does_not_call_anthropic(monkeypatch):
    def fail_if_called():
        raise AssertionError("Generate Draft should not construct Anthropic service")

    monkeypatch.setattr(campaigns, "AnthropicCampaignService", fail_if_called)
    campaign = make_campaign()
    db = FakeDb(campaign)

    result = campaigns.generate_draft(campaign.id, request=json_request(), db=db)

    draft = next(item for item in db.added if isinstance(item, CampaignDraft))
    assert result["source"] == "local_parser"
    assert result["validation_status"] == "valid"
    assert draft.model_name == "local_parser"
    assert draft.draft_json["sequence"][0]["variants"][0]["subject"] == "Quick Benchmark"


def test_ai_revision_invalid_json_returns_error_without_500(monkeypatch):
    class BadAnthropicService:
        def revise_campaign_plan(self, **_kwargs):
            raise JSONDecodeError("Expecting value", "not json", 0)

    monkeypatch.setattr(campaigns, "_has_configured_anthropic_key", lambda: True)
    monkeypatch.setattr(campaigns, "AnthropicCampaignService", BadAnthropicService)
    campaign = make_campaign()
    latest_draft = CampaignDraft(
        id=uuid.uuid4(),
        request_id=campaign.id,
        draft_json={"workspace_key": "smartlead_mcp"},
        validation_status="valid",
    )
    db = FakeDb(campaign, latest_draft)

    result = campaigns.revise_draft(
        campaign.id,
        request=json_request(),
        revision_instruction="make it shorter",
        db=db,
    )

    assert result["ok"] is False
    assert "not valid CampaignPlan JSON" in result["errors"][0]
    assert db.commits == 1


def test_html_form_posts_redirect_back_to_campaign_page():
    campaign_id = uuid.uuid4()
    response = campaigns._api_or_campaign_redirect(
        SimpleNamespace(headers={"accept": "text/html,application/xhtml+xml"}),
        {"ok": True},
        campaign_id,
    )

    assert response.status_code == 303
    assert response.headers["location"] == f"/campaigns/{campaign_id}"
