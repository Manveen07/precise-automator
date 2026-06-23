import re
import string
from collections.abc import Sequence
from html import escape, unescape


def format_email_body_for_smartlead(body: str) -> str:
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = re.sub(r"[\u200b-\u200d\ufeff]", "", body)
    body = body.replace("\u00a0", " ").replace("\u2028", "\n").replace("\u2029", "\n\n")
    # Repair sentences glued by soft-return exports ("care.That" -> "care. That").
    # Lowercase-then-uppercase only, so decimals (3.5), abbreviations (U.S.A),
    # and domains (site.com) are left untouched.
    body = re.sub(r"(?<=[a-z])([.?!])(?=[A-Z])", r"\1 ", body)
    body = body.replace("%Signature%", "%signature%").replace("%SIGNATURE%", "%signature%")
    body = body.replace("\t", "    ")
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = body.strip("\n")
    body = _expand_prose_paragraphs(body)
    body = re.sub(r"\n*%signature%", "\n\n%signature%", body)
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = escape(body, quote=False)
    body = _preserve_visible_spacing(body)
    body = body.replace("\n\n", "<br><br>")
    return body.replace("\n", "<br>")


def format_subject_for_smartlead(subject: str) -> str:
    subject = subject.replace("\r\n", "\n").replace("\r", "\n")
    subject = re.sub(r"[\u200b-\u200d\ufeff]", "", subject)
    subject = subject.replace("\u00a0", " ").replace("\u2028", " ").replace("\u2029", " ")
    return re.sub(r"\s+", " ", subject).strip()


def smartlead_html_to_text(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", value or "")
    text = re.sub(r"(?i)</(?:p|div)\s*>", "\n", text)
    text = re.sub(r"(?i)<(?:p|div)[^>]*>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    text = text.replace("\u00a0", " ").replace("\u2028", "\n").replace("\u2029", "\n\n")
    text = text.replace("%Signature%", "%signature%").replace("%SIGNATURE%", "%signature%")
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _preserve_visible_spacing(body: str) -> str:
    lines = []
    for line in body.split("\n"):
        line = re.sub(r"^ +", lambda match: "&nbsp;" * len(match.group(0)), line)
        line = re.sub(r" {2,}", lambda match: " " + "&nbsp;" * (len(match.group(0)) - 1), line)
        lines.append(line)
    return "\n".join(lines)


_LIST_ITEM_RE = re.compile(r"^\s*(?:[-*•–—]|\d+[.)]|[A-Za-z][.)])\s+")


def _expand_prose_paragraphs(body: str) -> str:
    """Treat adjacent prose lines from rich-text exports as paragraphs.

    Google Docs and similar editors can show paragraph spacing through visual
    styles that disappear in .txt exports. Keep explicit spacing and list
    blocks intact while adding paragraph breaks between prose lines.
    """
    lines = body.split("\n")
    out: list[str] = []
    for idx, line in enumerate(lines):
        out.append(line)
        if idx + 1 >= len(lines):
            break
        next_line = lines[idx + 1]
        if not line.strip() or not next_line.strip():
            continue
        if _LIST_ITEM_RE.match(next_line):
            continue
        out.append("")
    return "\n".join(out)


def build_smartlead_sequences(plan_sequence: Sequence[dict]) -> list[dict]:
    sequences: list[dict] = []
    for step in plan_sequence:
        seq_number = step["step_number"]
        variants = step["variants"]
        # Use a manual percentage split only when every variant has one and they sum to 100;
        # otherwise fall back to an equal split (the safe default).
        percentages = [v.get("distribution_percentage") for v in variants]
        use_percentage = (
            len(variants) > 1
            and all(isinstance(p, (int, float)) for p in percentages)
            and round(sum(percentages)) == 100
        )
        seq_variants = []
        for idx, variant in enumerate(variants):
            label = variant.get("variant_label") or string.ascii_uppercase[idx]
            subject = format_subject_for_smartlead(variant.get("subject", "")) if seq_number == 1 else ""
            seq_variant = {
                "subject": subject,
                "email_body": format_email_body_for_smartlead(variant["body"]),
                "variant_label": label,
            }
            if use_percentage:
                seq_variant["variant_distribution_percentage"] = int(variant["distribution_percentage"])
            seq_variants.append(seq_variant)
        sequences.append(
            {
                "seq_number": seq_number,
                "seq_delay_details": {"delay_in_days": 0 if seq_number == 1 else step["delay_days"]},
                "variant_distribution_type": "MANUAL_PERCENTAGE" if use_percentage else "MANUAL_EQUAL",
                "seq_variants": seq_variants,
            }
        )
    return sequences
