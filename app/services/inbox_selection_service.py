"""Pure inbox-selection logic. No IO — takes parsed sheet rows, returns a recommendation.

A "row" is a dict keyed by the sheet's header names (see inbox_sheet_service). Selection
follows the team's documented algorithm: filter to the client's eligible FREE inboxes,
dedup by account, size the pick by ceil(volume / avg capacity), and rank the picks.
"""
import math


def _to_float(value, default: float = 0.0) -> float:
    if value is None:
        return default
    text = str(value).strip().replace("%", "")
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        return default


def _account_id(value) -> int | None:
    try:
        return int(str(value).strip())
    except (ValueError, AttributeError):
        return None


def _neg_date_key(value) -> int:
    """ISO date -> negative integer so fresher (larger) dates sort first in ascending order."""
    try:
        year, month, day = str(value).split("-")
        return -(int(year) * 10000 + int(month) * 100 + int(day))
    except (ValueError, AttributeError):
        return 0


def _is_eligible(row: dict, client: str) -> bool:
    if str(row.get("Client", "")).strip() != client:
        return False
    if str(row.get("Availability", "")).strip().upper() != "FREE":
        return False
    if _to_float(row.get("Warmup Rep %")) < 90:
        return False
    if str(row.get("Test Status", "")).strip().lower() != "inbox":
        return False
    if _to_float(row.get("Avail. Capacity")) <= 0:
        return False
    return True


def _row_view(row: dict) -> dict:
    return {
        "account_id": _account_id(row.get("Account ID")),
        "email": row.get("Email", ""),
        "provider": row.get("Provider", ""),
        "avail_capacity": _to_float(row.get("Avail. Capacity")),
        "capacity_left": _to_float(row.get("Capacity Left")),
        "warmup_state": row.get("Warmup State", ""),
        "warmup_rep": row.get("Warmup Rep %", ""),
        "test_status": row.get("Test Status", ""),
        "test_date": row.get("Test Date", ""),
        "campaigns": _to_float(row.get("# Campaigns")),
        "availability": row.get("Availability", ""),
        "busy_reason": row.get("Busy Reason", ""),
    }


def _rank_key(row: dict):
    campaigns = _to_float(row.get("# Campaigns"))
    avail = _to_float(row.get("Avail. Capacity"))
    warmup_ramped = 0 if str(row.get("Warmup State", "")).strip().lower() == "ramped" else 1
    return (0 if campaigns == 0 else 1, -avail, warmup_ramped, _neg_date_key(row.get("Test Date")))


def _dedup_by_account(rows: list[dict]) -> list[dict]:
    """Keep one row per account id — the one with the lowest Avail. Capacity (worst case)."""
    best: dict[int, dict] = {}
    for row in rows:
        account_id = _account_id(row.get("Account ID"))
        if account_id is None:
            continue
        current = best.get(account_id)
        if current is None or _to_float(row.get("Avail. Capacity")) < _to_float(current.get("Avail. Capacity")):
            best[account_id] = row
    return list(best.values())


def select_inboxes(rows: list[dict], client: str, needed_daily_volume: int) -> dict:
    client_rows = [row for row in rows if str(row.get("Client", "")).strip() == client]
    eligible = _dedup_by_account([row for row in client_rows if _is_eligible(row, client)])
    eligible.sort(key=_rank_key)

    total_capacity = sum(_to_float(row.get("Avail. Capacity")) for row in eligible)
    if eligible:
        avg_capacity = total_capacity / len(eligible)
        inboxes_needed = min(len(eligible), max(1, math.ceil(needed_daily_volume / avg_capacity)))
    else:
        inboxes_needed = 0

    picked = eligible[:inboxes_needed]

    busy: dict[str, list[dict]] = {}
    eligible_accounts = {_account_id(row.get("Account ID")) for row in eligible}
    for row in client_rows:
        if _account_id(row.get("Account ID")) in eligible_accounts:
            continue
        reason = str(row.get("Busy Reason", "")).strip() or "other"
        busy.setdefault(reason, []).append(_row_view(row))

    return {
        "client": client,
        "needed_daily_volume": needed_daily_volume,
        "recommended": [_row_view(row) for row in picked],
        "email_account_ids": [_account_id(row.get("Account ID")) for row in picked],
        "free_pool": [_row_view(row) for row in eligible],
        "busy": busy,
        "estimated_daily_capacity": sum(_to_float(row.get("Avail. Capacity")) for row in picked),
        "provider_counts": {
            "gmail": sum(1 for row in picked if str(row.get("Provider", "")).strip().lower() == "gmail"),
            "outlook": sum(1 for row in picked if str(row.get("Provider", "")).strip().lower() == "outlook"),
        },
        "shortfall": total_capacity < needed_daily_volume,
        "eligible_count": len(eligible),
    }
