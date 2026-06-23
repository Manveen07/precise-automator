"""Fetches the inbox sheet (Google Apps Script Web App) and parses it into rows.

IO lives here only. The Web App returns a JSON 2D array (row 0 = headers). We map each
data row to a dict keyed by header name, so downstream selection is resilient to column
reordering. Results are cached briefly to avoid refetching on every page load.
"""
import time

import httpx

from app.config import settings

_CACHE_TTL_SECONDS = 300
_cache: dict[str, tuple[float, list[dict]]] = {}
_last_sync_cache: dict[str, object] = {"ts": 0.0, "value": None}
_LAST_SYNC_TAB = "Last Sync"


class InboxSheetError(RuntimeError):
    """Raised when the inbox sheet cannot be fetched or parsed."""


def fetch_last_sync(use_cache: bool = True) -> str | None:
    """Timestamp from the sheet's 'Last Sync' tab, or None. Never raises — purely informational."""
    url = settings.INBOX_SHEET_WEBAPP_URL
    if not url:
        return None
    if use_cache and _last_sync_cache["ts"] and (time.monotonic() - float(_last_sync_cache["ts"])) < _CACHE_TTL_SECONDS:
        return _last_sync_cache["value"]  # type: ignore[return-value]
    try:
        response = httpx.get(url, params={"sheet": _LAST_SYNC_TAB, "action": "read"}, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
        grid = response.json()
        value = grid[0][1] if grid and len(grid[0]) > 1 else None
    except (httpx.HTTPError, ValueError, IndexError, KeyError, TypeError):
        return None
    _last_sync_cache["ts"] = time.monotonic()
    _last_sync_cache["value"] = value
    return value


def rows_from_grid(grid: list[list]) -> list[dict]:
    if not grid or len(grid) < 2:
        return []
    headers = [str(h).strip() for h in grid[0]]
    rows: list[dict] = []
    for raw in grid[1:]:
        rows.append({headers[i]: raw[i] for i in range(min(len(headers), len(raw)))})
    return rows


def fetch_inbox_rows(tab: str | None = None, use_cache: bool = True) -> list[dict]:
    url = settings.INBOX_SHEET_WEBAPP_URL
    if not url:
        raise InboxSheetError("INBOX_SHEET_WEBAPP_URL is not configured.")
    tab = tab or settings.INBOX_SHEET_TAB

    if use_cache:
        cached = _cache.get(tab)
        if cached and (time.monotonic() - cached[0]) < _CACHE_TTL_SECONDS:
            return cached[1]

    try:
        # Apps Script /exec 302-redirects to googleusercontent.com; must follow it.
        response = httpx.get(url, params={"sheet": tab, "action": "read"}, timeout=20.0, follow_redirects=True)
        response.raise_for_status()
        grid = response.json()
    except httpx.HTTPError as exc:
        raise InboxSheetError(f"Could not reach the inbox sheet: {exc}") from exc
    except ValueError as exc:
        raise InboxSheetError("Inbox sheet did not return valid JSON (check sharing/Web App).") from exc

    if not isinstance(grid, list):
        raise InboxSheetError("Inbox sheet response was not a 2D array.")

    rows = rows_from_grid(grid)
    _cache[tab] = (time.monotonic(), rows)
    return rows
