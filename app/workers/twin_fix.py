"""Background worker: fix Twain spacing on a twin campaign's leads.

Mirrors app/workers/sync_campaign.py. Fetches every lead, normalizes the three
Twain custom fields (<br>-primary), writes only changed leads via the per-lead
endpoint, flags greeting-content issues, audits, and re-checks the template
join (repushing the corrected twin sequence if it has reverted to a lone <br>).
"""

import asyncio
import re

from app.config import get_workspace_config
from app.services.sequence_builder import build_smartlead_sequences
from app.services.smartlead_service import SmartleadService
from app.services.twain_service import (
    audit_twain_field,
    flag_greeting_issues,
    normalize_twain_field,
    twain_sequence_plan,
)
from app import store

_FIELD_KEYS = ("Subject 1", "Step 1", "Step 3")
_SUBJECT_KEYS = {"Subject 1"}
_PAGE = 100
_URL_RE = re.compile(r"(?:email-campaigns-v2|email-campaigns|email-campaign|campaigns?)/(\d+)")


def run_twin_fix_now(campaign_id: str, override_url: str | None = None) -> None:
    """Synchronous entrypoint used by FastAPI BackgroundTasks."""
    asyncio.run(_run_twin_fix(campaign_id, override_url))


def _extract_campaign_id_from_url(value: str) -> int | None:
    text = (value or "").strip()
    if not text:
        return None
    if text.isdigit():
        return int(text)
    m = _URL_RE.search(text)
    return int(m.group(1)) if m else None


def _resolve_target_id(doc: dict, override_url: str | None) -> int | None:
    from_url = _extract_campaign_id_from_url(override_url or doc.get("twin_smartlead_url") or "")
    if from_url:
        return from_url
    linked = doc.get("smartlead_campaign_id")
    return int(linked) if linked else None


async def _run_twin_fix(campaign_id: str, override_url: str | None) -> dict:
    summary = {
        "ran_at": store.now_utc(),
        "campaign_id": None,
        "total_leads": 0,
        "leads_changed": 0,
        "field_counts": {k: 0 for k in _FIELD_KEYS},
        "greeting_flags": [],
        "template_repushed": False,
        "residual_defects": [],
        "errors": [],
    }
    doc = store.get_campaign(campaign_id)
    if not doc:
        summary["errors"].append("Campaign not found")
        return summary

    target_id = _resolve_target_id(doc, override_url)
    if not target_id:
        summary["errors"].append("No Smartlead campaign id (paste a URL or link the campaign first)")
        store.save_twin_fix(campaign_id, summary)
        return summary
    summary["campaign_id"] = target_id

    workspace = get_workspace_config(doc.get("smartlead_workspace", ""))
    if not workspace or not workspace.get("api_key"):
        summary["errors"].append(f"Smartlead API key not configured for workspace '{doc.get('smartlead_workspace')}'")
        store.save_twin_fix(campaign_id, summary)
        return summary

    smartlead = SmartleadService(workspace["api_key"])

    try:
        await _fix_leads(smartlead, target_id, summary)
        await _recheck_template(smartlead, target_id, summary)
    except Exception as exc:  # surface, don't crash the background task
        summary["errors"].append(f"{exc.__class__.__name__}: {exc}")

    store.save_twin_fix(campaign_id, summary)
    return summary


async def _fix_leads(smartlead: SmartleadService, campaign_id: int, summary: dict) -> None:
    offset = 0
    while True:
        response = await smartlead.get_leads(campaign_id, limit=_PAGE, offset=offset)
        rows = response.get("data") or []
        if not rows:
            break
        for row in rows:
            lead = row.get("lead") or row
            summary["total_leads"] += 1
            email = (lead.get("email") or "").strip()
            if not email:
                continue  # can't update or send
            cf = dict(lead.get("custom_fields") or {})
            changed = {}
            for key in _FIELD_KEYS:
                if key not in cf:
                    continue
                original = cf[key]
                fixed = normalize_twain_field(original, is_subject=key in _SUBJECT_KEYS)
                if fixed != original:
                    changed[key] = fixed
                    summary["field_counts"][key] += 1
            flags = flag_greeting_issues(cf.get("Step 1"), cf.get("Step 3"))
            for flag in flags:
                summary["greeting_flags"].append({"lead_id": lead.get("id"), "email": email, "flag": flag})
            if changed:
                merged = {**cf, **changed}
                await smartlead.update_lead(campaign_id, lead.get("id"), email, merged)
                summary["leads_changed"] += 1
                for key, val in changed.items():
                    residual = audit_twain_field(val)
                    if residual:
                        summary["residual_defects"].append({"lead_id": lead.get("id"), "field": key, "defects": residual})
        if len(rows) < _PAGE:
            break
        offset += _PAGE


def _step1_body_from_sequences(response: object) -> str | None:
    data = response.get("data") if isinstance(response, dict) else response
    if not isinstance(data, list):
        return None
    for step in data:
        if int(step.get("seq_number") or step.get("step_number") or 0) == 1:
            variants = step.get("seq_variants") or step.get("sequence_variants") or [step]
            for v in variants:
                body = v.get("email_body") or step.get("email_body")
                if body:
                    return body
    return None


async def _recheck_template(smartlead: SmartleadService, campaign_id: int, summary: dict) -> None:
    """Re-check the Step 1 template join; repush the corrected twin sequence if reverted."""
    response = await smartlead.get_sequences(campaign_id)
    step1_body = _step1_body_from_sequences(response)
    if step1_body and audit_twain_field(step1_body):
        sequences = build_smartlead_sequences(twain_sequence_plan())
        await smartlead.update_sequences(campaign_id, sequences)
        summary["template_repushed"] = True
