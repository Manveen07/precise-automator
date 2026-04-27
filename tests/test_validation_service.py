from app.services.validation_service import validate_campaign_plan


def valid_plan() -> dict:
    return {
        "workspace_key": "smartlead_mcp",
        "campaign_name": "Test Campaign",
        "template_family": "cold_email_standard_v1",
        "lead_source": {"type": "none"},
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
                "variants": [{"variant_label": "A", "subject": "hello", "body": "Hi {{first_name}}"}],
            },
            {
                "step_number": 2,
                "delay_days": 3,
                "variants": [{"variant_label": "A", "subject": "", "body": "Bumping this up"}],
            },
        ],
        "approval_required": True,
        "notes_for_operator": [],
    }


def test_valid_campaign_plan_has_no_errors():
    assert validate_campaign_plan(valid_plan(), {"smartlead_mcp"}) == []


def test_follow_up_subject_is_rejected():
    plan = valid_plan()
    plan["sequence"][1]["variants"][0]["subject"] = "bad followup subject"
    assert any("follow-up subjects" in error for error in validate_campaign_plan(plan, {"smartlead_mcp"}))
