import re

_WITHDRAW_DAYS_DEFAULT = 25


def resolve_spintax(text: str) -> str:
    """Resolve spintax blocks like {Hi|Hey} to their first option (e.g., 'Hi')."""
    def replace_block(match):
        content = match.group(1)
        if "|" in content:
            options = content.split("|")
            return options[0].strip()
        return match.group(0)

    pattern = re.compile(r"\{([^{}]*\|[^{}]*)\}")
    current = text
    while True:
        next_text = pattern.sub(replace_block, current)
        if next_text == current:
            break
        current = next_text
    return current


def to_heyreach_message(body: str, *, collapse_whitespace: bool = False) -> tuple[str, str]:
    text = (body or "")
    for sig in ("%signature%", "%Signature%", "%SIGNATURE%"):
        text = text.replace(sig, "")
    text = text.strip()

    # Normalize line endings
    text = re.sub(r"\r\n", "\n", text)
    # Collapse 3+ newlines to a paragraph break, then a single newline (soft wrap,
    # e.g. "{{first_name}} ,\n{{personalized_first_line}}\nBody...") to a space so
    # it reads as one continuous opening line instead of 3 separate lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", " ", text)
    # Trim trailing spaces left on each paragraph line
    text = "\n\n".join(line.strip() for line in text.split("\n\n"))
    text = re.sub(r" {2,}", " ", text)
    # Fix space before punctuation, e.g. "{{first_name}} ," → "{{first_name}},"
    text = re.sub(r" +([,\.!?])", r"\1", text)

    if collapse_whitespace:
        text = re.sub(r"\s+", " ", text)
        text = re.sub(r"\s+([,\.!?])", r"\1", text)

    text = resolve_spintax(text)

    # Convert {{custom_var}} → {CUSTOM_VAR} for HeyReach single-bracket format
    def to_heyreach_var(m: re.Match) -> str:
        return "{" + m.group(1).upper() + "}"

    text = re.sub(r"\{\{(\w+)\}\}", to_heyreach_var, text)

    def render(first: str, company: str) -> str:
        return (
            text.replace("{FIRST_NAME}", first)
            .replace("{COMPANY_NAME}", company)
            .replace("{COMPANY}", company)
        )

    message = render("{FIRST_NAME}", "{COMPANY}")
    fallback = render("there", "your company")
    # Strip any remaining {UNKNOWN_VAR} tokens from fallback — HeyReach rejects unknown vars
    fallback = re.sub(r"\{[A-Z_]+\}", "", fallback)
    # Fix "there ," → "there," (space before punctuation)
    fallback = re.sub(r"\bthere\s+([,\.!?])", r"there\1", fallback)
    # Collapse multiple spaces left by removed placeholders
    fallback = re.sub(r"[ \t]+", " ", fallback)
    # Remove lines that are now empty or whitespace-only after placeholder removal
    fallback = "\n".join(line.strip() for line in fallback.splitlines() if line.strip())
    fallback = fallback.strip()
    return message, fallback


def _like_post(delay: int = 3, unit: str = "DAY") -> dict:
    return {
        "nodeType": "LIKE_POST",
        "actionDelay": delay,
        "actionDelayUnit": unit,
        "payload": {
            "reactionType": "LIKE",
            "randomReaction": True,
            "reactBefore": "MONTH1",
            "skipDelayIfCannotLike": True,
        },
    }


def _view_profile(delay: int = 3, unit: str = "DAY") -> dict:
    return {"nodeType": "VIEW_PROFILE", "actionDelay": delay, "actionDelayUnit": unit}


def _end(delay: int = 0, unit: str = "HOUR") -> dict:
    return {"nodeType": "END", "actionDelay": delay, "actionDelayUnit": unit}


def _interaction(idx: int) -> dict:
    return _like_post() if idx % 2 == 0 else _view_profile()


def _message_chain(messages: list[str], idx: int) -> dict:
    message, fallback = to_heyreach_message(messages[idx])
    node = {
        "nodeType": "MESSAGE",
        "actionDelay": 1,
        "actionDelayUnit": "DAY",
        "payload": {"messages": [message], "fallbackMessage": fallback},
        "conditionalNode": _end(0, "HOUR"),  # replied -> exit
    }
    if idx == len(messages) - 1:
        node["unconditionalNode"] = _end(3, "DAY")
    else:
        interaction = _interaction(idx)
        interaction["unconditionalNode"] = _message_chain(messages, idx + 1)
        node["unconditionalNode"] = interaction
    return node


def build_linkedin_sequence(
    messages: list[str],
    *,
    connection_note: str = "",
    withdraw_days: int = _WITHDRAW_DAYS_DEFAULT,
) -> dict:
    if not messages or len(messages) > 3:
        raise ValueError("LinkedIn sequence needs 1 to 3 messages")
    if connection_note.strip():
        note_text, note_fallback = to_heyreach_message(connection_note, collapse_whitespace=True)
    else:
        note_text = ""
        note_fallback = ""
    # When no note: send empty strings — HeyReach UI uses this for "Leave Blank" CR.
    # When note provided: use the note text and fallback.
    cr_messages = [note_text] if note_text else [""]
    cr_fallback = note_fallback if note_fallback else ""
    return {
        "nodeType": "CHECK_IS_CONNECTION",
        "actionDelay": 0,
        "actionDelayUnit": "HOUR",
        "conditionalNode": _message_chain(messages, 0),
        "unconditionalNode": {
            "nodeType": "VIEW_PROFILE",
            "actionDelay": 1,
            "actionDelayUnit": "DAY",
            "unconditionalNode": {
                "nodeType": "CONNECTION_REQUEST",
                "actionDelay": 2,
                "actionDelayUnit": "DAY",
                "payload": {"messages": cr_messages, "fallbackMessage": cr_fallback, "toBeWithdrawnAfterDays": withdraw_days},
                "conditionalNode": _message_chain(messages, 0),
                "unconditionalNode": {
                    **_like_post(3, "DAY"),
                    "unconditionalNode": _end(1, "DAY"),
                },
            },
        },
    }
