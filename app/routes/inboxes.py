from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["inboxes"])


class InboxRow(BaseModel):
    email: str
    provider: str
    campaign: str | None = None
    available_capacity: int
    warmup_rep: float
    test_status: str
    availability: str
    account_id: int


class InboxRecommendationRequest(BaseModel):
    target_daily_volume: int = 100
    rows: list[InboxRow]


@router.post("/inboxes/recommend")
def recommend_inboxes(payload: InboxRecommendationRequest) -> dict:
    eligible_by_account: dict[int, InboxRow] = {}
    for row in payload.rows:
        if row.availability.upper() != "FREE":
            continue
        if row.warmup_rep < 90 or row.test_status.lower() != "inbox" or row.available_capacity <= 0:
            continue
        current = eligible_by_account.get(row.account_id)
        if not current or row.available_capacity < current.available_capacity:
            eligible_by_account[row.account_id] = row

    eligible = list(eligible_by_account.values())
    if not eligible:
        return {"selected": [], "estimated_daily_capacity": 0}

    avg_capacity = sum(row.available_capacity for row in eligible) / len(eligible)
    inboxes_needed = min(30, max(1, int((payload.target_daily_volume + avg_capacity - 1) // avg_capacity)))
    eligible.sort(key=lambda row: (row.campaign not in (None, "", "N/A"), -row.available_capacity))
    selected = eligible[:inboxes_needed]
    return {
        "selected": [row.model_dump() for row in selected],
        "email_account_ids": [row.account_id for row in selected],
        "estimated_daily_capacity": sum(row.available_capacity for row in selected),
        "provider_counts": {
            "gmail": sum(1 for row in selected if row.provider.lower() == "gmail"),
            "outlook": sum(1 for row in selected if row.provider.lower() == "outlook"),
        },
    }
