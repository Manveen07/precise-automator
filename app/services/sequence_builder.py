import re
import string
from collections.abc import Sequence


def format_email_body_for_smartlead(body: str) -> str:
    body = body.replace("\r\n", "\n").replace("\r", "\n")
    body = body.replace("%Signature%", "%signature%").replace("%SIGNATURE%", "%signature%")
    lines = [line.rstrip() for line in body.split("\n")]
    body = "\n".join(lines)
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = body.replace("%signature%", "\n\n%signature%")
    body = re.sub(r"\n{3,}", "\n\n", body)
    body = body.replace("\n\n", "<br><br>")
    return body.replace("\n", "<br>")


def build_smartlead_sequences(plan_sequence: Sequence[dict]) -> list[dict]:
    sequences: list[dict] = []
    for step in plan_sequence:
        seq_number = step["step_number"]
        seq_variants = []
        for idx, variant in enumerate(step["variants"]):
            label = variant.get("variant_label") or string.ascii_uppercase[idx]
            subject = variant.get("subject", "") if seq_number == 1 else ""
            seq_variants.append(
                {
                    "subject": subject,
                    "email_body": format_email_body_for_smartlead(variant["body"]),
                    "variant_label": label,
                }
            )
        sequences.append(
            {
                "seq_number": seq_number,
                "seq_delay_details": {"delay_in_days": step["delay_days"]},
                "variant_distribution_type": "MANUAL_EQUAL",
                "seq_variants": seq_variants,
            }
        )
    return sequences
