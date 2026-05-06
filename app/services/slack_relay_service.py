import hashlib
import hmac
import json
import time
from typing import Any

import httpx

from app.config import get_secret_value, settings
from app.services.smartlead_monitor_service import BounceClassification, CampaignAccount


def slack_credentials_configured() -> bool:
    return bool(_slack_bot_token() and _slack_channel_id())


async def post_campaign_status_alert(
    *,
    campaign_id: int,
    campaign_name: str,
    status: str,
    account: CampaignAccount,
    classification: BounceClassification | None = None,
) -> None:
    text = _fallback_text(campaign_id, campaign_name, status, account, classification)
    if slack_credentials_configured():
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                "https://slack.com/api/chat.postMessage",
                headers={
                    "Authorization": f"Bearer {_slack_bot_token()}",
                    "Content-Type": "application/json",
                },
                json={
                    "channel": _slack_channel_id(),
                    "text": text,
                    "blocks": _status_blocks(campaign_id, campaign_name, status, account, classification),
                },
            )
            response.raise_for_status()
        return

    if settings.SLACK_WEBHOOK_URL:
        async with httpx.AsyncClient(timeout=20) as client:
            await client.post(str(settings.SLACK_WEBHOOK_URL), json={"text": text})


def verify_slack_signature(raw_body: bytes, headers: dict[str, str]) -> bool:
    signing_secret = _slack_signing_secret()
    if not signing_secret:
        return True

    timestamp = headers.get("x-slack-request-timestamp")
    signature = headers.get("x-slack-signature")
    if not timestamp or not signature:
        return False
    try:
        timestamp_int = int(timestamp)
    except ValueError:
        return False
    if abs(int(time.time()) - timestamp_int) > 300:
        return False

    sig_base = b"v0:" + timestamp.encode("utf-8") + b":" + raw_body
    expected = "v0=" + hmac.new(signing_secret.encode("utf-8"), sig_base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def parse_slack_action_payload(raw_body: bytes) -> dict[str, Any]:
    from urllib.parse import parse_qs

    params = parse_qs(raw_body.decode("utf-8"))
    payload = params.get("payload", ["{}"])[0]
    return json.loads(payload)


def _status_blocks(
    campaign_id: int,
    campaign_name: str,
    status: str,
    account: CampaignAccount,
    classification: BounceClassification | None,
) -> list[dict]:
    fields = [
        {"type": "mrkdwn", "text": f"*Campaign:*\n{campaign_name or f'ID {campaign_id}'}"},
        {"type": "mrkdwn", "text": f"*Account:*\n{account.workspace_name}"},
        {"type": "mrkdwn", "text": f"*Status:*\n{status}"},
    ]
    if classification:
        bounce_text = "unknown"
        if classification.bounce_rate is not None:
            bounce_text = f"{classification.bounce_rate * 100:.2f}%"
        fields.append({"type": "mrkdwn", "text": f"*Bounce Rate:*\n{bounce_text}"})
        fields.append({"type": "mrkdwn", "text": f"*Bounced/Sent:*\n{classification.bounced}/{classification.sent}"})

    header = "Campaign Status Changed"
    if classification and classification.kind == "confirmed_bounce_protection":
        header = "Confirmed Bounce Protection Pause"
    elif classification and classification.kind == "likely_bounce_protection":
        header = "Likely Bounce Protection Pause"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header}},
        {"type": "section", "fields": fields},
    ]
    if classification and classification.is_bounce_protection:
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Threshold: {settings.BOUNCE_PROTECTION_THRESHOLD * 100:.0f}%\n"
                        "Use the action below only after reviewing the campaign and bounce cause."
                    ),
                },
            }
        )
        blocks.append(
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "static_select",
                        "action_id": "campaign_action_select",
                        "placeholder": {"type": "plain_text", "text": "Choose an action..."},
                        "options": [
                            {
                                "text": {"type": "plain_text", "text": "Resume Campaign"},
                                "value": json.dumps(
                                    {
                                        "action": "ACTIVE",
                                        "campaign_id": campaign_id,
                                        "campaign_name": campaign_name or "",
                                    }
                                ),
                            },
                            {
                                "text": {"type": "plain_text", "text": "Keep Paused"},
                                "value": json.dumps(
                                    {
                                        "action": "PAUSED",
                                        "campaign_id": campaign_id,
                                        "campaign_name": campaign_name or "",
                                    }
                                ),
                            },
                        ],
                    }
                ],
            }
        )
    return blocks


def _fallback_text(
    campaign_id: int,
    campaign_name: str,
    status: str,
    account: CampaignAccount,
    classification: BounceClassification | None,
) -> str:
    prefix = "Campaign status changed"
    if classification and classification.kind == "confirmed_bounce_protection":
        prefix = "Campaign confirmed paused by bounce protection"
    elif classification and classification.kind == "likely_bounce_protection":
        prefix = "Campaign likely paused by bounce protection"

    bounce = ""
    if classification and classification.bounce_rate is not None:
        bounce = f" Bounce rate: {classification.bounce_rate * 100:.2f}% ({classification.bounced}/{classification.sent})."
    return f"{prefix}: {campaign_name or campaign_id} [{account.workspace_name}] status={status}.{bounce}"


def _slack_bot_token() -> str | None:
    return get_secret_value("SLACK_BOT_TOKEN") or settings.SLACK_BOT_TOKEN or None


def _slack_signing_secret() -> str | None:
    return get_secret_value("SLACK_SIGNING_SECRET") or settings.SLACK_SIGNING_SECRET or None


def _slack_channel_id() -> str | None:
    return get_secret_value("SLACK_CHANNEL_ID") or settings.SLACK_CHANNEL_ID or None
