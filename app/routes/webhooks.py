from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import WebhookEvent

router = APIRouter(prefix="/api", tags=["webhooks"])


@router.post("/webhooks/smartlead")
async def smartlead_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    payload = await request.json()
    event = WebhookEvent(
        smartlead_campaign_id=payload.get("campaign_id") or payload.get("smartlead_campaign_id"),
        event_type=payload.get("event_type") or payload.get("type"),
        payload_json=payload,
    )
    db.add(event)
    db.commit()
    return {"ok": True}
