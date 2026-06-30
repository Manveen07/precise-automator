from app.schemas.campaign_plan import SequenceStep, linkedin_messages


def test_step_defaults_to_email_channel():
    step = SequenceStep(step_number=1, delay_days=0, variants=[{"body": "x"}])
    assert step.channel == "email"


def test_step_accepts_linkedin_channel():
    step = SequenceStep(step_number=1, delay_days=0, channel="linkedin", variants=[{"body": "hi"}])
    assert step.channel == "linkedin"


def test_linkedin_messages_extracts_ordered_bodies():
    plan = {
        "sequence": [
            {"step_number": 2, "delay_days": 0, "channel": "linkedin", "variants": [{"body": "second"}]},
            {"step_number": 1, "delay_days": 0, "channel": "linkedin", "variants": [{"body": "first"}]},
            {"step_number": 3, "delay_days": 0, "channel": "email", "variants": [{"body": "email body"}]},
        ]
    }
    assert linkedin_messages(plan) == ["first", "second"]


def test_linkedin_messages_empty_when_none():
    plan = {"sequence": [{"step_number": 1, "delay_days": 0, "variants": [{"body": "x"}]}]}
    assert linkedin_messages(plan) == []
