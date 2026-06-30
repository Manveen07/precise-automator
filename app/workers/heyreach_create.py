"""Background worker: create a HeyReach LinkedIn campaign.

Mirrors the sync_campaign / twin_fix pattern — a sync entrypoint wraps
asyncio.run() around the real async core so FastAPI BackgroundTasks can call it
without blocking the event loop.
"""

import asyncio

from app.config import get_workspace_config
from app.services.heyreach_sequence_builder import build_linkedin_sequence
from app.services.heyreach_service import HeyReachService
from app import store


def create_heyreach_campaign_now(campaign_id: str) -> dict:
    """Synchronous entrypoint used by FastAPI BackgroundTasks."""
    return asyncio.run(_create_async(campaign_id))


async def _create_async(campaign_id: str) -> dict:
    summary: dict = {
        "status": "failed",
        "errors": [],
        "heyreach_campaign_id": None,
        "url": None,
    }

    # Load campaign doc
    doc = store.get_campaign(campaign_id)
    if not doc:
        summary["errors"].append(f"Campaign not found: {campaign_id}")
        return summary

    # Extract LinkedIn messages from the current plan
    plan = doc.get("current_plan") or {}
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
    ]
    connection_note = ""
    if cr_steps:
        cr_body = (cr_steps[0].get("variants") or [{}])[0].get("body", "")
        connection_note = cr_body.strip()

    if not dm_messages:
        err = "No LinkedIn DM steps found in plan"
        summary["errors"].append(err)
        store.save_heyreach_result(
            campaign_id,
            campaign_id_value=None,
            url=None,
            status="failed",
            error=err,
        )
        return summary

    # Workspace / API key
    workspace = get_workspace_config(doc.get("smartlead_workspace", ""))
    if not workspace or not workspace.get("heyreach_api_key"):
        err = f"HeyReach API key not configured for workspace '{doc.get('smartlead_workspace')}'"
        summary["errors"].append(err)
        store.save_heyreach_result(
            campaign_id,
            campaign_id_value=None,
            url=None,
            status="failed",
            error=err,
        )
        return summary

    heyreach = HeyReachService(workspace["heyreach_api_key"])

    try:
        # Fetch all sender accounts
        accounts_response = await heyreach.get_linkedin_accounts()
        all_ids = _account_ids(accounts_response)

        # Filter by per-client account mapping when one is configured
        from app.config import get_heyreach_account_ids_for_client
        client_name = doc.get("smartlead_client_name")
        workspace_key = doc.get("smartlead_workspace", "")
        mapped_ids = get_heyreach_account_ids_for_client(workspace_key, client_name)
        if mapped_ids is not None:
            filtered = [i for i in all_ids if i in mapped_ids]
            # Fall back to all accounts if none of the mapped IDs exist in this workspace
            all_ids = filtered if filtered else all_ids

        account_ids = all_ids
        if not account_ids:
            raise RuntimeError("No LinkedIn sender accounts in this workspace")

        # Create an empty lead list
        campaign_name = doc.get("campaign_name", "")
        created_list = await heyreach.create_empty_list(campaign_name)
        list_id = int(created_list.get("id") or created_list.get("listId"))

        # Build the sequence tree and create the campaign
        sequence = build_linkedin_sequence(dm_messages, connection_note=connection_note)
        created = await heyreach.create_campaign(campaign_name, list_id, account_ids, sequence)
        hr_id = int(created.get("id") or created.get("campaignId"))
        url = heyreach.campaign_url(hr_id)

        summary["status"] = "draft_created"
        summary["heyreach_campaign_id"] = hr_id
        summary["url"] = url

        store.save_heyreach_result(
            campaign_id,
            campaign_id_value=hr_id,
            url=url,
            status="draft_created",
        )

    except Exception as exc:
        err = f"{exc.__class__.__name__}: {exc}"
        summary["errors"].append(err)
        store.save_heyreach_result(
            campaign_id,
            campaign_id_value=None,
            url=None,
            status="failed",
            error=err,
        )

    return summary


def _account_ids(accounts_response: dict) -> list[int]:
    items = accounts_response.get("items") or accounts_response.get("data") or []
    ids = []
    for item in items:
        aid = item.get("id") if isinstance(item, dict) else None
        if aid is not None:
            ids.append(int(aid))
    return ids
