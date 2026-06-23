import pytest

from app.services import inbox_sheet_service
from app.services.inbox_sheet_service import InboxSheetError, rows_from_grid


def test_rows_from_grid_maps_headers_to_dicts():
    grid = [
        ["Client", "Email", "Account ID", "Availability"],
        ["PRECISE_LEADS", "a@x.com", "1001", "FREE"],
        ["DARLEAN", "b@x.com", "1002", "BUSY"],
    ]
    rows = rows_from_grid(grid)
    assert rows == [
        {"Client": "PRECISE_LEADS", "Email": "a@x.com", "Account ID": "1001", "Availability": "FREE"},
        {"Client": "DARLEAN", "Email": "b@x.com", "Account ID": "1002", "Availability": "BUSY"},
    ]


def test_rows_from_grid_handles_empty_or_header_only():
    assert rows_from_grid([]) == []
    assert rows_from_grid([["Client", "Email"]]) == []


def test_fetch_inbox_rows_requires_configured_url(monkeypatch):
    monkeypatch.setattr(inbox_sheet_service.settings, "INBOX_SHEET_WEBAPP_URL", "")
    with pytest.raises(InboxSheetError):
        inbox_sheet_service.fetch_inbox_rows(use_cache=False)


def test_fetch_inbox_rows_parses_json_grid(monkeypatch):
    monkeypatch.setattr(inbox_sheet_service.settings, "INBOX_SHEET_WEBAPP_URL", "https://script/exec")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [["Client", "Email"], ["PRECISE_LEADS", "a@x.com"]]

    def fake_get(url, params=None, timeout=None, follow_redirects=None):
        return FakeResponse()

    monkeypatch.setattr(inbox_sheet_service.httpx, "get", fake_get)
    rows = inbox_sheet_service.fetch_inbox_rows(use_cache=False)
    assert rows == [{"Client": "PRECISE_LEADS", "Email": "a@x.com"}]


def test_fetch_inbox_rows_wraps_transport_errors(monkeypatch):
    monkeypatch.setattr(inbox_sheet_service.settings, "INBOX_SHEET_WEBAPP_URL", "https://script/exec")

    def boom(url, params=None, timeout=None, follow_redirects=None):
        raise inbox_sheet_service.httpx.HTTPError("network down")

    monkeypatch.setattr(inbox_sheet_service.httpx, "get", boom)
    with pytest.raises(InboxSheetError):
        inbox_sheet_service.fetch_inbox_rows(use_cache=False)


def test_fetch_last_sync_returns_timestamp(monkeypatch):
    monkeypatch.setattr(inbox_sheet_service.settings, "INBOX_SHEET_WEBAPP_URL", "https://script/exec")

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return [["Last Synced", "2026-06-23 05:06:09"]]

    monkeypatch.setattr(inbox_sheet_service.httpx, "get", lambda url, params=None, timeout=None, follow_redirects=None: FakeResponse())
    assert inbox_sheet_service.fetch_last_sync(use_cache=False) == "2026-06-23 05:06:09"


def test_fetch_last_sync_returns_none_on_error(monkeypatch):
    monkeypatch.setattr(inbox_sheet_service.settings, "INBOX_SHEET_WEBAPP_URL", "https://script/exec")

    def boom(url, params=None, timeout=None, follow_redirects=None):
        raise inbox_sheet_service.httpx.HTTPError("down")

    monkeypatch.setattr(inbox_sheet_service.httpx, "get", boom)
    assert inbox_sheet_service.fetch_last_sync(use_cache=False) is None


def test_fetch_last_sync_none_when_unconfigured(monkeypatch):
    monkeypatch.setattr(inbox_sheet_service.settings, "INBOX_SHEET_WEBAPP_URL", "")
    assert inbox_sheet_service.fetch_last_sync(use_cache=False) is None
