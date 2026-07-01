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
    """Post-action nodes need delay >= 1h or any DAY value; 0 only allowed on
    the first CHECK_IS_CONNECTION. Matches the live reference campaign
    (491489), which uses VIEW_PROFILE actionDelay=1 HOUR."""
    def walk(n, is_root):
        if not isinstance(n, dict):
            return True
        delay = n.get("actionDelay", 0)
        unit = n.get("actionDelayUnit", "HOUR")
        zero_ok = is_root or n.get("nodeType") == "END"  # reply-exit ENDs use 0
        if not zero_ok and unit == "HOUR" and delay < 1:
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


def test_to_heyreach_message_strips_space_before_punctuation():
    # Source file has "{{first_name}} ," — neither message nor fallback should keep the space
    msg, fb = to_heyreach_message("{{first_name}} , glad to connect.")
    assert msg == "{FIRST_NAME}, glad to connect."
    assert fb == "there, glad to connect."


def test_single_message_sequence_shape():
    seq = build_linkedin_sequence(["Hi {{first_name}}"])
    assert seq["nodeType"] == "CHECK_IS_CONNECTION"
    assert seq["conditionalNode"]["nodeType"] == "MESSAGE"          # already connected -> message
    notc = seq["unconditionalNode"]
    assert notc["nodeType"] == "VIEW_PROFILE"
    cr = notc["unconditionalNode"]
    assert cr["nodeType"] == "CONNECTION_REQUEST"
    # No CR note supplied → blank strings (HeyReach "Leave Blank" behaviour)
    assert cr["payload"]["toBeWithdrawnAfterDays"] == 25
    assert cr["payload"]["messages"] == [""]
    assert cr["payload"]["fallbackMessage"] == ""
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


def test_first_message_fires_shortly_after_connection_or_acceptance():
    """Step 1 DM should go right away — 3h after CHECK_IS_CONNECTION (already
    connected) or 3h after CR acceptance, not a multi-day wait."""
    seq = build_linkedin_sequence(["Hi {{first_name}}", "Follow up"])
    already_connected_msg1 = seq["conditionalNode"]
    assert already_connected_msg1["actionDelay"] == 3
    assert already_connected_msg1["actionDelayUnit"] == "HOUR"

    cr = seq["unconditionalNode"]["unconditionalNode"]
    assert cr["nodeType"] == "CONNECTION_REQUEST"
    assert cr["actionDelay"] == 3 and cr["actionDelayUnit"] == "HOUR"  # CR fires almost immediately
    accepted_msg1 = cr["conditionalNode"]
    assert accepted_msg1["actionDelay"] == 3
    assert accepted_msg1["actionDelayUnit"] == "HOUR"


def test_followup_uses_file_specified_day_gap():
    """A follow-up's interaction-node delay should match the file's day gap,
    not the 1-day default, when delay_days is provided."""
    seq = build_linkedin_sequence(["Msg1", "Msg2"], delay_days=[0, 3])
    interaction = seq["conditionalNode"]["unconditionalNode"]
    assert interaction["nodeType"] in ("LIKE_POST", "VIEW_PROFILE")
    assert interaction["actionDelay"] == 3
    assert interaction["actionDelayUnit"] == "DAY"


def test_followup_defaults_to_one_day_gap_when_unspecified():
    seq = build_linkedin_sequence(["Msg1", "Msg2"])
    interaction = seq["conditionalNode"]["unconditionalNode"]
    assert interaction["actionDelay"] == 1
    assert interaction["actionDelayUnit"] == "DAY"


def test_not_accepted_branch_waits_at_least_as_long_as_accepted_branch():
    """A late accepter (e.g. day 5) must not be cut off — the not-accepted
    branch's final wait should cover the full accepted+all-followups timeline."""
    seq = build_linkedin_sequence(["Msg1", "Msg2", "Msg3"], delay_days=[0, 3, 4])
    cr = seq["unconditionalNode"]["unconditionalNode"]
    not_accepted_end = cr["unconditionalNode"]["unconditionalNode"]
    assert not_accepted_end["nodeType"] == "END"
    assert not_accepted_end["actionDelayUnit"] == "DAY"
    # Accepted branch total: 1 (reply-exit) + 3 + 4 = 8, plus 1-day buffer = 9
    assert not_accepted_end["actionDelay"] >= 8


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


def test_build_linkedin_sequence_connection_note_collapses_source_spacing():
    from app.services.heyreach_sequence_builder import build_linkedin_sequence

    tree = build_linkedin_sequence(
        ["Follow-up message"],
        connection_note=(
            "{{first_name}} , I led the strategy work behind Cone Health's brand transformation.\n\n"
            "A few things we learned there might apply to {{company}} as well.\n\n"
            "Would love to connect."
        ),
    )

    cr_node = tree["unconditionalNode"]["unconditionalNode"]
    note = cr_node["payload"]["messages"][0]
    fallback = cr_node["payload"]["fallbackMessage"]
    assert note == (
        "{FIRST_NAME}, I led the strategy work behind Cone Health's brand transformation. "
        "A few things we learned there might apply to {COMPANY} as well. "
        "Would love to connect."
    )
    assert fallback.startswith("there, I led")
    assert "\n" not in note


def test_build_linkedin_sequence_blank_note_default():
    from app.services.heyreach_sequence_builder import build_linkedin_sequence
    tree = build_linkedin_sequence(["DM body"])
    cr_node = tree["unconditionalNode"]["unconditionalNode"]
    # No note supplied → blank strings matching HeyReach "Leave Blank" behaviour
    assert cr_node["payload"]["messages"] == [""]
    assert cr_node["payload"]["fallbackMessage"] == ""


def test_to_heyreach_message_resolves_spintax():
    msg, fb = to_heyreach_message(
        "{Hi|Hey|Hello} {{first_name}}! We help {scaling|growing} companies like {{company}}."
    )
    assert msg == "Hi {FIRST_NAME}! We help scaling companies like {COMPANY}."
    assert fb == "Hi there! We help scaling companies like your company."


def test_to_heyreach_message_preserves_source_line_breaks():
    # Real source pattern: name line, personalization line, body line — each is its own
    # line in the .txt (single \n). HeyReach output must keep that exact line layout,
    # not join lines into a flowing paragraph.
    body = (
        "{{first_name}} , \n"
        "{{personalized_first_line}}\n"
        "I've spent a lot of time in regulated categories (Ally, MetLife, a few others). \n\n"
        "My team put together a report that features {{company}}.\n"
        "Happy to send it over."
    )
    msg, _ = to_heyreach_message(body)
    assert msg == (
        "{FIRST_NAME},\n"
        "{PERSONALIZED_FIRST_LINE}\n"
        "I've spent a lot of time in regulated categories (Ally, MetLife, a few others).\n\n"
        "My team put together a report that features {COMPANY}.\n"
        "Happy to send it over."
    )


def test_to_heyreach_message_fallback_strips_heyreach_tokens():
    # Body already uses HeyReach token format — fallback must not contain raw {FIRST_NAME}/{COMPANY}
    msg, fb = to_heyreach_message("Hey {FIRST_NAME}, quick thought for {COMPANY}.")
    assert msg == "Hey {FIRST_NAME}, quick thought for {COMPANY}."
    assert "{FIRST_NAME}" not in fb
    assert "{COMPANY}" not in fb
    assert fb == "Hey there, quick thought for your company."


def test_to_heyreach_message_fallback_strips_custom_placeholders():
    # {{personalized_first_line}} and other custom vars must be removed from fallback
    msg, fb = to_heyreach_message(
        "{{first_name}} , {{personalized_first_line}} I've spent a lot of time in healthcare."
    )
    assert "{{personalized_first_line}}" not in fb
    assert "{FIRST_NAME}" not in fb
    assert fb == "there, I've spent a lot of time in healthcare."


def test_to_heyreach_message_fallback_preserves_paragraph_breaks():
    # Fallback must keep the same line/paragraph structure as the message — stripping a
    # placeholder-only line must drop just that line, not flatten neighboring line breaks.
    body = (
        "{{first_name}} , \n"
        "{{personalized_first_line}}\n"
        "I've spent a lot of time in regulated categories (Ally, MetLife, a few others). \n\n"
        "My team put together a report that features {{company}}.\n"
        "Happy to send it over."
    )
    msg, fb = to_heyreach_message(body)
    assert msg.count("\n\n") == fb.count("\n\n") == 1
    assert fb == (
        "there,\n"
        "I've spent a lot of time in regulated categories (Ally, MetLife, a few others).\n\n"
        "My team put together a report that features your company.\n"
        "Happy to send it over."
    )

