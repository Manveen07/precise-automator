from app.services.local_plan_service import build_campaign_plan_from_input, build_twin_campaign_plan
from app.services.validation_service import validate_campaign_plan


def test_delay_days_derived_from_parsed_day_offsets():
    plan = build_campaign_plan_from_input(
        {
            "workspace_key": "w",
            "campaign_name": "c",
            "parsed_messaging": {
                "subjects": ["S"],
                "steps": [
                    {"step_number": 1, "day": 0, "body_variants": [{"variant_label": "A", "body": "one"}]},
                    {"step_number": 2, "day": 5, "body_variants": [{"variant_label": "A", "body": "two"}]},
                    {"step_number": 3, "day": 10, "body_variants": [{"variant_label": "A", "body": "three"}]},
                ],
            },
        }
    )
    assert [step["delay_days"] for step in plan["sequence"]] == [0, 5, 5]


def test_delay_days_falls_back_to_defaults_without_day():
    plan = build_campaign_plan_from_input(
        {
            "workspace_key": "w",
            "campaign_name": "c",
            "parsed_messaging": {
                "subjects": ["S"],
                "steps": [
                    {"step_number": 1, "body_variants": [{"variant_label": "A", "body": "one"}]},
                    {"step_number": 2, "body_variants": [{"variant_label": "A", "body": "two"}]},
                ],
            },
        }
    )
    assert [step["delay_days"] for step in plan["sequence"]] == [0, 3]


def test_build_campaign_plan_from_parsed_repository_input():
    plan = build_campaign_plan_from_input(
        {
            "workspace_key": "smartlead_mcp",
            "template_key": "cold_email_standard_v1",
            "campaign_name": "Darlean Benchmark",
            "max_new_leads_per_day": 75,
            "parsed_messaging": {
                "selected_campaign": "Benchmark",
                "subjects": ["Quick Benchmark", "Program Ops Question"],
                "steps": [
                    {
                        "step_number": 1,
                        "body_variants": [{"variant_label": "A", "body": "Hi {{first_name}}\n%signature%"}],
                    },
                    {
                        "step_number": 2,
                        "body_variants": [{"variant_label": "A", "body": "Bumping this up\n%signature%"}],
                    },
                ],
            },
        }
    )

    assert plan["campaign_name"] == "Darlean Benchmark"
    assert plan["schedule"]["max_new_leads_per_day"] == 75
    assert len(plan["sequence"][0]["variants"]) == 2
    assert plan["sequence"][0]["delay_days"] == 0
    assert plan["sequence"][1]["delay_days"] == 3
    assert plan["sequence"][0]["variants"][0]["subject"] == "Quick Benchmark"
    assert plan["sequence"][1]["variants"][0]["subject"] == ""
    assert "%signature%" in plan["sequence"][0]["variants"][0]["body"]


def test_build_campaign_plan_includes_all_email_steps_and_notes_skipped_empty_steps():
    plan = build_campaign_plan_from_input(
        {
            "workspace_key": "smartlead_mcp",
            "template_key": "cold_email_standard_v1",
            "campaign_name": "Darlean Benchmark",
            "parsed_messaging": {
                "warnings": ["Parser warning."],
                "subjects": ["Quick Benchmark"],
                "steps": [
                    {"step_number": 1, "body_variants": [{"variant_label": "A", "body": "Body 1"}]},
                    {"step_number": 2, "body_variants": []},
                    {"step_number": 3, "body_variants": [{"variant_label": "A", "body": "Body 3"}]},
                    {"step_number": 4, "body_variants": [{"variant_label": "A", "body": "Body 4"}]},
                    {"step_number": 5, "body_variants": [{"variant_label": "A", "body": "Body 5"}]},
                ],
            },
        }
    )

    assert len(plan["sequence"]) == 4
    assert [step["step_number"] for step in plan["sequence"]] == [1, 3, 4, 5]
    assert "Parser warning." in plan["notes_for_operator"]
    assert "Skipped step 2 because no body variants were parsed." in plan["notes_for_operator"]


def test_build_twin_campaign_plan_uses_fixed_sequence():
    raw = {"workspace_key": "darlean", "campaign_name": "Events - Twain", "max_new_leads_per_day": 80}
    plan = build_twin_campaign_plan(raw)
    assert plan["workspace_key"] == "darlean"
    assert plan["campaign_name"] == "Events - Twain"
    assert plan["schedule"]["max_new_leads_per_day"] == 80
    seq = plan["sequence"]
    assert [s["step_number"] for s in seq] == [1, 2]
    assert seq[0]["variants"][0]["subject"] == "{{Subject 1}}"
    assert "{{Step 1}}" in seq[0]["variants"][0]["body"]
    assert "{{Step 3}}" in seq[1]["variants"][0]["body"]


def test_twin_plan_passes_validation():
    raw = {"workspace_key": "darlean", "campaign_name": "Events - Twain", "max_new_leads_per_day": 80}
    plan = build_twin_campaign_plan(raw)
    assert validate_campaign_plan(plan, {"darlean"}) == []


def test_build_plan_includes_linkedin_steps():
    from app.services.local_plan_service import build_campaign_plan_from_input

    parsed = {
        "source_format": "repository",
        "selected_campaign": "Test Campaign",
        "subjects": ["Subject 1"],
        "steps": [
            {
                "step_number": 1,
                "channel": "email",
                "body_variants": [{"variant_label": "A", "body": "Email body one."}],
            },
            {
                "step_number": 2,
                "channel": "linkedin",
                "linkedin_subtype": "connection_request",
                "body_variants": [{"variant_label": "A", "body": "Connect with me!"}],
            },
            {
                "step_number": 3,
                "channel": "linkedin",
                "linkedin_subtype": "dm",
                "body_variants": [{"variant_label": "A", "body": "Thanks for connecting!"}],
            },
        ],
        "campaigns": [],
        "warnings": [],
    }
    plan, errors = build_campaign_plan_from_input(
        parsed_result=parsed,
        workspace_key="preciselead",
        campaign_name="Test Campaign",
    )
    assert not errors
    sequence = plan["sequence"]
    email_steps = [s for s in sequence if s["channel"] == "email"]
    linkedin_steps = [s for s in sequence if s["channel"] == "linkedin"]
    assert len(email_steps) == 1
    assert len(linkedin_steps) == 2
    cr = next(s for s in linkedin_steps if s["linkedin_subtype"] == "connection_request")
    dm = next(s for s in linkedin_steps if s["linkedin_subtype"] == "dm")
    assert cr["variants"][0]["body"] == "Connect with me!"
    assert dm["variants"][0]["body"] == "Thanks for connecting!"


def test_linkedin_dm_delay_days_carries_file_specified_day_gap():
    """A follow-up DM step with 'day' in the parsed file should get delay_days
    relative to the previous LinkedIn DM's day (CR steps don't anchor this)."""
    from app.services.local_plan_service import build_campaign_plan_from_input

    parsed = {
        "source_format": "repository",
        "selected_campaign": "Test Campaign",
        "subjects": ["Subject 1"],
        "steps": [
            {
                "step_number": 1,
                "channel": "email",
                "body_variants": [{"variant_label": "A", "body": "Email body one."}],
            },
            {
                "step_number": 2,
                "channel": "linkedin",
                "linkedin_subtype": "connection_request",
                "day": None,
                "body_variants": [{"variant_label": "A", "body": "Connect!"}],
            },
            {
                "step_number": 3,
                "channel": "linkedin",
                "linkedin_subtype": "dm",
                "day": 0,
                "body_variants": [{"variant_label": "A", "body": "DM one"}],
            },
            {
                "step_number": 4,
                "channel": "linkedin",
                "linkedin_subtype": "dm",
                "day": 4,
                "body_variants": [{"variant_label": "A", "body": "DM two"}],
            },
        ],
        "campaigns": [],
        "warnings": [],
    }
    plan, errors = build_campaign_plan_from_input(
        parsed_result=parsed, workspace_key="preciselead", campaign_name="Test Campaign",
    )
    assert not errors
    linkedin_steps = [s for s in plan["sequence"] if s["channel"] == "linkedin"]
    cr = next(s for s in linkedin_steps if s["linkedin_subtype"] == "connection_request")
    dm1 = next(s for s in linkedin_steps if s["variants"][0]["body"] == "DM one")
    dm2 = next(s for s in linkedin_steps if s["variants"][0]["body"] == "DM two")
    assert cr["delay_days"] == 0
    assert dm1["delay_days"] == 0
    assert dm2["delay_days"] == 4
