from app.services.sequence_builder import build_smartlead_sequences, format_email_body_for_smartlead


def test_format_email_body_for_smartlead_preserves_tokens():
    body = "Hi {{first_name}},\n\nLine two {a|b}\n%Signature%"
    formatted = format_email_body_for_smartlead(body)
    assert "{{first_name}}" in formatted
    assert "{a|b}" in formatted
    assert "<br><br>%Signature%" in formatted
    assert "\n" not in formatted


def test_build_smartlead_sequences_uses_seq_variants_and_blank_followup_subjects():
    sequences = build_smartlead_sequences(
        [
            {
                "step_number": 1,
                "delay_days": 1,
                "variants": [{"variant_label": "A", "subject": "hello", "body": "Body"}],
            },
            {
                "step_number": 2,
                "delay_days": 3,
                "variants": [{"variant_label": "A", "subject": "must be removed", "body": "Follow up"}],
            },
        ]
    )
    assert "seq_variants" in sequences[0]
    assert sequences[0]["seq_variants"][0]["subject"] == "hello"
    assert sequences[1]["seq_variants"][0]["subject"] == ""
