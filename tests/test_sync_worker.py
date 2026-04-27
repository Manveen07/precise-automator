from types import SimpleNamespace

import httpx
import pytest
from rq.timeouts import TimerDeathPenalty

from app.workers.rq_windows import WindowsSimpleWorker
from app.workers.sync_campaign import _error_text, _extract_campaign_id, _mark_failed, _mark_succeeded


class FakeDb:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


def test_extract_campaign_id_accepts_top_level_and_wrapped_responses():
    assert _extract_campaign_id({"id": 123}) == 123
    assert _extract_campaign_id({"campaign_id": "456"}) == 456
    assert _extract_campaign_id({"data": {"id": 789}}) == 789


def test_extract_campaign_id_raises_clear_error_for_missing_or_invalid_id():
    with pytest.raises(RuntimeError, match="missing id/campaign_id"):
        _extract_campaign_id({"ok": True})

    with pytest.raises(RuntimeError, match="invalid id"):
        _extract_campaign_id({"id": 0})


def test_http_status_error_text_keeps_status_and_response_body():
    request = httpx.Request("POST", "https://server.smartlead.ai/api/v1/campaigns/create")
    response = httpx.Response(500, request=request, text="temporary failure")
    error = httpx.HTTPStatusError("server error", request=request, response=response)

    text = _error_text(error)

    assert "HTTP 500" in text
    assert "temporary failure" in text


def test_windows_worker_uses_timer_timeout_handler():
    assert WindowsSimpleWorker.death_penalty_class is TimerDeathPenalty


def test_run_status_updates_campaign_status_on_success_and_failure():
    db = FakeDb()
    request = SimpleNamespace(status="syncing")
    run = SimpleNamespace(run_status="running", error_text=None, finished_at=None, request=request)

    _mark_succeeded(db, run)

    assert run.run_status == "succeeded"
    assert request.status == "synced"
    assert run.finished_at is not None

    request.status = "syncing"
    run.finished_at = None
    _mark_failed(db, run, "boom")

    assert run.run_status == "failed"
    assert run.error_text == "boom"
    assert request.status == "failed"
    assert db.commits == 2
