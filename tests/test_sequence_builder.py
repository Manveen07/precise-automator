from app.services.sequence_builder import build_smartlead_sequences, format_email_body_for_smartlead, format_subject_for_smartlead


def test_format_email_body_for_smartlead_preserves_tokens():
    body = "Hi {{first_name}},\n\nLine two {a|b}\n%Signature%"
    formatted = format_email_body_for_smartlead(body)
    assert "{{first_name}}" in formatted
    assert "{a|b}" in formatted
    assert "Line two {a|b}<br><br>%signature%" in formatted
    assert "\n" not in formatted


def test_format_email_body_for_smartlead_preserves_visible_text_spacing_and_literals():
    body = "Hi {{first_name}},\n\n1.  <>\n\n  indented  words"
    formatted = format_email_body_for_smartlead(body)

    assert "1. &nbsp;&lt;&gt;" in formatted
    assert "<br><br>&nbsp;&nbsp;indented &nbsp;words" in formatted


def test_format_email_body_for_smartlead_collapses_extra_blank_lines_and_strips_trailing_spaces():
    body = "Hi {{first_name}},   \n\n\n\nParagraph two.  \n%Signature%\n\n"
    formatted = format_email_body_for_smartlead(body)

    assert "Hi {{first_name}},<br><br>Paragraph two.<br><br>%signature%" == formatted
    assert "<br><br><br>" not in formatted
    assert "&nbsp;" not in formatted


def test_format_email_body_for_smartlead_cleans_google_docs_unicode_whitespace():
    body = "\ufeffHi\u00a0{{first_name}},\u2028Line two\taligned\u2029%Signature%"
    formatted = format_email_body_for_smartlead(body)

    assert formatted == "Hi {{first_name}},<br>Line two &nbsp;&nbsp;&nbsp;aligned<br><br>%signature%"
    assert "\ufeff" not in formatted
    assert "\u00a0" not in formatted
    assert "\u2028" not in formatted


def test_format_subject_for_smartlead_flattens_whitespace():
    subject = "\ufeffNew\u00a0Movers\nAcquisition\tTest"

    assert format_subject_for_smartlead(subject) == "New Movers Acquisition Test"


def test_build_smartlead_sequences_uses_seq_variants_and_blank_followup_subjects():
    sequences = build_smartlead_sequences(
        [
            {
                "step_number": 1,
                "delay_days": 9,
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
    assert sequences[0]["seq_delay_details"]["delay_in_days"] == 0
    assert sequences[1]["seq_delay_details"]["delay_in_days"] == 3
    assert sequences[0]["seq_variants"][0]["subject"] == "hello"
    assert sequences[1]["seq_variants"][0]["subject"] == ""
