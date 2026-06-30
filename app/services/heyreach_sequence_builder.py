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


def _like_post(delay: int = 2, unit: str = "DAY") -> dict:
    return {
        "nodeType": "LIKE_POST",
        "actionDelay": delay,
        "actionDelayUnit": unit,
        "payload": {
            "reactionType": "LIKE",
            "randomReaction": False,
            "reactBefore": "MONTH1",
            "skipDelayIfCannotLike": False,
        },
    }


def _view_profile(delay: int = 2, unit: str = "DAY") -> dict:
    return {"nodeType": "VIEW_PROFILE", "actionDelay": delay, "actionDelayUnit": unit}


def _end(delay: int = 0, unit: str = "HOUR") -> dict:
    return {"nodeType": "END", "actionDelay": delay, "actionDelayUnit": unit}


def _interaction(idx: int) -> dict:
    return _like_post() if idx % 2 == 0 else _view_profile()


def _message_chain(messages: list[str], idx: int) -> dict:
    message, fallback = to_heyreach_message(messages[idx])
    node = {
        "nodeType": "MESSAGE",
        "actionDelay": 3,
        "actionDelayUnit": "HOUR",
        "payload": {"messages": [message], "fallbackMessage": fallback},
        "conditionalNode": _end(0, "HOUR"),  # replied -> exit
    }
    if idx == len(messages) - 1:
        node["unconditionalNode"] = _end(2, "DAY")
    else:
        interaction = _interaction(idx)
        interaction["unconditionalNode"] = _message_chain(messages, idx + 1)
        node["unconditionalNode"] = interaction
    return node


def build_linkedin_sequence(messages: list[str], *, withdraw_days: int = _WITHDRAW_DAYS_DEFAULT) -> dict:
    if not messages or len(messages) > 3:
        raise ValueError("LinkedIn sequence needs 1 to 3 messages")
    return {
        "nodeType": "CHECK_IS_CONNECTION",
        "actionDelay": 0,
        "actionDelayUnit": "HOUR",
        "conditionalNode": _message_chain(messages, 0),
        "unconditionalNode": {
            "nodeType": "VIEW_PROFILE",
            "actionDelay": 3,
            "actionDelayUnit": "HOUR",
            "unconditionalNode": {
                "nodeType": "CONNECTION_REQUEST",
                "actionDelay": 3,
                "actionDelayUnit": "HOUR",
                "payload": {"messages": [""], "fallbackMessage": "", "toBeWithdrawnAfterDays": withdraw_days},
                "conditionalNode": _message_chain(messages, 0),
                "unconditionalNode": {
                    **_like_post(2, "DAY"),
                    "unconditionalNode": _end(1, "DAY"),
                },
            },
        },
    }
