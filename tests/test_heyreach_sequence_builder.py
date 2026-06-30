import pytest

from app.services.heyreach_sequence_builder import (
    build_linkedin_sequence,
    to_heyreach_message,
)


def _collect_node_types(node, acc):
    if not isinstance(node, dict):
        return
    acc.append(node.get("nodeType"))
    for key in ("conditionalNode", "unconditionalNode"):
        if node.get(key):
            _collect_node_types(node[key], acc)


def _all_leaves_are_end(node):
    """Every path must terminate in an END node."""
    if not isinstance(node, dict):
        return True
    cond, uncond = node.get("conditionalNode"), node.get("unconditionalNode")
    if cond is None and uncond is None:
        return node.get("nodeType") == "END"
    return all(_all_leaves_are_end(c) for c in (cond, uncond) if c is not None)


def _delays_valid(node):
    """Post-action nodes need delay >= 3h or any DAY value; 0 only allowed on
    the first CHECK_IS_CONNECTION and on MESSAGE reply-exit END nodes."""
    def walk(n, is_root):
        if not isinstance(n, dict):
            return True
        delay = n.get("actionDelay", 0)
        unit = n.get("actionDelayUnit", "HOUR")
        zero_ok = is_root or n.get("nodeType") == "END"  # reply-exit ENDs use 0
        if not zero_ok and unit == "HOUR" and delay < 3:
            return False
        return all(walk(n.get(k), False) for k in ("conditionalNode", "unconditionalNode") if n.get(k))
    return walk(node, True)


def test_to_heyreach_message_translates_tags_and_builds_fallback():
    msg, fb = to_heyreach_message("Hi {{first_name}} at {{company}} — quick idea.%signature%")
    assert msg == "Hi {FIRST_NAME} at {COMPANY} — quick idea."
    assert fb == "Hi there at your company — quick idea."


def test_to_heyreach_message_handles_company_name_alias():
    msg, fb = to_heyreach_message("For {{company_name}}.")
    assert msg == "For {COMPANY}."
    assert fb == "For your company."


def test_to_heyreach_message_strips_space_before_punctuation_in_fallback():
    # Source file has "{{first_name}} ," — fallback must not render "there ,"
    msg, fb = to_heyreach_message("{{first_name}} , glad to connect.")
    assert msg == "{FIRST_NAME} , glad to connect."
    assert fb == "there, glad to connect."


def test_single_message_sequence_shape():
    seq = build_linkedin_sequence(["Hi {{first_name}}"])
    assert seq["nodeType"] == "CHECK_IS_CONNECTION"
    assert seq["conditionalNode"]["nodeType"] == "MESSAGE"          # already connected -> message
    notc = seq["unconditionalNode"]
    assert notc["nodeType"] == "VIEW_PROFILE"
    cr = notc["unconditionalNode"]
    assert cr["nodeType"] == "CONNECTION_REQUEST"
    # No CR note supplied → falls back to generic connect message (API rejects empty strings)
    assert cr["payload"]["toBeWithdrawnAfterDays"] == 25
    assert cr["payload"]["messages"] != [""] and cr["payload"]["messages"][0]
    assert cr["payload"]["fallbackMessage"] != ""
    assert cr["conditionalNode"]["nodeType"] == "MESSAGE"           # accepted -> message
    # not accepted -> wait (LIKE_POST) then END
    assert cr["unconditionalNode"]["nodeType"] == "LIKE_POST"
    assert cr["unconditionalNode"]["unconditionalNode"]["nodeType"] == "END"
    assert _all_leaves_are_end(seq)
    assert _delays_valid(seq)


def test_message_node_has_reply_exit_and_payload():
    seq = build_linkedin_sequence(["Hello {{first_name}}"])
    msg = seq["conditionalNode"]  # connected branch's first MESSAGE
    assert msg["payload"]["messages"] == ["Hello {FIRST_NAME}"]
    assert msg["payload"]["fallbackMessage"] == "Hello there"
    assert msg["conditionalNode"]["nodeType"] == "END"   # replied -> END
    assert msg["unconditionalNode"]["nodeType"] == "END" # single message -> END after


def test_two_messages_interleave_like_post():
    seq = build_linkedin_sequence(["m1 {{first_name}}", "m2"])
    chain = seq["conditionalNode"]              # MESSAGE_1
    assert chain["nodeType"] == "MESSAGE"
    inter = chain["unconditionalNode"]          # interaction between 1 and 2
    assert inter["nodeType"] == "LIKE_POST"
    assert inter["unconditionalNode"]["nodeType"] == "MESSAGE"   # MESSAGE_2
    assert _all_leaves_are_end(seq)
    assert _delays_valid(seq)


def test_three_messages_use_two_interactions():
    seq = build_linkedin_sequence(["a", "b", "c"])
    chain = seq["conditionalNode"]
    types = []
    _collect_node_types(chain, types)
    assert types.count("MESSAGE") == 3
    assert "LIKE_POST" in types and "VIEW_PROFILE" in types
    assert _all_leaves_are_end(seq)
    assert _delays_valid(seq)


def test_rejects_empty_and_too_many():
    with pytest.raises(ValueError):
        build_linkedin_sequence([])
    with pytest.raises(ValueError):
        build_linkedin_sequence(["a", "b", "c", "d"])


def test_build_linkedin_sequence_connection_note_used():
    from app.services.heyreach_sequence_builder import build_linkedin_sequence
    tree = build_linkedin_sequence(
        ["Follow-up message"],
        connection_note="Hi {{first_name}}, I'd love to connect!"
    )
    # Find the CONNECTION_REQUEST node
    cr_node = tree["unconditionalNode"]["unconditionalNode"]
    assert cr_node["nodeType"] == "CONNECTION_REQUEST"
    note = cr_node["payload"]["messages"][0]
    assert "{FIRST_NAME}" in note
    assert note != ""


def test_build_linkedin_sequence_blank_note_default():
    from app.services.heyreach_sequence_builder import build_linkedin_sequence
    tree = build_linkedin_sequence(["DM body"])
    cr_node = tree["unconditionalNode"]["unconditionalNode"]
    # No note supplied — must use non-empty fallback (API rejects empty CR messages)
    assert cr_node["payload"]["messages"][0] != ""
    assert cr_node["payload"]["fallbackMessage"] != ""
