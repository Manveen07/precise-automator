from app.services.parser_service import extract_subjects, parse_messaging_file


def test_extract_subjects_from_numbered_lines():
    text = "1. quick thought for {{company_name}}\n2. idea for {{company}}\n"
    assert extract_subjects(text) == ["quick thought for {{company_name}}", "idea for {{company}}"]


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
