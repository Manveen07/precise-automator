import hashlib
import hmac
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import CampaignRun, WebhookEvent

router = APIRouter(prefix="/api", tags=["webhooks"])


@router.post("/webhooks/smartlead")
async def smartlead_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    body = await request.body()
    _verify_smartlead_webhook(request, body)
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Webhook body must be valid JSON") from exc

    smartlead_campaign_id = _payload_campaign_id(payload)
    run = _campaign_run_for_smartlead_id(db, smartlead_campaign_id)
    event = WebhookEvent(
        workspace_id=run.request.workspace_id if run else None,
        smartlead_campaign_id=smartlead_campaign_id,
        event_type=payload.get("event_type") or payload.get("type"),
        payload_json=payload,
    )
    db.add(event)
    db.commit()
    return {"ok": True}


def _verify_smartlead_webhook(request: Request, body: bytes) -> None:
    secret = settings.SMARTLEAD_WEBHOOK_SECRET
    if not secret:
        if settings.APP_ENV == "local":
            return
        raise HTTPException(status_code=503, detail="Smartlead webhook secret is not configured")

    provided_secret = request.query_params.get("secret") or request.headers.get("X-Smartlead-Webhook-Secret")
    if provided_secret and hmac.compare_digest(provided_secret, secret):
        return

    signature = request.headers.get("X-Smartlead-Signature", "")
    if signature.startswith("sha256="):
        signature = signature.removeprefix("sha256=")
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(status_code=401, detail="Invalid Smartlead webhook signature")


def _payload_campaign_id(payload: dict) -> int | None:
    value = payload.get("campaign_id") or payload.get("smartlead_campaign_id")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _campaign_run_for_smartlead_id(db: Session, smartlead_campaign_id: int | None) -> CampaignRun | None:
    if smartlead_campaign_id is None:
        return None
    return (
        db.query(CampaignRun)
        .filter_by(smartlead_campaign_id=smartlead_campaign_id)
        .order_by(CampaignRun.started_at.desc().nullslast(), CampaignRun.finished_at.desc().nullslast())
        .first()
    )
