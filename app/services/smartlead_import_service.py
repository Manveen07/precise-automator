from html import unescape
import re
import string


def build_campaign_plan_from_smartlead(
    *,
    workspace_key: str,
    campaign: dict,
    sequences: list[dict],
    max_new_leads_per_day: int = 100,
) -> dict:
    campaign_name = str(campaign.get("name") or f"Smartlead Campaign {campaign.get('id', '')}").strip()
    schedule = campaign.get("scheduler_cron_value") or {}
    sequence = [_build_sequence_step(step) for step in sorted(sequences, key=lambda item: int(item.get("seq_number") or 0))]
    sequence = [step for step in sequence if step["variants"]]

    return {
        "workspace_key": workspace_key,
        "client_key": None,
        "campaign_name": campaign_name or "Imported Smartlead Campaign",
        "template_family": "smartlead_import_v1",
        "goal": "book_meeting",
        "lead_source": {"type": "none", "expected_count": None},
        "schedule": {
            "timezone": schedule.get("tz") or "America/New_York",
            "days_of_the_week": schedule.get("days") or [1, 2, 3, 4, 5],
            "start_hour": _normalize_hour(schedule.get("startHour") or "09:00"),
            "end_hour": _normalize_hour(schedule.get("endHour") or "18:00"),
            "min_time_btw_emails": int(campaign.get("min_time_btwn_emails") or 17),
            "max_new_leads_per_day": int(campaign.get("max_leads_per_day") or max_new_leads_per_day or 100),
        },
        "settings": {
            "send_as_plain_text": True,
            "track_opens": False,
            "track_clicks": False,
            "stop_on_reply": True,
            "enable_ai_esp_matching": bool(campaign.get("enable_ai_esp_matching", True)),
            "auto_pause_domain_leads_on_reply": True,
            "ooo_restart_delay_days": 10,
        },
        "inbox_selection": {"mode": "skip", "email_account_ids": [], "provider_mix": {"gmail": 0.7, "outlook": 0.3}},
        "sequence": sequence,
        "approval_required": True,
        "notes_for_operator": ["Imported from an existing Smartlead campaign."],
    }


def _build_sequence_step(step: dict) -> dict:
    step_number = int(step.get("seq_number") or 1)
    variants = []
    raw_variants = step.get("sequence_variants") or []
    for idx, variant in enumerate(raw_variants):
        if variant.get("is_deleted"):
            continue
        body = _html_to_text(variant.get("email_body") or step.get("email_body") or "")
        if not body:
            continue
        variants.append(
            {
                "variant_label": str(variant.get("variant_label") or _label_for_index(idx)),
                "subject": str(variant.get("subject") or step.get("subject") or ""),
                "body": _canonicalize_body(body),
            }
        )
    if not variants:
        body = _html_to_text(step.get("email_body") or "")
        if body:
            variants.append(
                {
                    "variant_label": "A",
                    "subject": str(step.get("subject") or ""),
                    "body": _canonicalize_body(body),
                }
            )
    return {
        "step_number": step_number,
        "delay_days": int((step.get("seq_delay_details") or {}).get("delayInDays") or 1),
        "variants": variants,
    }


def _html_to_text(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", value or "")
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"(?i)<p[^>]*>", "", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _canonicalize_body(body: str) -> str:
    return body.strip().replace("%signature%", "%Signature%").replace("%SIGNATURE%", "%Signature%")


def _normalize_hour(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"\d{1,2}", text):
        return f"{int(text):02d}:00"
    return text


def _label_for_index(index: int) -> str:
    if index < len(string.ascii_uppercase):
        return string.ascii_uppercase[index]
    return f"V{index + 1}"
