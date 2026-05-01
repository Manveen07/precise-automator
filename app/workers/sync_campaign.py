"""Sync a campaign doc to Smartlead.

Runs as a FastAPI BackgroundTask after `POST /api/campaigns/{id}/sync`.

Steps:
  1. Load the campaign doc from Mongo.
  2. Re-validate the plan (defensive; route already checks).
  3. Resolve the workspace API key + client_id from env.
  4. If no smartlead_campaign_id: create the Smartlead campaign.
     Else: update the existing one in place.
  5. Apply settings, schedule, sequences. Optionally attach email accounts.
  6. Update the Mongo doc with the resulting smartlead_campaign_id and status.

On failure, mark the doc `failed` with the error text. The route returns to the
operator immediately, so this runs after the response is sent.
"""

import asyncio
import json
import re

import httpx

from app.config import get_workspace_config
from app.services.sequence_builder import build_smartlead_sequences
from app.services.smartlead_service import SmartleadService
from app.services.validation_service import validate_campaign_plan
from app import store


def sync_campaign_now(campaign_id: str) -> None:
    """Synchronous entrypoint used by FastAPI BackgroundTasks."""
    asyncio.run(_sync_campaign_async(campaign_id))


async def _sync_campaign_async(campaign_id: str) -> None:
    doc = store.get_campaign(campaign_id)
    if not doc:
        return

    try:
        plan = doc.get("current_plan") or {}
        workspace_keys = _active_workspace_keys()
        errors = validate_campaign_plan(plan, workspace_keys)
        if errors:
            store.mark_sync_failed(campaign_id, "Validation failed: " + "; ".join(errors))
            return

        workspace = get_workspace_config(doc["smartlead_workspace"])
        if not workspace or not workspace.get("api_key"):
            store.mark_sync_failed(
                campaign_id,
                f"Smartlead API key not configured for workspace '{doc['smartlead_workspace']}'",
            )
            return

        smartlead = SmartleadService(workspace["api_key"])
        existing_smartlead_id = doc.get("smartlead_campaign_id")

        if existing_smartlead_id:
            smartlead_id = existing_smartlead_id
        else:
            client_id = await _resolve_client_id(smartlead, workspace)
            create_response = await smartlead.create_campaign(doc["campaign_name"], client_id)
            smartlead_id = _extract_campaign_id(create_response)

        ooo_delay_days = int(plan.get("settings", {}).get("ooo_restart_delay_days", 10))
        await smartlead.apply_v1_settings(smartlead_id, ooo_delay_days)
        await smartlead.update_schedule(smartlead_id, plan["schedule"])

        sequences = build_smartlead_sequences(plan["sequence"])
        await smartlead.update_sequences(smartlead_id, sequences)

        email_account_ids = plan.get("inbox_selection", {}).get("email_account_ids") or []
        if email_account_ids:
            await smartlead.attach_email_accounts(smartlead_id, email_account_ids)

        store.attach_smartlead(campaign_id, smartlead_id)
    except Exception as exc:
        store.mark_sync_failed(campaign_id, _error_text(exc))


def _active_workspace_keys() -> set[str]:
    from app.config import SMARTLEAD_WORKSPACES

    return {w["key"] for w in SMARTLEAD_WORKSPACES}


async def _resolve_client_id(smartlead: SmartleadService, workspace: dict) -> int | None:
    """Return explicit env client_id or find it by Smartlead client name.

    Smartlead's create campaign endpoint can omit client_id, but agency keys can
    create under the wrong account if the intended client is not specified. For
    workspaces with a configured client_name, fetch Smartlead clients and match
    by normalized name. The env var remains an override.
    """
    if workspace.get("client_id"):
        return workspace["client_id"]

    client_name = workspace.get("client_name")
    if not client_name:
        return None

    try:
        response = await smartlead.get_clients()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            return None
        raise
    clients = response.get("data", response) if isinstance(response, dict) else response
    if not isinstance(clients, list):
        raise RuntimeError("Smartlead client list response did not include a data list")
    if not clients:
        return None

    expected = _client_name_key(client_name)
    matches = [client for client in clients if _client_name_key(str(client.get("name", ""))) == expected]
    if not matches:
        raise RuntimeError(f"Smartlead client named '{client_name}' was not found")
    if len(matches) > 1:
        raise RuntimeError(f"Smartlead returned multiple clients named '{client_name}'")

    client_id = matches[0].get("id")
    try:
        parsed_client_id = int(client_id)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Smartlead client '{client_name}' returned invalid id: {client_id}") from exc
    if parsed_client_id <= 0:
        raise RuntimeError(f"Smartlead client '{client_name}' returned invalid id: {client_id}")
    return parsed_client_id


def _client_name_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _extract_campaign_id(response: dict) -> int:
    """Pull the integer campaign id out of Smartlead's create response."""
    for candidate in (response, response.get("data") if isinstance(response.get("data"), dict) else None):
        if not candidate:
            continue
        for key in ("id", "campaign_id"):
            if key in candidate and candidate[key] is not None:
                try:
                    campaign_id = int(candidate[key])
                except (TypeError, ValueError) as exc:
                    raise RuntimeError(
                        f"Smartlead create_campaign returned non-numeric {key}: {_response_snippet(response)}"
                    ) from exc
                if campaign_id <= 0:
                    raise RuntimeError(
                        f"Smartlead create_campaign returned invalid {key}: {_response_snippet(response)}"
                    )
                return campaign_id
    raise RuntimeError(f"Smartlead create_campaign response missing id/campaign_id: {_response_snippet(response)}")


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        response = exc.response
        return (
            f"{exc.__class__.__name__}: HTTP {response.status_code} for {response.request.method} "
            f"{_redact_api_key(str(response.request.url))}; body={_truncate(response.text)}"
        )
    return f"{exc.__class__.__name__}: {exc}"


def _response_snippet(response: dict) -> str:
    return _truncate(json.dumps(response, default=str, sort_keys=True))


def _truncate(value: str, limit: int = 1000) -> str:
    return value if len(value) <= limit else value[:limit] + "...[truncated]"


def _redact_api_key(value: str) -> str:
    return re.sub(r"([?&]api_key=)[^&]+", r"\1[redacted]", value)
