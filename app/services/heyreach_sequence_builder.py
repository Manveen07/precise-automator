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
    # Drop lines that are now empty/whitespace-only (a placeholder was the whole line),
    # but preserve real paragraph breaks (\n\n) between the surviving lines/paragraphs.
    fallback = "\n\n".join(
        "\n".join(line.strip() for line in para.splitlines() if line.strip())
        for para in fallback.split("\n\n")
    )
    fallback = re.sub(r"\n{3,}", "\n\n", fallback)
    fallback = fallback.strip()
    return message, fallback


_DEFAULT_FOLLOWUP_DELAY_DAYS = 1
_FIRST_MESSAGE_DELAY = (3, "HOUR")  # fires right after connection-check / CR acceptance
_NOT_ACCEPTED_BUFFER_DAYS = 1  # slack added on top of the accepted-branch total


def _like_post(delay: int, unit: str) -> dict:
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


def _view_profile(delay: int, unit: str) -> dict:
    return {"nodeType": "VIEW_PROFILE", "actionDelay": delay, "actionDelayUnit": unit}


def _end(delay: int = 0, unit: str = "HOUR") -> dict:
    return {"nodeType": "END", "actionDelay": delay, "actionDelayUnit": unit}


def _interaction(idx: int, delay_days: int) -> dict:
    return _like_post(delay_days, "DAY") if idx % 2 == 0 else _view_profile(delay_days, "DAY")


def _normalize_delays(messages: list[str], delay_days: list[int] | None) -> list[int]:
    """Delay (in days) before each follow-up message, indexed 1..len-1 (index 0 unused —
    the first message always fires FIRST_MESSAGE_DELAY after connection/acceptance).
    Falls back to _DEFAULT_FOLLOWUP_DELAY_DAYS for any step the file didn't specify."""
    if not delay_days:
        return [_DEFAULT_FOLLOWUP_DELAY_DAYS] * len(messages)
    out = list(delay_days) + [_DEFAULT_FOLLOWUP_DELAY_DAYS] * (len(messages) - len(delay_days))
    return [d if isinstance(d, int) and d > 0 else _DEFAULT_FOLLOWUP_DELAY_DAYS for d in out[: len(messages)]]


def _message_chain(messages: list[str], delays: list[int], idx: int) -> dict:
    message, fallback = to_heyreach_message(messages[idx])
    delay, unit = _FIRST_MESSAGE_DELAY if idx == 0 else (3, "HOUR")
    node = {
        "nodeType": "MESSAGE",
        "actionDelay": delay,
        "actionDelayUnit": unit,
        "payload": {"messages": [message], "fallbackMessage": fallback},
        "conditionalNode": _end(1, "DAY"),  # replied -> exit
    }
    if idx == len(messages) - 1:
        node["unconditionalNode"] = _end(_DEFAULT_FOLLOWUP_DELAY_DAYS, "DAY")
    else:
        interaction = _interaction(idx, delays[idx + 1])
        interaction["unconditionalNode"] = _message_chain(messages, delays, idx + 1)
        node["unconditionalNode"] = interaction
    return node


def _accepted_branch_total_days(delays: list[int]) -> int:
    """Cumulative wait (in days) the accepted+all-follow-ups branch takes end to end,
    used to size the not-accepted branch's final wait so a late accepter is never
    cut off mid-sequence."""
    # First message: ~0 days (fires same day, 3h after acceptance).
    # Each follow-up adds its interaction delay + the reply-exit END's 1 day.
    total = 1  # the accepted branch's own final reply-exit wait
    for idx in range(1, len(delays)):
        total += delays[idx]
    return total


def build_linkedin_sequence(
    messages: list[str],
    *,
    connection_note: str = "",
    withdraw_days: int = _WITHDRAW_DAYS_DEFAULT,
    delay_days: list[int] | None = None,
) -> dict:
    """Build the CHECK_IS_CONNECTION -> ... -> END node tree for a LinkedIn sequence.

    delay_days: optional per-follow-up-message day gap, indexed 0..len(messages)-1
    (index 0 is unused/ignored — the first message always fires shortly after
    connection/acceptance). Falls back to a 1-day gap for any step not specified.
    """
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

    delays = _normalize_delays(messages, delay_days)
    not_accepted_wait = _accepted_branch_total_days(delays) + _NOT_ACCEPTED_BUFFER_DAYS

    return {
        "nodeType": "CHECK_IS_CONNECTION",
        "actionDelay": 0,
        "actionDelayUnit": "HOUR",
        # Already connected -> message right away
        "conditionalNode": _message_chain(messages, delays, 0),
        "unconditionalNode": {
            # Not connected -> view profile, then send the connection request almost
            # immediately (matches reference: 1h view, 3h request — not a multi-day wait).
            "nodeType": "VIEW_PROFILE",
            "actionDelay": 1,
            "actionDelayUnit": "HOUR",
            "unconditionalNode": {
                "nodeType": "CONNECTION_REQUEST",
                "actionDelay": 3,
                "actionDelayUnit": "HOUR",
                "payload": {"messages": cr_messages, "fallbackMessage": cr_fallback, "toBeWithdrawnAfterDays": withdraw_days},
                "conditionalNode": _message_chain(messages, delays, 0),
                "unconditionalNode": {
                    # Not accepted: wait as long as the full accepted+messaged branch
                    # would take, so a late accepter still gets the follow-up sequence.
                    **_like_post(2, "DAY"),
                    "unconditionalNode": _end(not_accepted_wait, "DAY"),
                },
            },
        },
    }
