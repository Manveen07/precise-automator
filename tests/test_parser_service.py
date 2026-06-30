from app.services.parser_service import extract_subjects, parse_messaging_file


def test_extract_subjects_from_numbered_lines():
    text = "1. quick thought for {{company_name}}\n2. idea for {{company}}\n"
    assert extract_subjects(text) == ["quick thought for {{company_name}}", "idea for {{company}}"]


def test_parse_email_sections_without_subject_heading():
    text = """
1. Quick test

Email 1
V1
Hi {{first_name}},
1. This numbered body line is not a subject.
%Signature%

Email 2
Bumping this up.
"""
    parsed = parse_messaging_file(text)

    assert parsed["source_format"] == "email_sections"
    assert parsed["subjects"] == ["Quick test"]
    assert [step["step_number"] for step in parsed["steps"]] == [1, 2]
    assert parsed["steps"][0]["body_variants"][0]["body"].startswith("Hi {{first_name}}")
    assert parsed["steps"][1]["body_variants"][0]["body"] == "Bumping this up."


def test_parse_email_and_version_hash_headings():
    text = """
Round Table Offer

Audience: CEO/Partners/Owners from:
1. Chemicals -> Chemicals
2. Logistics -> Logistics

Subject Line Options :
1. Panel Discussion Invitation

Email#1

Version#1
Plain copy ignored.

Spintax
Hi {{first_name}},
Version one body.
%Signature%

Version#2
Plain copy ignored.

Spintax
Hi {{first_name}},
Version two body.
%Signature%

Email#2

Version#1
Spintax
Bumping this up.
%Signature%
"""
    parsed = parse_messaging_file(text)

    assert parsed["source_format"] == "repository"
    assert parsed["selected_campaign"] == "Round Table Offer"
    assert parsed["subjects"] == ["Panel Discussion Invitation"]
    assert [step["step_number"] for step in parsed["steps"]] == [1, 2]
    assert len(parsed["steps"][0]["body_variants"]) == 2
    assert "Version one body" in parsed["steps"][0]["body_variants"][0]["body"]
    assert "Version two body" in parsed["steps"][0]["body_variants"][1]["body"]
    assert parsed["steps"][1]["body_variants"][0]["body"].startswith("Bumping this up.")


def test_parse_steps_variants_and_spintax_copy():
    text = """
1. subject one
Step 1
V1
Plain copy ignored.
Spintax
Hi {{first_name}}, {open|quick} note.
%Signature%
V2
Spintax
Different body.
Step 2
Spintax
Bumping this up.
"""
    parsed = parse_messaging_file(text)
    assert parsed["subjects"] == ["subject one"]
    assert parsed["steps"][0]["step_number"] == 1
    assert parsed["steps"][0]["body_variants"][0]["body"].startswith("Hi {{first_name}}")
    assert parsed["steps"][0]["body_variants"][1]["body"] == "Different body."
    assert parsed["steps"][1]["body_variants"][0]["body"] == "Bumping this up."
    assert parsed["warnings"] == []


def test_parse_repository_format_scopes_subjects_and_email_steps():
    text = """
Non Profits Redraft

Benchmark

Subject Line Options:
1. Quick Benchmark
2. Program Ops Question

Email 1
Hi {{first_name}},
1. This numbered body line is not a subject.
%signature%

--- SPINTAX VERSION ---

{Hi|Hey} {{first_name}},
Would love your take.
%signature%

Email 2
Hi {{first_name}},
Plain follow up.

--- SPINTAX VERSION ---

Bumping this up.
%signature%

Micro-Poll

Subject Line
1. Quick Poll

Email 1
--- SPINTAX VERSION ---
One question.
"""
    parsed = parse_messaging_file(text)
    assert parsed["source_format"] == "repository"
    assert parsed["selected_campaign"] == "Benchmark"
    assert parsed["subjects"] == ["Quick Benchmark", "Program Ops Question"]
    assert len(parsed["campaigns"]) == 2
    assert parsed["steps"][0]["body_variants"][0]["body"].startswith("{Hi|Hey}")
    assert "Micro-Poll" not in parsed["steps"][1]["body_variants"][0]["body"]
    assert parsed["warnings"] == []


def test_parse_repository_with_day_suffixes_version_letters_and_linkedin_blocks():
    text = """
Insurance Vertical
Email 1 (Day-0)
Subject Lines
1. New Movers
2. Shared Mail

Version A (Sam's Original)
Plain copy ignored.

--- SPINTAX VERSION ---

{Hi|Hey} {{first_name}},

Email one A.
%signature%

Unique combinations: 2 x 1 = 2

Version B (Suggested Copy)

--- SPINTAX VERSION ---

{Hi|Hey} {{first_name}},

Email one B.
%signature%

Unique combinations: 2 x 1 = 2

LinkedIn : Connection Request (Day-2)
This should not become email copy.

Email 2 : Follow-Up (Day-4)

--- SPINTAX VERSION ---

Email two.
%signature%

Email 3 : Deadline Nudge (Day-7)

--- SPINTAX VERSION ---

Email three.
%signature%

LinkedIn : DM1 (1 week after connection)
This should also be ignored.
"""
    parsed = parse_messaging_file(text)

    assert parsed["source_format"] == "repository"
    assert parsed["selected_campaign"] == "Insurance Vertical"
    assert parsed["subjects"] == ["New Movers", "Shared Mail"]
    assert [step["step_number"] for step in parsed["steps"]] == [1, 2, 3]
    assert len(parsed["steps"][0]["body_variants"]) == 2
    bodies = [
        variant["body"]
        for step in parsed["steps"]
        for variant in step["body_variants"]
    ]
    assert any("Email one A." in body for body in bodies)
    assert any("Email one B." in body for body in bodies)
    assert all("LinkedIn" not in body for body in bodies)
    assert all("Unique combinations" not in body for body in bodies)


def test_parse_step_prefixed_mixed_channel_keeps_email_skips_linkedin():
    """Google-Docs 'Step N — Email/LinkedIn' format with interleaved channels.

    Email steps are isolated and renumbered sequentially (1..N) for Smartlead.
    LinkedIn steps are now emitted (not skipped) with channel='linkedin' and
    their original step number so ordering is preserved.
    """
    text = """Health Systems Campaign: 5-Step Sequence

* Target: Hospital systems
* Email Sender: Scott

Step 1 — Email (Day 0)

Subject Line Options:

1. Cone Health Perspective
2. Complex Care Perception
3. Brand Preference Gap
4. Regional Care Choice

Message Body:

Hi {{first_name}},

A few years ago, Cone Health faced a problem.
%signature%

Step 2 — LinkedIn (Connection Request - Optional) (Day 0)

{{first_name}}, I led the strategy work behind Cone Health.

Step 3 — Email #2 (Day 3)

{{first_name}},

One pattern we saw with Cone Health is the media mix.
%signature%

Step 4 — LinkedIn DM #1 (3 Hours after Connection)

Hey {{first_name}}, wanted to share a thought.

Step 5 — LinkedIn DM #2 (Final Touch)

The media audit is one lens.
"""
    parsed = parse_messaging_file(text)

    assert parsed["selected_campaign"] == "Health Systems Campaign: 5-Step Sequence"
    assert parsed["subjects"] == [
        "Cone Health Perspective",
        "Complex Care Perception",
        "Brand Preference Gap",
        "Regional Care Choice",
    ]

    # Email steps are renumbered sequentially; LinkedIn steps keep original numbers.
    email_steps = [s for s in parsed["steps"] if s.get("channel", "email") == "email"]
    linkedin_steps = [s for s in parsed["steps"] if s.get("channel") == "linkedin"]
    assert [s["step_number"] for s in email_steps] == [1, 2]
    assert [s["step_number"] for s in linkedin_steps] == [2, 4, 5]

    body_one = email_steps[0]["body_variants"][0]["body"]
    body_two = email_steps[1]["body_variants"][0]["body"]
    assert body_one.startswith("Hi {{first_name}},")
    assert "Message Body" not in body_one
    assert "Cone Health Perspective" not in body_one  # subject lines stripped from body
    assert body_two.startswith("{{first_name}},")
    assert "media mix" in body_two

    # No LinkedIn copy leaked into any email body.
    email_bodies = [v["body"] for s in email_steps for v in s["body_variants"]]
    assert all("LinkedIn" not in b for b in email_bodies)
    assert all("media audit is one lens" not in b for b in email_bodies)

    # LinkedIn steps are emitted, not skipped — no "Skipped" warnings.
    assert not any("Skipped" in w for w in parsed.get("warnings", []))


def test_doc_comment_markers_and_definitions_are_stripped():
    """Google-Docs comment anchors [a]/[b] and the footnote dump must not reach copy."""
    text = """Health Campaign[a]

Step 1 — Email (Day 0)

Subject Line Options:
1. Subject One

Message Body:

Hi {{first_name}},
No access needed.[b]
%signature%

Step 2 — Email (Day 3)

{{first_name}},
Second email.[c]
%signature%

[a]@ken@x.com reviewed, no red flags.
CC: @boss@x.com
_Assigned to ken@x.com_
[b]Flag: reads like we built it for them.
[c]Hope this version works.
"""
    parsed = parse_messaging_file(text)
    bodies = " ".join(v["body"] for s in parsed["steps"] for v in s["body_variants"])

    assert "[a]" not in bodies and "[b]" not in bodies and "[c]" not in bodies
    assert "Flag:" not in bodies          # footnote-definition prose gone
    assert "Assigned to" not in bodies
    assert "@ken@x.com" not in bodies
    assert "No access needed." in bodies  # surrounding copy intact
    assert "Second email." in bodies
    assert parsed["selected_campaign"] == "Health Campaign"  # trailing [a] stripped from title


def test_step_channel_format_captures_day_offsets():
    text = """Campaign Title

Step 1 — Email (Day 0)
Subject Line Options:
1. Subject One

Message Body:
Hi {{first_name}}, first.
%signature%

Step 2 — Email (Day 5)
{{first_name}}, second.
%signature%

Step 3 — Email (Day 10)
{{first_name}}, third.
%signature%
"""
    parsed = parse_messaging_file(text)
    assert [step.get("day") for step in parsed["steps"]] == [0, 5, 10]


def test_repository_sequence_name_miss_warns_before_falling_back_to_first():
    text = """
Benchmark

Subject Line Options:
1. Quick Benchmark

Email 1
Body one.

Micro-Poll

Subject Line Options:
1. Quick Poll

Email 1
Body two.
"""
    parsed = parse_messaging_file(text, selected_sequence_name="Benchmark v3")

    assert parsed["selected_campaign"] == "Benchmark"
    assert parsed["warnings"] == [
        "Requested sequence 'Benchmark v3' was not found; using first parsed sequence 'Benchmark'."
    ]


def test_empty_spintax_block_produces_warning_not_silent_drop():
    """
    Email 2 has a Spintax marker but nothing under it.
    Before the fix: Email 2 was silently dropped, producing a 1-step plan.
    After the fix: Email 2 is kept with empty body_variants and a warning is emitted.
    """
    text = """
Benchmark

Subject Line Options:
1. Quick Benchmark

Email 1
--- SPINTAX VERSION ---
{Hi|Hey} {{first_name}}, body one.
%Signature%

Email 2
--- SPINTAX VERSION ---

"""
    parsed = parse_messaging_file(text)

    # Two steps present — Email 2 is NOT dropped
    assert len(parsed["steps"]) == 2
    assert parsed["steps"][1]["step_number"] == 2
    assert parsed["steps"][1]["body_variants"] == []

    # Warning is emitted pointing at the problem step
    assert len(parsed["warnings"]) == 1
    assert "Email 2" in parsed["warnings"][0]
    assert "Spintax" in parsed["warnings"][0]


def test_missing_spintax_marker_uses_full_body():
    """
    If there's no Spintax marker at all, the whole block is the body.
    This should produce no warnings.
    """
    text = """
Benchmark

Subject Line Options:
1. Quick Benchmark

Email 1
{Hi|Hey} {{first_name}}, no spintax marker here.
%Signature%
"""
    parsed = parse_messaging_file(text)
    assert len(parsed["steps"]) == 1
    assert parsed["steps"][0]["body_variants"][0]["body"].startswith("{Hi|Hey}")
    assert parsed["warnings"] == []


def test_empty_step_warning_includes_step_number():
    """Warning text must reference the correct Email number."""
    text = """
Benchmark

Subject Line Options:
1. Quick Benchmark

Email 1
--- SPINTAX VERSION ---
Body one.

Email 2
--- SPINTAX VERSION ---

Email 3
--- SPINTAX VERSION ---
Body three.
"""
    parsed = parse_messaging_file(text)

    step_numbers = [s["step_number"] for s in parsed["steps"]]
    assert step_numbers == [1, 2, 3]

    # Only Email 2 is empty
    assert len(parsed["warnings"]) == 1
    assert "Email 2" in parsed["warnings"][0]

    # Email 1 and 3 have content
    assert parsed["steps"][0]["body_variants"] != []
    assert parsed["steps"][2]["body_variants"] != []


def test_parse_step_channel_linkedin_connection_request():
    text = """Campaign Title

Step 1 — Email (Day 0)
Hey {{first_name}},

Step 2 — LinkedIn (Connection Request - Optional) (Day 0)
Hi {{first_name}}, I'd love to connect!

Step 3 — LinkedIn DM#1 (3 Hours after Connection)
Thanks for connecting {{first_name}}!
"""
    result = parse_messaging_file(text)
    steps = result["steps"]
    email_steps = [s for s in steps if s.get("channel", "email") == "email"]
    linkedin_steps = [s for s in steps if s.get("channel") == "linkedin"]
    assert len(email_steps) == 1
    assert email_steps[0]["step_number"] == 1
    assert len(linkedin_steps) == 2
    cr_step = next(s for s in linkedin_steps if s["linkedin_subtype"] == "connection_request")
    dm_step = next(s for s in linkedin_steps if s["linkedin_subtype"] == "dm")
    assert "connect" in cr_step["body_variants"][0]["body"].lower()
    assert "thanks" in dm_step["body_variants"][0]["body"].lower()


def test_parse_step_channel_linkedin_produces_no_warning():
    text = """Title

Step 1 — Email (Day 0)
Body.

Step 2 — LinkedIn DM#1 (Day 3)
DM body.
"""
    result = parse_messaging_file(text)
    assert not any("Skipped" in w for w in result.get("warnings", []))


def test_parse_step_channel_email_step_numbering_unaffected():
    """Email steps still get sequential numbers 1..N for Smartlead."""
    text = """Title

Step 1 — LinkedIn (Connection Request) (Day 0)
Connect note.

Step 2 — Email (Day 1)
First email body.

Step 3 — LinkedIn DM#1 (Day 3)
Follow-up DM.

Step 4 — Email (Day 5)
Second email body.
"""
    result = parse_messaging_file(text)
    email_steps = [s for s in result["steps"] if s.get("channel", "email") == "email"]
    assert [s["step_number"] for s in email_steps] == [1, 2]
