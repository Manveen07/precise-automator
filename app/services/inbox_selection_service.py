"""Pure inbox-selection logic. No IO — takes parsed sheet rows, returns a recommendation.

A "row" is a dict keyed by the sheet's header names (see inbox_sheet_service). Selection
follows the team's documented algorithm: filter to the client's eligible FREE inboxes,
dedup by account, rank them (unassigned + highest capacity first), then greedily pick the
fewest inboxes whose combined capacity covers the needed daily volume.
"""
from app.config import PRECISELEAD_INTERNAL_DOMAINS, subclient_inbox_domains


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
    """ISO date or other date format -> negative integer so fresher (larger) dates sort first."""
    if not value:
        return 0
    s = str(value).strip()
    for sep in ("-", "/", "."):
        if sep in s:
            parts = s.split(sep)
            if len(parts) == 3:
                try:
                    if len(parts[0]) == 4:
                        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                    elif len(parts[2]) == 4:
                        day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
                    else:
                        continue
                    return -(year * 10000 + month * 100 + day)
                except ValueError:
                    pass
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


# Sub-client domain signatures within the PreciseLead Smartlead account, sourced from
# config (the single place to edit). Email-domain substrings are a workaround until the
# sheet carries a sub-client column.
_SUBCLIENT_DOMAIN_MATCHERS = subclient_inbox_domains("preciselead")


def _domain_matches_subclient(domain: str, subclient_key: str) -> bool:
    return any(sig in domain for sig in _SUBCLIENT_DOMAIN_MATCHERS.get(subclient_key, ()))


def _is_subclient_eligible(row: dict, subclient_key: str | None) -> bool:
    if not subclient_key:
        return True
    email = str(row.get("Email", "")).lower()
    domain = email.split("@")[-1] if "@" in email else ""
    # "internal" = PreciseLeads' OWN inboxes only (preciselead domains). Inboxes that match
    # neither an active sub-client nor these are OLD clients and are excluded from everything.
    if subclient_key == "internal":
        return any(sig in domain for sig in PRECISELEAD_INTERNAL_DOMAINS)
    if subclient_key in _SUBCLIENT_DOMAIN_MATCHERS:
        return _domain_matches_subclient(domain, subclient_key)
    return True


def _get_provider(row: dict) -> str:
    prov = str(row.get("Provider", "")).strip().lower()
    if "gmail" in prov or "google" in prov:
        return "gmail"
    if "outlook" in prov or "office" in prov or "microsoft" in prov or "exchange" in prov:
        return "outlook"
    return prov


def select_inboxes(
    rows: list[dict],
    client: str,
    needed_daily_volume: int,
    subclient_key: str | None = None,
    provider_mix: dict[str, float] | None = None,
) -> dict:
    client_rows = [row for row in rows if str(row.get("Client", "")).strip() == client]
    if client == "PRECISE_LEADS":
        client_rows = [row for row in client_rows if _is_subclient_eligible(row, subclient_key)]
    eligible = _dedup_by_account([row for row in client_rows if _is_eligible(row, client)])
    eligible.sort(key=_rank_key)

    total_capacity = sum(_to_float(row.get("Avail. Capacity")) for row in eligible)
    
    picked: list[dict] = []
    
    if provider_mix:
        # Standardize provider keys in the mix
        mix = {k.lower(): weight for k, weight in provider_mix.items()}
        
        # Split eligible list into pools while keeping them sorted by rank
        pools: dict[str, list[dict]] = {}
        for row in eligible:
            prov = _get_provider(row)
            pools.setdefault(prov, []).append(row)
            
        picked_by_provider: dict[str, list[dict]] = {}
        capacity_by_provider: dict[str, float] = {}
        
        # Primary pass: fill each provider's target capacity
        for prov_key, weight in mix.items():
            target_cap = weight * needed_daily_volume
            pool = pools.get(prov_key, [])
            picked_list: list[dict] = []
            cap = 0.0
            for row in pool:
                if cap >= target_cap:
                    break
                picked_list.append(row)
                cap += _to_float(row.get("Avail. Capacity"))
            picked_by_provider[prov_key] = picked_list
            capacity_by_provider[prov_key] = cap
            
        # Combine picked lists
        for prov_key, picked_list in picked_by_provider.items():
            picked.extend(picked_list)
            
        total_picked_cap = sum(capacity_by_provider.values())
        
        # Fallback pass: if total capacity is still below needed, pick from any remaining unpicked inboxes
        if total_picked_cap < needed_daily_volume:
            picked_ids = {_account_id(row.get("Account ID")) for row in picked}
            remaining = [row for row in eligible if _account_id(row.get("Account ID")) not in picked_ids]
            
            for row in remaining:
                if total_picked_cap >= needed_daily_volume:
                    break
                picked.append(row)
                total_picked_cap += _to_float(row.get("Avail. Capacity"))
                
        # Sort the final picked list back to standard ranking order
        picked.sort(key=_rank_key)
    else:
        # Standard greedy selection
        running = 0.0
        for row in eligible:
            if running >= needed_daily_volume:
                break
            picked.append(row)
            running += _to_float(row.get("Avail. Capacity"))

    busy: dict[str, list[dict]] = {}
    eligible_accounts = {_account_id(row.get("Account ID")) for row in eligible}
    for row in client_rows:
        if _account_id(row.get("Account ID")) in eligible_accounts:
            continue
        reason = str(row.get("Busy Reason", "")).strip() or "other"
        busy.setdefault(reason, []).append(_row_view(row))

    warnings: list[str] = []
    label = f"sub-client '{subclient_key}'" if subclient_key else f"client '{client}'"
    if not eligible:
        warnings.append(
            f"No eligible FREE inboxes for {label}. Check the inbox sheet or domain mapping, "
            f"or free up busy inboxes."
        )
    elif total_capacity < needed_daily_volume:
        warnings.append(
            f"Eligible capacity ({int(total_capacity)}) is below the {needed_daily_volume} "
            f"daily volume for {label}."
        )

    return {
        "warnings": warnings,
        "client": client,
        "needed_daily_volume": needed_daily_volume,
        "recommended": [_row_view(row) for row in picked],
        "email_account_ids": [_account_id(row.get("Account ID")) for row in picked],
        "free_pool": [_row_view(row) for row in eligible],
        "busy": busy,
        "estimated_daily_capacity": sum(_to_float(row.get("Avail. Capacity")) for row in picked),
        "provider_counts": {
            "gmail": sum(1 for row in picked if _get_provider(row) == "gmail"),
            "outlook": sum(1 for row in picked if _get_provider(row) == "outlook"),
        },
        "shortfall": total_capacity < needed_daily_volume,
        "eligible_count": len(eligible),
    }
