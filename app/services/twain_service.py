"""Twain twin-campaign helpers: <br>-primary spacing normalizer, audit, greeting
flags, and the fixed twin sequence template.

Contract (locked by tests/test_twain_service.py):
    audit_twain_field(normalize_twain_field(x)) == []  for every input.
"""

import re

_AUDIT_PATTERNS = {
    "lone_br":        re.compile(r"(?i)(?<!<br>)<br>(?!<br>)"),
    "triple_br":      re.compile(r"(?i)(?:<br>){3,}"),
    "space_before_br": re.compile(r"[ \t]<br>"),
    "lone_nl":        re.compile(r"(?<!\n)\n(?!\n)"),
    "triple_nl":      re.compile(r"\n{3,}"),
    "trailing_space_nl": re.compile(r"[ \t]+\n"),
}

_GREETING_ANY = re.compile(r"(?i)^Hi [^,]+,")


def _clean_unicode(text: str) -> str:
    text = re.sub(r"[​-‍﻿]", "", text)   # zero-width + BOM
    text = text.replace(" ", " ")                     # nbsp -> space
    text = text.replace(" ", "\n").replace(" ", "\n")  # line/para sep
    return text


def normalize_twain_field(value: str, *, is_subject: bool = False) -> str:
    """Idempotent spacing normalizer. Never alters wording.

    Body fields are <br>-primary (Twain's actual format), with a raw-newline
    fallback. Subjects are flattened to a single line.
    """
    if not isinstance(value, str) or not value.strip():
        return value

    text = value.replace("\r\n", "\n").replace("\r", "\n")
    text = _clean_unicode(text)

    if is_subject:
        text = re.sub(r"(?i)<br\s*/?>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    text = re.sub(r"(?i)<br\s*/?>", "<br>", text)          # standardize tag spelling
    text = re.sub(r"[ \t]*<br>[ \t]*", "<br>", text)        # strip spaces around <br>
    text = re.sub(r"(?:<br>){3,}", "<br><br>", text)        # 3+ -> 2
    text = re.sub(r"(?<!<br>)<br>(?!<br>)", "<br><br>", text)  # lone -> double
    text = re.sub(r"(?:<br>){3,}", "<br><br>", text)        # re-collapse if overshot

    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"(?<!\n)\n(?!\n)", "\n\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)

    text = re.sub(r"^(?:<br>|\n|\s)+", "", text)
    text = re.sub(r"(?:<br>|\n|\s)+$", "", text)
    return text


def audit_twain_field(value: str) -> list[str]:
    """Return names of spacing defects found. Clean field -> []."""
    if not isinstance(value, str) or not value.strip():
        return []
    return [name for name, pat in _AUDIT_PATTERNS.items() if pat.search(value)]


def flag_greeting_issues(step1: str | None, step3: str | None) -> list[str]:
    """Flag (do not edit) greeting-CONTENT issues.

    Step 1 should have NO greeting (template supplies it); Step 3 SHOULD start
    with 'Hi X,'. Tight greeting SPACING is auto-fixed by the normalizer, so it
    is not flagged here — only presence/absence.
    """
    flags: list[str] = []
    if isinstance(step1, str) and _GREETING_ANY.match(step1.strip()):
        flags.append("step1_has_greeting")
    if isinstance(step3, str) and step3.strip() and not _GREETING_ANY.match(step3.strip()):
        flags.append("step3_missing_greeting")
    return flags


def twain_sequence_plan(followup_delay_days: int = 3) -> list[dict]:
    """The fixed twin template in the app's plan SequenceStep shape.

    Bodies are authored with \\n\\n; format_email_body_for_smartlead emits
    <br><br> at push time.
    """
    return [
        {
            "step_number": 1,
            "delay_days": 0,
            "variants": [
                {
                    "variant_label": "A",
                    "subject": "{{Subject 1}}",
                    "body": "Hi {{first_name}},\n\n{{Step 1}}\n\n%signature%",
                }
            ],
        },
        {
            "step_number": 2,
            "delay_days": followup_delay_days,
            "variants": [
                {
                    "variant_label": "A",
                    "subject": "",
                    "body": "{{Step 3}}\n\n%signature%",
                }
            ],
        },
    ]
