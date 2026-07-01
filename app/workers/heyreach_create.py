"""Background worker: create/update a HeyReach LinkedIn campaign."""

import asyncio

import httpx

from app.config import get_workspace_config
from app.services.heyreach_sequence_builder import build_linkedin_sequence
from app.services.heyreach_service import HeyReachService
from app import store

_BLANK = {"<leave blank>", "leave blank", "<blank>"}


def create_heyreach_campaign_now(campaign_id: str) -> dict:
    return asyncio.run(_create_async(campaign_id))


def update_heyreach_sequence_now(campaign_id: str) -> dict:
    """Update sequence on existing HeyReach campaign; create fresh if 404."""
    return asyncio.run(_update_sequence_async(campaign_id))


def sync_heyreach_now(campaign_id: str) -> dict:
    """Called on every resync: update if exists, create if not."""
    return asyncio.run(_sync_async(campaign_id))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_linkedin_messages(plan: dict) -> tuple[list[str], str]:
    """Return (dm_messages, connection_note) from a plan dict."""
    sequence_steps = plan.get("sequence") or []
    dm_steps = sorted(
        [s for s in sequence_steps if s.get("channel") == "linkedin" and s.get("linkedin_subtype") != "connection_request"],
        key=lambda s: s.get("step_number", 0),
    )
    cr_steps = [s for s in sequence_steps if s.get("channel") == "linkedin" and s.get("linkedin_subtype") == "connection_request"]
    dm_messages = [
        (s.get("variants") or [{}])[0].get("body", "")
        for s in dm_steps
        if (s.get("variants") or [{}])[0].get("body", "").strip()
        and (s.get("variants") or [{}])[0].get("body", "").strip().lower() not in _BLANK
    ]
    connection_note = ""
    if cr_steps:
        cr_body = (cr_steps[0].get("variants") or [{}])[0].get("body", "").strip()
        if cr_body.lower() not in _BLANK:
            connection_note = cr_body
    return dm_messages, connection_note


def _account_ids(accounts_response: dict) -> list[int]:
    items = accounts_response.get("items") or accounts_response.get("data") or []
    ids = []
    for item in items:
        aid = item.get("id") if isinstance(item, dict) else None
        if aid is not None:
            ids.append(int(aid))
    return ids


def _get_heyreach(doc: dict):
    workspace = get_workspace_config(doc.get("smartlead_workspace", ""))
    if not workspace or not workspace.get("heyreach_api_key"):
        raise RuntimeError(f"HeyReach API key not configured for workspace '{doc.get('smartlead_workspace')}'")
    return HeyReachService(workspace["heyreach_api_key"])


# ---------------------------------------------------------------------------
# Async cores
# ---------------------------------------------------------------------------

async def _create_async(campaign_id: str) -> dict:
    summary: dict = {"status": "failed", "errors": [], "heyreach_campaign_id": None, "url": None}

    doc = store.get_campaign(campaign_id)
    if not doc:
        summary["errors"].append(f"Campaign not found: {campaign_id}")
        return summary

    dm_messages, connection_note = _extract_linkedin_messages(doc.get("current_plan") or {})
    if not dm_messages:
        err = "No LinkedIn DM steps found in plan"
        summary["errors"].append(err)
        store.save_heyreach_result(campaign_id, campaign_id_value=None, url=None, status="failed", error=err)
        return summary

    try:
        heyreach = _get_heyreach(doc)
        accounts_response = await heyreach.get_linkedin_accounts()
        all_ids = _account_ids(accounts_response)

        from app.config import get_heyreach_account_ids_for_client
        mapped_ids = get_heyreach_account_ids_for_client(doc.get("smartlead_workspace", ""), doc.get("smartlead_client_name"))
        if mapped_ids is not None:
            filtered = [i for i in all_ids if i in mapped_ids]
            all_ids = filtered if filtered else all_ids
        if not all_ids:
            raise RuntimeError("No LinkedIn sender accounts in this workspace")

        campaign_name = doc.get("campaign_name", "")
        created_list = await heyreach.create_empty_list(campaign_name)
        list_id = int(created_list.get("id") or created_list.get("listId"))

        sequence = build_linkedin_sequence(dm_messages, connection_note=connection_note)
        created = await heyreach.create_campaign(campaign_name, list_id, all_ids, sequence)
        hr_id = int(created.get("id") or created.get("campaignId"))
        url = heyreach.campaign_url(hr_id)

        summary.update(status="draft_created", heyreach_campaign_id=hr_id, url=url)
        store.save_heyreach_result(campaign_id, campaign_id_value=hr_id, url=url, status="draft_created")

    except Exception as exc:
        err = f"{exc.__class__.__name__}: {exc}"
        summary["errors"].append(err)
        store.save_heyreach_result(campaign_id, campaign_id_value=None, url=None, status="failed", error=err)

    return summary


async def _update_sequence_async(campaign_id: str) -> dict:
    """Push rebuilt sequence to existing HeyReach campaign; fall back to create on 404."""
    summary: dict = {"status": "failed", "errors": []}

    doc = store.get_campaign(campaign_id)
    if not doc:
        summary["errors"].append(f"Campaign not found: {campaign_id}")
        return summary

    hr_campaign_id = doc.get("heyreach_campaign_id")
    if not hr_campaign_id:
        return await _create_async(campaign_id)

    dm_messages, connection_note = _extract_linkedin_messages(doc.get("current_plan") or {})
    if not dm_messages:
        summary["errors"].append("No LinkedIn DM steps found in plan")
        return summary

    try:
        heyreach = _get_heyreach(doc)
        sequence = build_linkedin_sequence(dm_messages, connection_note=connection_note)
        await heyreach.post("campaign/UpdateSequence", {"campaignId": hr_campaign_id, "Sequence": sequence})
        url = heyreach.campaign_url(hr_campaign_id)
        summary["status"] = "draft_created"
        store.save_heyreach_result(campaign_id, campaign_id_value=hr_campaign_id, url=url, status="draft_created")

    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            # Campaign deleted in HeyReach — clear ID and create fresh
            store.save_heyreach_result(campaign_id, campaign_id_value=None, url=None, status=None, error=None)
            return await _create_async(campaign_id)
        err = f"{exc.__class__.__name__}: {exc}"
        summary["errors"].append(err)
        store.save_heyreach_result(campaign_id, campaign_id_value=hr_campaign_id, url=None, status="failed", error=err)

    except Exception as exc:
        err = f"{exc.__class__.__name__}: {exc}"
        summary["errors"].append(err)
        store.save_heyreach_result(campaign_id, campaign_id_value=hr_campaign_id, url=None, status="failed", error=err)

    return summary


async def _sync_async(campaign_id: str) -> dict:
    """Called on every resync: update sequence if campaign exists, create if not."""
    doc = store.get_campaign(campaign_id)
    if not doc:
        return {"status": "failed", "errors": [f"Campaign not found: {campaign_id}"]}
    if doc.get("heyreach_campaign_id"):
        return await _update_sequence_async(campaign_id)
    return await _create_async(campaign_id)
