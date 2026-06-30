_WITHDRAW_DAYS_DEFAULT = 25


def to_heyreach_message(body: str) -> tuple[str, str]:
    text = (body or "")
    for sig in ("%signature%", "%Signature%", "%SIGNATURE%"):
        text = text.replace(sig, "")
    text = text.strip()

    def render(first: str, company: str) -> str:
        return (
            text.replace("{{first_name}}", first)
            .replace("{{company_name}}", company)
            .replace("{{company}}", company)
        )

    message = render("{FIRST_NAME}", "{COMPANY}")
    fallback = render("there", "your company")
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
        note_text, note_fallback = to_heyreach_message(connection_note)
    else:
        note_text = ""
        note_fallback = ""
    # CONNECTION_REQUEST requires non-empty messages list and non-empty fallback.
    # Use a blank-note friendly default when no CR copy was provided.
    cr_messages = [note_text] if note_text else ["Hi {FIRST_NAME}, I'd love to connect."]
    cr_fallback = note_fallback if note_fallback else "Hi there, I'd love to connect."
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
