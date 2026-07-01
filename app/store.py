"""MongoDB persistence layer.

Single collection `campaigns` holds the full lifecycle of a campaign — input,
current plan, validation errors, and (after sync) the Smartlead campaign id.
No separate drafts/runs/steps tables: revising a plan overwrites
`current_plan`, syncing updates `smartlead_campaign_id` and `status`.
"""

from datetime import datetime, timezone
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database

from app.config import settings

DISPLAY_TZ = ZoneInfo("Asia/Kolkata")


def to_display_tz(value: datetime | None) -> datetime | None:
    """Convert a stored timestamp to the display timezone for the UI.

    Mongo strips tzinfo on reads, so naive datetimes are assumed UTC
    (which is how we write them via now_utc()).
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(DISPLAY_TZ)


@lru_cache
def _client() -> MongoClient:
    return MongoClient(settings.MONGODB_URL)


def get_db() -> Database:
    return _client()[settings.MONGODB_DB_NAME]


def campaigns_collection() -> Collection:
    return get_db()["precise_automator_campaigns"]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_object_id(value: str) -> ObjectId | None:
    try:
        return ObjectId(value)
    except Exception:
        return None


def serialize_campaign(doc: dict[str, Any]) -> dict[str, Any]:
    """Convert a Mongo doc into a JSON-friendly shape (str ids, ISO timestamps)."""
    if not doc:
        return doc
    out = dict(doc)
    if "_id" in out:
        out["id"] = str(out.pop("_id"))
    for field in ("created_at", "updated_at", "synced_at"):
        if field in out and isinstance(out[field], datetime):
            out[field] = out[field].isoformat()
    return out


def insert_campaign(
    *,
    workspace_key: str,
    campaign_name: str,
    raw_input: dict,
    plan: dict,
    validation_errors: list[str],
    smartlead_campaign_id: int | None = None,
    smartlead_client_id: int | None = None,
    smartlead_client_name: str | None = None,
    smartlead_client_match: str | None = None,
    status: str | None = None,
    created_by: str | None = None,
    is_twin: bool = False,
    twin_smartlead_url: str | None = None,
) -> dict:
    now = now_utc()
    status = status or ("drafting" if validation_errors else "ready")
    doc = {
        "smartlead_campaign_id": smartlead_campaign_id,
        "smartlead_workspace": workspace_key,
        "smartlead_client_id": smartlead_client_id,
        "smartlead_client_name": smartlead_client_name,
        "smartlead_client_match": smartlead_client_match,
        "campaign_name": campaign_name,
        "raw_input": raw_input,
        "current_plan": plan,
        "validation_errors": validation_errors,
        "status": status,
        "last_sync_error": None,
        "created_by": created_by,
        "is_twin": is_twin,
        "twin_smartlead_url": twin_smartlead_url,
        "twin_last_fix": None,
        "twin_fix_running": False,
        "heyreach_campaign_id": None,
        "heyreach_campaign_url": None,
        "heyreach_status": None,
        "heyreach_creating": False,
        "heyreach_last_error": None,
        "created_at": now,
        "updated_at": now,
    }
    result = campaigns_collection().insert_one(doc)
    doc["_id"] = result.inserted_id
    return doc


def get_campaign(campaign_id: str) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one({"_id": oid})


def update_plan(campaign_id: str, plan: dict, validation_errors: list[str]) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    status_update = "drafting" if validation_errors else "ready"
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {
            "$set": {
                "current_plan": plan,
                "validation_errors": validation_errors,
                "status": status_update,
                "updated_at": now_utc(),
            }
        },
        return_document=True,
    )


def attach_smartlead(campaign_id: str, smartlead_campaign_id: int) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {
            "$set": {
                "smartlead_campaign_id": smartlead_campaign_id,
                "status": "synced",
                "last_sync_error": None,
                "synced_at": now_utc(),
                "updated_at": now_utc(),
            }
        },
        return_document=True,
    )


def mark_sync_failed(campaign_id: str, error_text: str) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {
            "$set": {
                "status": "failed",
                "last_sync_error": error_text,
                "heyreach_creating": False,
                "updated_at": now_utc(),
            }
        },
        return_document=True,
    )


def set_twin(campaign_id: str, is_twin: bool, twin_smartlead_url: str | None) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"is_twin": is_twin, "twin_smartlead_url": twin_smartlead_url, "updated_at": now_utc()}},
        return_document=True,
    )


def save_twin_fix(campaign_id: str, summary: dict) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"twin_last_fix": summary, "twin_fix_running": False, "updated_at": now_utc()}},
        return_document=True,
    )


def set_twin_fix_running(campaign_id: str, running: bool) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"twin_fix_running": running, "updated_at": now_utc()}},
        return_document=True,
    )


def set_heyreach_creating(campaign_id: str, creating: bool) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {"$set": {"heyreach_creating": creating, "updated_at": now_utc()}},
        return_document=True,
    )


def save_heyreach_result(
    campaign_id: str,
    *,
    campaign_id_value: int | None,
    url: str | None,
    status: str | None,
    error: str | None = None,
) -> dict | None:
    oid = to_object_id(campaign_id)
    if not oid:
        return None
    return campaigns_collection().find_one_and_update(
        {"_id": oid},
        {
            "$set": {
                "heyreach_campaign_id": campaign_id_value,
                "heyreach_campaign_url": url,
                "heyreach_status": status,
                "heyreach_last_error": error,
                "heyreach_creating": False,
                "updated_at": now_utc(),
            }
        },
        return_document=True,
    )


def list_recent_campaigns(limit: int = 25) -> list[dict]:
    cursor = campaigns_collection().find().sort("updated_at", -1).limit(limit)
    return list(cursor)


def delete_campaign(campaign_id: str) -> bool:
    oid = to_object_id(campaign_id)
    if not oid:
        return False
    return campaigns_collection().delete_one({"_id": oid}).deleted_count == 1
