from app.services.smartlead_import_service import build_campaign_plan_from_smartlead


def test_build_campaign_plan_from_smartlead_maps_sequences_and_html_body():
    plan = build_campaign_plan_from_smartlead(
        workspace_key="preciselead",
        campaign={
            "id": 3141346,
            "name": "Solo Practitioners Fractional CFO",
            "scheduler_cron_value": {"tz": "America/New_York", "days": [1, 2, 3, 4, 5], "startHour": "9", "endHour": "18"},
            "min_time_btwn_emails": 17,
            "max_leads_per_day": 100,
            "enable_ai_esp_matching": True,
        },
        sequences=[
            {
                "seq_number": 1,
                "seq_delay_details": {"delayInDays": 1},
                "sequence_variants": [
                    {
                        "variant_label": "A",
                        "subject": "Referral Dependence",
                        "email_body": "Hi {{first_name}},<br><br>Want me to hold you a seat?<br><br>%signature%",
                    }
                ],
            }
        ],
    )

    assert plan["campaign_name"] == "Solo Practitioners Fractional CFO"
    assert plan["schedule"]["start_hour"] == "09:00"
    assert plan["schedule"]["end_hour"] == "18:00"
    assert plan["sequence"][0]["variants"][0]["subject"] == "Referral Dependence"
    assert plan["sequence"][0]["variants"][0]["body"] == "Hi {{first_name}},\n\nWant me to hold you a seat?\n\n%signature%"
