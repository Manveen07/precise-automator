from app.services.anthropic_service import _strip_markdown_fence


def test_strip_returns_unchanged_when_no_fence():
    assert _strip_markdown_fence('{"a": 1}') == '{"a": 1}'


def test_strip_handles_json_fence():
    raw = '```json\n{"a": 1}\n```'
    assert _strip_markdown_fence(raw) == '{"a": 1}'


def test_strip_handles_bare_fence():
    raw = '```\n{"a": 1}\n```'
    assert _strip_markdown_fence(raw) == '{"a": 1}'


def test_strip_handles_uppercase_language_tag():
    raw = '```JSON\n{"a": 1}\n```'
    assert _strip_markdown_fence(raw) == '{"a": 1}'


def test_strip_tolerates_leading_and_trailing_whitespace():
    raw = '  \n```json\n{"a": 1}\n```\n  '
    assert _strip_markdown_fence(raw) == '{"a": 1}'


def test_strip_preserves_internal_backticks_in_content():
    raw = '```json\n{"body": "use `code` here"}\n```'
    assert _strip_markdown_fence(raw) == '{"body": "use `code` here"}'


def test_strip_handles_empty_input():
    assert _strip_markdown_fence("") == ""
    assert _strip_markdown_fence("   ") == ""


def test_strip_handles_single_line_with_no_newline_in_fence():
    # Edge case: fence opener but no body — we just return what's there.
    assert _strip_markdown_fence("```json") == "```json"
