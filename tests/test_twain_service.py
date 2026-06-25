"""Tests for app.services.twain_service.

The central contract this file locks: the normalizer and the audit AGREE —
audit_twain_field(normalize_twain_field(x)) == [] for every input.
"""

import pytest

from app.services.twain_service import (
    audit_twain_field,
    flag_greeting_issues,
    normalize_twain_field,
    twain_sequence_plan,
)

DIRTY_BODIES = [
    "A<br>B",
    "A<br><br><br>B",
    "A<br><br><br><br>B",
    "A <br> B",
    "A\t<br>\tB",
    "A<br/>B",
    "A<br />B",
    "A<BR>B",
    "A\nB",
    "A\n\n\nB",
    "A  \nB",
    "Para1<br>Para2<br><br>Para3<br>Para4",
    " A<br>B​",
    "<br>A<br><br>B<br>",
]

CLEAN_BODIES = [
    "A<br><br>B",
    "A<br><br>B<br><br>C",
    "Single paragraph, no breaks.",
    "Hi Mark,<br><br>That close had moving parts.<br><br>Worth a look?",
]


@pytest.mark.parametrize("dirty", DIRTY_BODIES)
def test_normalize_produces_clean_field(dirty):
    assert audit_twain_field(normalize_twain_field(dirty)) == []


@pytest.mark.parametrize("clean", CLEAN_BODIES)
def test_normalize_leaves_clean_fields_clean(clean):
    assert audit_twain_field(normalize_twain_field(clean)) == []


def test_lone_br_promoted_to_double():
    assert normalize_twain_field("A<br>B") == "A<br><br>B"


def test_triple_br_collapsed():
    assert normalize_twain_field("A<br><br><br>B") == "A<br><br>B"


def test_spaces_around_br_stripped():
    assert normalize_twain_field("A <br> B") == "A<br><br>B"


def test_self_closing_br_variants_normalized():
    assert normalize_twain_field("A<br/>B") == "A<br><br>B"
    assert normalize_twain_field("A<br />B") == "A<br><br>B"


def test_existing_double_br_left_intact():
    assert normalize_twain_field("A<br><br>B") == "A<br><br>B"


def test_raw_newline_fallback():
    assert normalize_twain_field("A\nB") == "A\n\nB"
    assert normalize_twain_field("A\n\n\nB") == "A\n\nB"
    assert normalize_twain_field("A  \nB") == "A\n\nB"


def test_unicode_and_bom_cleaned():
    out = normalize_twain_field("﻿A<br>B​")
    assert "﻿" not in out and "​" not in out


def test_leading_and_trailing_breaks_stripped():
    assert normalize_twain_field("<br>A<br><br>B<br>") == "A<br><br>B"


def test_wording_never_changed():
    body = "Running 2,000+ events, you feel coordination friction.<br>How are you?"
    out = normalize_twain_field(body)
    for token in ["Running 2,000+ events", "coordination friction", "How are you?"]:
        assert token in out


@pytest.mark.parametrize("case", DIRTY_BODIES + CLEAN_BODIES)
def test_idempotent(case):
    once = normalize_twain_field(case)
    assert normalize_twain_field(once) == once


def test_empty_and_none_safe():
    assert normalize_twain_field("") == ""
    assert normalize_twain_field("   ") == "   "
    assert normalize_twain_field(None) is None  # type: ignore[arg-type]


def test_subject_strips_br_and_flattens():
    assert normalize_twain_field("Event<br>coordination", is_subject=True) == "Event coordination"


def test_subject_collapses_whitespace():
    assert normalize_twain_field("  Event   coordination  ", is_subject=True) == "Event coordination"


def test_audit_detects_each_defect_class():
    assert "lone_br" in audit_twain_field("A<br>B")
    assert "triple_br" in audit_twain_field("A<br><br><br>B")
    assert "space_before_br" in audit_twain_field("A <br>B")
    assert "lone_nl" in audit_twain_field("A\nB")
    assert "triple_nl" in audit_twain_field("A\n\n\nB")
    assert "trailing_space_nl" in audit_twain_field("A \nB")


def test_audit_clean_field_returns_empty():
    assert audit_twain_field("A<br><br>B") == []


def test_audit_empty_returns_empty():
    assert audit_twain_field("") == []


def test_step1_with_greeting_flagged():
    assert "step1_has_greeting" in flag_greeting_issues("Hi Mark,<br><br>Body", "Hi Mark,<br><br>Body")


def test_step3_missing_greeting_flagged():
    assert "step3_missing_greeting" in flag_greeting_issues("Body only", "No greeting here")


def test_clean_greetings_no_flags():
    flags = flag_greeting_issues("You have a lot happening...", "Hi Mark,<br><br>Follow up")
    assert flags == []


def test_tight_greeting_spacing_not_flagged_as_content():
    assert "step3_missing_greeting" not in flag_greeting_issues(None, "Hi Mark,<br>Body")


def test_twain_sequence_plan_shape():
    plan = twain_sequence_plan()
    assert len(plan) == 2
    assert plan[0]["step_number"] == 1 and plan[0]["delay_days"] == 0
    assert plan[1]["step_number"] == 2 and plan[1]["delay_days"] == 3
    assert plan[0]["variants"][0]["subject"] == "{{Subject 1}}"
    assert plan[1]["variants"][0]["subject"] == ""
    assert "{{Step 1}}" in plan[0]["variants"][0]["body"]
    assert "{{Step 3}}" in plan[1]["variants"][0]["body"]
    assert "%signature%" in plan[0]["variants"][0]["body"]


def test_twain_sequence_plan_custom_delay():
    plan = twain_sequence_plan(followup_delay_days=5)
    assert plan[1]["delay_days"] == 5


def test_twain_sequence_plan_bodies_emit_double_br_after_formatter():
    from app.services.sequence_builder import format_email_body_for_smartlead
    plan = twain_sequence_plan()
    step1 = format_email_body_for_smartlead(plan[0]["variants"][0]["body"])
    assert "Hi {{first_name}},<br><br>{{Step 1}}<br><br>%signature%" == step1
    assert audit_twain_field(step1) == []
