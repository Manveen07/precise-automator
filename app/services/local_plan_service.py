import string


def build_campaign_plan_from_input(raw_input: dict, note: str | None = None) -> dict:
    parsed = raw_input.get("parsed_messaging") or {}
    subjects = parsed.get("subjects") or []
    steps = parsed.get("steps") or []
    max_new_leads_per_day = int(raw_input.get("max_new_leads_per_day") or 100)
    parser_warnings = parsed.get("warnings") or []
    plan_warnings = []
    if len(steps) > 4:
        plan_warnings.append(f"Input had {len(steps)} steps; V1 draft includes only the first 4 steps.")

    sequence = []
    for step in steps[:4]:
        step_number = int(step["step_number"])
        body_variants = step.get("body_variants") or []
        variants = _build_step_variants(step_number, subjects, body_variants)
        if variants:
            sequence.append(
                {
                    "step_number": step_number,
                    "delay_days": _default_delay_days(step_number),
                    "variants": variants,
                }
            )
        else:
            plan_warnings.append(f"Skipped step {step_number} because no body variants were parsed.")

    notes = [
        "Draft generated deterministically from the parsed messaging file.",
        "Review sequence copy and settings before Smartlead sync.",
    ]
    if note:
        notes.insert(0, note)
    if parsed.get("selected_campaign"):
        notes.append(f"Selected messaging sequence: {parsed['selected_campaign']}.")
    notes.extend(parser_warnings)
    notes.extend(plan_warnings)

    return {
        "workspace_key": raw_input["workspace_key"],
        "client_key": None,
        "campaign_name": raw_input["campaign_name"],
        "template_family": raw_input.get("template_key") or "cold_email_standard_v1",
        "goal": "book_meeting",
        "lead_source": {"type": "none", "expected_count": None},
        "schedule": {
            "timezone": "America/New_York",
            "days_of_the_week": [1, 2, 3, 4, 5],
            "start_hour": "09:00",
            "end_hour": "18:00",
            "min_time_btw_emails": 17,
            "max_new_leads_per_day": max_new_leads_per_day,
        },
        "settings": {
            "send_as_plain_text": True,
            "track_opens": False,
            "track_clicks": False,
            "stop_on_reply": True,
            "enable_ai_esp_matching": True,
            "auto_pause_domain_leads_on_reply": True,
            "ooo_restart_delay_days": 10,
        },
        "inbox_selection": {"mode": "skip", "email_account_ids": [], "provider_mix": {"gmail": 0.7, "outlook": 0.3}},
        "sequence": sequence,
        "approval_required": True,
        "notes_for_operator": notes,
    }


def _build_step_variants(step_number: int, subjects: list[str], body_variants: list[dict]) -> list[dict]:
    if step_number == 1:
        selected_subjects = subjects or [""]
        variants = []
        for subject in selected_subjects:
            for body_variant in body_variants:
                variants.append(
                    {
                        "variant_label": _label_for_index(len(variants)),
                        "subject": subject,
                        "body": _canonicalize_body(body_variant["body"]),
                    }
                )
        return variants

    return [
        {
            "variant_label": _label_for_index(idx),
            "subject": "",
            "body": _canonicalize_body(body_variant["body"]),
        }
        for idx, body_variant in enumerate(body_variants)
    ]


def _canonicalize_body(body: str) -> str:
    return body.strip().replace("%signature%", "%Signature%").replace("%SIGNATURE%", "%Signature%")


def _default_delay_days(step_number: int) -> int:
    return {1: 1, 2: 3, 3: 4, 4: 5}.get(step_number, 3)


def _label_for_index(index: int) -> str:
    if index < len(string.ascii_uppercase):
        return string.ascii_uppercase[index]
    return f"V{index + 1}"
