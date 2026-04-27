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
