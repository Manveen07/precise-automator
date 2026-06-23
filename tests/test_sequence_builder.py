from app.services.sequence_builder import (
    build_smartlead_sequences,
    format_email_body_for_smartlead,
    format_subject_for_smartlead,
    smartlead_html_to_text,
)


def test_format_email_body_repairs_glued_sentences():
    """Soft-return exports glue sentences: 'care.That'. Insert the missing space."""
    body = "more complex care.That became the center of our work."
    formatted = format_email_body_for_smartlead(body)
    assert "care. That" in formatted
    assert "care.That" not in formatted


def test_format_email_body_does_not_split_abbreviations_decimals_or_domains():
    """Only split lowercase.Uppercase; leave decimals, abbreviations, domains alone."""
    body = "We grew 3.5x. Sales in the U.S.A grew. See site.com today."
    formatted = format_email_body_for_smartlead(body)
    assert "3.5x" in formatted        # decimal untouched
    assert "U.S.A" in formatted       # uppercase abbreviation not split
    assert "site.com" in formatted    # domain untouched


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

    assert formatted == "Hi {{first_name}},<br><br>Line two &nbsp;&nbsp;&nbsp;aligned<br><br>%signature%"
    assert "\ufeff" not in formatted
    assert "\u00a0" not in formatted
    assert "\u2028" not in formatted


def test_format_subject_for_smartlead_flattens_whitespace():
    subject = "\ufeffNew\u00a0Movers\nAcquisition\tTest"

    assert format_subject_for_smartlead(subject) == "New Movers Acquisition Test"


def test_smartlead_html_to_text_handles_paragraph_tags_and_nbsp():
    html = "<p>Hi&nbsp;{{first_name}},</p><div>Line&nbsp;&nbsp;two</div><p>%Signature%</p>"

    assert smartlead_html_to_text(html) == "Hi {{first_name}},\nLine  two\n%signature%"


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


def test_format_email_body_expands_google_docs_flat_export():
    body = (
        "Hi {{First_Name}}\n"
        "\n"
        "Most environmental firms hit the same wall once they juggle 30+ projects.\n"
        "Field teams, labs, and report writers all work in separate systems.\n"
        "Darlean helps firms track projects without spreadsheets or emails.\n"
        "Worth exploring?\n"
        "%Signature%"
    )
    formatted = format_email_body_for_smartlead(body)

    assert "Hi {{First_Name}}<br><br>Most environmental firms" in formatted
    assert "30+ projects.<br><br>Field teams" in formatted
    assert "separate systems.<br><br>Darlean helps" in formatted
    assert "Worth exploring?<br><br>%signature%" in formatted
    assert "projects.<br>Field" not in formatted


def test_format_email_body_does_not_double_space_pre_spaced_source():
    body = (
        "{Hi|Hey} {{first_name}},\n"
        "\n"
        "{Have you tested|Ever tried} shared mail?\n"
        "\n"
        "{Let me know|Happy to share} more.\n"
        "\n"
        "%signature%"
    )
    formatted = format_email_body_for_smartlead(body)

    assert "<br><br><br>" not in formatted
    assert "{first_name}},<br><br>{Have you tested" in formatted
    assert "shared mail?<br><br>{Let me know" in formatted


def test_format_email_body_keeps_list_items_tight():
    body = (
        "Here is what we offer:\n"
        "- Free setup\n"
        "- $1/user for 6 months\n"
        "- Migration support\n"
        "\n"
        "Worth a chat?\n"
        "%signature%"
    )
    formatted = format_email_body_for_smartlead(body)

    assert "we offer:<br>- Free setup" in formatted
    assert "Free setup<br>- $1/user" in formatted
    assert "$1/user for 6 months<br>- Migration support" in formatted
    assert "Migration support<br><br>Worth a chat?" in formatted
    assert "Free setup<br><br>" not in formatted


def test_format_email_body_handles_numbered_lists():
    body = (
        "Three steps:\n"
        "1. Sign up\n"
        "2. Connect inbox\n"
        "3. Launch campaign\n"
        "\n"
        "Done."
    )
    formatted = format_email_body_for_smartlead(body)

    assert "Three steps:<br>1. Sign up" in formatted
    assert "1. Sign up<br>2. Connect inbox" in formatted
    assert "3. Launch campaign<br><br>Done." in formatted


def test_build_sequences_uses_manual_percentage_when_distribution_set():
    plan = [{"step_number": 1, "delay_days": 0, "variants": [
        {"variant_label": "A", "subject": "S1", "body": "b1", "distribution_percentage": 70},
        {"variant_label": "B", "subject": "S2", "body": "b2", "distribution_percentage": 30},
    ]}]
    seqs = build_smartlead_sequences(plan)
    assert seqs[0]["variant_distribution_type"] == "MANUAL_PERCENTAGE"
    pcts = {v["variant_label"]: v["variant_distribution_percentage"] for v in seqs[0]["seq_variants"]}
    assert pcts == {"A": 70, "B": 30}


def test_build_sequences_defaults_to_manual_equal():
    plan = [{"step_number": 1, "delay_days": 0, "variants": [
        {"variant_label": "A", "subject": "S1", "body": "b1"},
        {"variant_label": "B", "subject": "S2", "body": "b2"},
    ]}]
    seqs = build_smartlead_sequences(plan)
    assert seqs[0]["variant_distribution_type"] == "MANUAL_EQUAL"
    assert "variant_distribution_percentage" not in seqs[0]["seq_variants"][0]


def test_build_sequences_ignores_partial_or_invalid_distribution():
    # Percentages that don't sum to 100 fall back to equal split (safe default).
    plan = [{"step_number": 1, "delay_days": 0, "variants": [
        {"variant_label": "A", "subject": "S1", "body": "b1", "distribution_percentage": 70},
        {"variant_label": "B", "subject": "S2", "body": "b2", "distribution_percentage": 10},
    ]}]
    seqs = build_smartlead_sequences(plan)
    assert seqs[0]["variant_distribution_type"] == "MANUAL_EQUAL"
