from unittest.mock import MagicMock, patch

from app.services.heyreach_fallback_ai import clean_fallbacks_in_sequence, looks_broken


def test_looks_broken_detects_double_space():
    assert looks_broken("Hey there,  I've spent a lot of time.")


def test_looks_broken_detects_leading_punctuation():
    assert looks_broken(", I've spent a lot of time.")


def test_looks_broken_false_for_clean_text():
    assert not looks_broken("Hey there, I've spent a lot of time in regulated categories.")


def test_looks_broken_false_for_empty_string():
    assert not looks_broken("")


def test_clean_fallbacks_skips_healthy_fallback_no_api_call():
    sequence = {
        "nodeType": "MESSAGE",
        "payload": {"messages": ["Hi {FIRST_NAME}"], "fallbackMessage": "Hi there, quick idea for you."},
    }
    with patch("app.services.heyreach_fallback_ai.rewrite_fallback") as mock_rewrite:
        cleaned, calls = clean_fallbacks_in_sequence(sequence)
    mock_rewrite.assert_not_called()
    assert calls == 0
    assert cleaned["payload"]["fallbackMessage"] == "Hi there, quick idea for you."


def test_clean_fallbacks_rewrites_broken_fallback():
    sequence = {
        "nodeType": "MESSAGE",
        "payload": {"messages": ["x"], "fallbackMessage": "there,  I've spent a lot of time."},
        "conditionalNode": {"nodeType": "END", "actionDelay": 0},
    }
    with patch("app.services.heyreach_fallback_ai.rewrite_fallback", return_value="Hey there, I've spent a lot of time.") as mock_rewrite:
        cleaned, calls = clean_fallbacks_in_sequence(sequence)
    mock_rewrite.assert_called_once()
    assert calls == 1
    assert cleaned["payload"]["fallbackMessage"] == "Hey there, I've spent a lot of time."


def test_clean_fallbacks_dedupes_identical_broken_text():
    """Same broken fallback appearing twice in a sequence (e.g. already-connected
    and CR-accepted branches share the same message) should only trigger one API call."""
    broken = "there,  I've spent a lot of time."
    sequence = {
        "nodeType": "CHECK_IS_CONNECTION",
        "conditionalNode": {
            "nodeType": "MESSAGE",
            "payload": {"messages": ["x"], "fallbackMessage": broken},
        },
        "unconditionalNode": {
            "nodeType": "CONNECTION_REQUEST",
            "payload": {"messages": ["y"], "fallbackMessage": broken},
        },
    }
    with patch("app.services.heyreach_fallback_ai.rewrite_fallback", return_value="fixed") as mock_rewrite:
        cleaned, calls = clean_fallbacks_in_sequence(sequence)
    mock_rewrite.assert_called_once()
    assert calls == 1
    assert cleaned["conditionalNode"]["payload"]["fallbackMessage"] == "fixed"
    assert cleaned["unconditionalNode"]["payload"]["fallbackMessage"] == "fixed"


def test_rewrite_fallback_uses_haiku_model():
    from app.services.heyreach_fallback_ai import rewrite_fallback, _HAIKU_MODEL

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="cleaned text")]
    with patch("app.services.heyreach_fallback_ai.Anthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response
        mock_anthropic_cls.return_value = mock_client

        result = rewrite_fallback("broken,  fallback")

    assert result == "cleaned text"
    call_kwargs = mock_client.messages.create.call_args[1]
    assert call_kwargs["model"] == _HAIKU_MODEL
    assert call_kwargs["max_tokens"] <= 300
