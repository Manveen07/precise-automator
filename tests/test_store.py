from datetime import datetime, timezone

from app import store
from app.store import to_display_tz, DISPLAY_TZ


def test_naive_datetime_is_treated_as_utc():
    # Mongo strips tzinfo; naive datetimes must be assumed UTC.
    naive_utc = datetime(2026, 5, 4, 7, 11)  # 07:11 UTC
    result = to_display_tz(naive_utc)
    assert result.hour == 12  # 12:41 IST = UTC + 5:30
    assert result.minute == 41
    assert result.tzinfo == DISPLAY_TZ


def test_aware_datetime_is_converted_to_display_tz():
    aware_utc = datetime(2026, 5, 4, 7, 11, tzinfo=timezone.utc)
    result = to_display_tz(aware_utc)
    assert result.hour == 12
    assert result.minute == 41
    assert result.tzinfo == DISPLAY_TZ


def test_none_returns_none():
    assert to_display_tz(None) is None


def test_already_in_display_tz_is_unchanged():
    # If for some reason a datetime is already in IST, conversion should be a no-op.
    in_ist = datetime(2026, 5, 4, 12, 41, tzinfo=DISPLAY_TZ)
    result = to_display_tz(in_ist)
    assert result.hour == 12
    assert result.minute == 41


# Tests for twin field persistence
def _new(**kw):
    return store.insert_campaign(
        workspace_key="darlean",
        campaign_name="T",
        raw_input={},
        plan={},
        validation_errors=[],
        **kw,
    )


def test_insert_defaults_not_twin():
    doc = _new()
    assert doc["is_twin"] is False
    assert doc["twin_smartlead_url"] is None
    assert doc["twin_last_fix"] is None


def test_insert_twin_fields():
    doc = _new(is_twin=True, twin_smartlead_url="https://app.smartlead.ai/app/email-campaign/42/overview")
    assert doc["is_twin"] is True
    assert "42" in doc["twin_smartlead_url"]


def test_set_twin_updates_flag_and_url():
    doc = _new()
    cid = str(doc["_id"])
    updated = store.set_twin(cid, True, "https://app.smartlead.ai/app/email-campaign/42/overview")
    assert updated["is_twin"] is True
    assert "42" in updated["twin_smartlead_url"]


def test_save_twin_fix_persists_summary():
    doc = _new(is_twin=True)
    cid = str(doc["_id"])
    summary = {"total_leads": 10, "leads_changed": 3, "errors": []}
    updated = store.save_twin_fix(cid, summary)
    assert updated["twin_last_fix"]["leads_changed"] == 3


def test_set_twin_fix_running_and_save_clears_it():
    doc = store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan={},
        validation_errors=[], is_twin=True,
    )
    cid = str(doc["_id"])
    assert doc["twin_fix_running"] is False
    running = store.set_twin_fix_running(cid, True)
    assert running["twin_fix_running"] is True
    done = store.save_twin_fix(cid, {"total_leads": 5, "leads_changed": 2})
    assert done["twin_fix_running"] is False
    assert done["twin_last_fix"]["leads_changed"] == 2


def test_insert_defaults_heyreach_fields():
    doc = store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan={},
        validation_errors=[],
    )
    assert doc["heyreach_campaign_id"] is None
    assert doc["heyreach_creating"] is False
    assert doc["heyreach_status"] is None


def test_set_heyreach_creating_and_save_result():
    doc = store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan={}, validation_errors=[],
    )
    cid = str(doc["_id"])
    assert store.set_heyreach_creating(cid, True)["heyreach_creating"] is True
    done = store.save_heyreach_result(
        cid, campaign_id_value=472000, url="https://app.heyreach.io/app/campaigns/472000",
        status="draft_created",
    )
    assert done["heyreach_campaign_id"] == 472000
    assert "472000" in done["heyreach_campaign_url"]
    assert done["heyreach_status"] == "draft_created"
    assert done["heyreach_creating"] is False


def test_save_heyreach_result_records_error():
    doc = store.insert_campaign(
        workspace_key="darlean", campaign_name="T", raw_input={}, plan={}, validation_errors=[],
    )
    cid = str(doc["_id"])
    done = store.save_heyreach_result(cid, campaign_id_value=None, url=None, status="failed", error="no senders")
    assert done["heyreach_status"] == "failed"
    assert done["heyreach_last_error"] == "no senders"
    assert done["heyreach_creating"] is False
