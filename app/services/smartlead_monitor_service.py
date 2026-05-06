from dataclasses import dataclass
from typing import Any

import httpx

from app.config import list_active_workspaces, settings
from app.services.smartlead_service import SmartleadService


@dataclass(frozen=True)
class CampaignAccount:
    workspace_key: str
    workspace_name: str
    api_key: str


@dataclass(frozen=True)
class BounceClassification:
    kind: str
    bounce_rate: float | None
    sent: int
    bounced: int
    explicit_reason: str | None = None

    @property
    def is_bounce_protection(self) -> bool:
        return self.kind in {"confirmed_bounce_protection", "likely_bounce_protection"}


BOUNCE_REASON_MARKERS = ("bounce", "bounced", "auto pause", "autopause", "protection")


async def find_account_for_campaign(campaign_id: int) -> tuple[CampaignAccount, dict] | tuple[None, None]:
    for workspace in list_active_workspaces():
        if not workspace or not workspace.get("api_key"):
            continue
        service = SmartleadService(workspace["api_key"])
        try:
            campaign = await service.get_campaign(campaign_id)
        except (httpx.HTTPStatusError, httpx.RequestError, ValueError):
            continue
        if isinstance(campaign, dict) and (campaign.get("id") or campaign.get("name")):
            return (
                CampaignAccount(
                    workspace_key=workspace["key"],
                    workspace_name=workspace["name"],
                    api_key=workspace["api_key"],
                ),
                campaign,
            )
    return None, None


async def classify_pause(
    *,
    campaign_id: int,
    account: CampaignAccount,
    webhook_payload: dict[str, Any],
) -> BounceClassification:
    reason = explicit_pause_reason(webhook_payload)
    analytics = await _safe_campaign_analytics(campaign_id, account.api_key)
    sent, bounced = _sent_and_bounced(analytics)
    bounce_rate = bounced / sent if sent else None

    if reason and _looks_like_bounce_reason(reason):
        return BounceClassification("confirmed_bounce_protection", bounce_rate, sent, bounced, reason)
    if bounce_rate is not None and bounce_rate >= settings.BOUNCE_PROTECTION_THRESHOLD:
        return BounceClassification("likely_bounce_protection", bounce_rate, sent, bounced, reason)
    return BounceClassification("generic_pause", bounce_rate, sent, bounced, reason)


async def resume_campaign(campaign_id: int, account: CampaignAccount) -> dict:
    return await SmartleadService(account.api_key).set_campaign_status(campaign_id, "ACTIVE")


def explicit_pause_reason(payload: dict[str, Any]) -> str | None:
    for key in (
        "pause_reason",
        "auto_pause_reason",
        "status_reason",
        "reason",
        "message",
        "status_message",
    ):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    metadata = payload.get("metadata")
    if isinstance(metadata, dict):
        for value in metadata.values():
            if isinstance(value, str) and value.strip() and _looks_like_bounce_reason(value):
                return value.strip()
    return None


def _looks_like_bounce_reason(value: str) -> bool:
    normalized = value.lower()
    return "bounce" in normalized and any(marker in normalized for marker in BOUNCE_REASON_MARKERS)


async def _safe_campaign_analytics(campaign_id: int, api_key: str) -> dict:
    try:
        analytics = await SmartleadService(api_key).get_campaign_analytics(campaign_id)
    except (httpx.HTTPStatusError, httpx.RequestError, ValueError):
        return {}
    return analytics if isinstance(analytics, dict) else {}


def _sent_and_bounced(analytics: dict) -> tuple[int, int]:
    sent = _int_value(
        analytics.get("unique_sent_count"),
        analytics.get("sent_count"),
        analytics.get("total_sent"),
    )
    bounced = _int_value(
        analytics.get("bounce_count"),
        analytics.get("total_bounces"),
        analytics.get("bounced_count"),
    )
    return sent, bounced


def _int_value(*values: object) -> int:
    for value in values:
        if value in (None, ""):
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0
