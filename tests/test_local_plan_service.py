from app.services.local_plan_service import build_campaign_plan_from_input


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
