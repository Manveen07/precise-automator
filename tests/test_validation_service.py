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


def test_missing_step_one_is_rejected():
    plan = valid_plan()
    plan["sequence"] = [plan["sequence"][1]]
    assert any("sequence must include Step 1" in error for error in validate_campaign_plan(plan, {"smartlead_mcp"}))


def test_schema_validation_errors_are_human_readable():
    plan = valid_plan()
    plan["sequence"] = []

    errors = validate_campaign_plan(plan, {"smartlead_mcp"})

    assert errors == ["sequence: sequence needs at least one step"]


def test_schedule_hours_are_parsed_not_string_compared():
    plan = valid_plan()
    plan["schedule"]["start_hour"] = "9:00"
    plan["schedule"]["end_hour"] = "6 pm"
    assert validate_campaign_plan(plan, {"smartlead_mcp"}) == []


def test_blocked_phrase_uses_token_boundaries():
    plan = valid_plan()
    plan["sequence"][0]["variants"][0]["body"] = "This is guaranteed-not-to-bounce copy."
    assert validate_campaign_plan(plan, {"smartlead_mcp"}) == []

    plan["sequence"][0]["variants"][0]["body"] = "This is guaranteed copy."
    assert any("blocked phrase" in error for error in validate_campaign_plan(plan, {"smartlead_mcp"}))


def test_merge_tag_inside_spintax_block_is_rejected():
    plan = valid_plan()
    plan["sequence"][0]["variants"][0]["body"] = (
        "Hi {{first_name}},\n\n"
        "I can share {a quick example|a few ideas for {{company_name}}}.\n\n"
        "%Signature%"
    )

    errors = validate_campaign_plan(plan, {"smartlead_mcp"})

    assert any("merge tag inside a spintax block" in error for error in errors)


def test_merge_tags_next_to_spintax_blocks_are_allowed():
    plan = valid_plan()
    plan["sequence"][0]["variants"][0]["body"] = (
        "Hi {{first_name}},\n\n"
        "For {{company_name}}, I can share {a quick example|a few ideas}.\n\n"
        "%Signature%"
    )

    assert validate_campaign_plan(plan, {"smartlead_mcp"}) == []
