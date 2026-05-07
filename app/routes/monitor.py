import json

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import Response

from app.config import get_secret_value, settings
from app.services.slack_relay_service import (
    parse_slack_action_payload,
    post_campaign_status_alert,
    verify_slack_signature,
)
from app.services.smartlead_monitor_service import (
    classify_pause,
    find_account_for_campaign,
    resume_campaign,
)

router = APIRouter(prefix="/api/monitor")


@router.post("/smartlead")
async def smartlead_status_webhook(request: Request):
    _verify_webhook_secret(request)
    payload = await request.json()

    campaign_id = _campaign_id_from_payload(payload)
    if not campaign_id:
        return {"ok": True, "ignored": "missing campaign_id"}

    status = _status_from_payload(payload)
    campaign_name = str(payload.get("campaign_name") or payload.get("name") or "")
    account, campaign = await find_account_for_campaign(campaign_id)
    if not account:
        return {"ok": False, "ignored": "campaign_not_found", "campaign_id": campaign_id}

    if not campaign_name and campaign:
        campaign_name = str(campaign.get("name") or "")
    if not status and campaign:
        status = str(campaign.get("status") or "")

    classification = None
    if status.upper() == "PAUSED":
        classification = await classify_pause(campaign_id=campaign_id, account=account, webhook_payload=payload)

    alert_posted = False
    if status.upper() == "PAUSED" and classification and classification.is_bounce_protection:
        await post_campaign_status_alert(
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            status=status.upper(),
            account=account,
            classification=classification,
        )
        alert_posted = True

    return {
        "ok": True,
        "campaign_id": campaign_id,
        "status": status.upper() if status else None,
        "account": account.workspace_name,
        "classification": classification.kind if classification else None,
        "alert_posted": alert_posted,
    }


@router.post("/slack/actions")
async def slack_action(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    if not verify_slack_signature(raw_body, dict(request.headers)):
        raise HTTPException(status_code=401, detail="Invalid Slack signature")

    payload = parse_slack_action_payload(raw_body)
    action = (payload.get("actions") or [{}])[0]
    if action.get("action_id") != "campaign_action_select":
        return Response(status_code=200)

    selected = json.loads((action.get("selected_option") or {}).get("value") or "{}")
    selected_action = selected.get("action")
    campaign_id = _positive_int(selected.get("campaign_id"))
    campaign_name = str(selected.get("campaign_name") or campaign_id or "")
    user = (payload.get("user") or {}).get("name") or (payload.get("user") or {}).get("username") or "Someone"

    if not campaign_id:
        return Response(status_code=200)
    if selected_action in {"PAUSED", "ACTIVE"}:
        background_tasks.add_task(
            _handle_slack_campaign_action,
            payload,
            selected_action,
            campaign_id,
            campaign_name,
            user,
        )
    return Response(status_code=200)


async def _handle_slack_campaign_action(
    payload: dict,
    selected_action: str,
    campaign_id: int,
    campaign_name: str,
    user: str,
) -> None:
    try:
        if selected_action == "PAUSED":
            await _post_slack_response(payload, f"{user} chose to keep {campaign_name} paused.")
            return

        account, _campaign = await find_account_for_campaign(campaign_id)
        if not account:
            await _post_slack_response(payload, f"Could not find Smartlead campaign {campaign_id} in configured accounts.")
            return
        await resume_campaign(campaign_id, account)
        await _post_slack_response(payload, f"{user} resumed {campaign_name}. Campaign is now ACTIVE.")
    except Exception as exc:
        await _post_slack_response(
            payload,
            f"Could not apply action to {campaign_name or campaign_id}: {_redact_secret_text(str(exc))}",
        )


def _verify_webhook_secret(request: Request) -> None:
    expected = (
        get_secret_value("SMARTLEAD_WEBHOOK_SECRET")
        or get_secret_value("WEBHOOK_SECRET")
        or settings.SMARTLEAD_WEBHOOK_SECRET
    )
    if not expected:
        return
    token = request.headers.get("x-webhook-secret") or request.query_params.get("secret")
    if token != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _campaign_id_from_payload(payload: dict) -> int | None:
    for key in ("campaign_id", "campaignId", "id"):
        value = _positive_int(payload.get(key))
        if value:
            return value
    return None


def _status_from_payload(payload: dict) -> str:
    for key in ("status", "new_status", "campaign_status"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _positive_int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


async def _post_slack_response(payload: dict, text: str) -> None:
    response_url = payload.get("response_url")
    if not response_url:
        return
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        await client.post(
            response_url,
            json={
                "text": text,
                "replace_original": False,
                "response_type": "in_channel",
            },
        )


def _redact_secret_text(value: str) -> str:
    return __import__("re").sub(r"api_key=([^&\s]+)", "api_key=[redacted]", value)
