"""Tests for HeyReach auto-creation triggered by _sync_campaign_async.

Uses asyncio.run() (not pytest-asyncio) to match the project's test convention.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bson import ObjectId


def _make_doc(with_linkedin: bool = True):
    steps = [
        {
            "step_number": 1,
            "channel": "email",
            "delay_days": 0,
            "variants": [{"variant_label": "A", "subject": "Sub", "body": "Email body."}],
        },
    ]
    if with_linkedin:
        steps.append(
            {
                "step_number": 2,
                "channel": "linkedin",
                "linkedin_subtype": "dm",
                "delay_days": 0,
                "variants": [{"variant_label": "A", "body": "LinkedIn DM!"}],
            }
        )
    return {
        "_id": ObjectId(),
        "campaign_name": "Test Campaign",
        "smartlead_workspace": "preciselead",
        "smartlead_client_name": None,
        "smartlead_campaign_id": None,
        "current_plan": {
            "workspace_key": "preciselead",
            "campaign_name": "Test Campaign",
            "sequence": steps,
            "schedule": {
                "timezone": "America/New_York",
                "days_of_the_week": [1, 2, 3, 4, 5],
                "start_hour": "09:00",
                "end_hour": "18:00",
                "min_time_btw_emails": 17,
                "max_new_leads_per_day": 100,
            },
            "settings": {
                "send_as_plain_text": True,
                "track_opens": False,
                "track_clicks": False,
                "stop_on_reply": True,
                "enable_ai_esp_matching": True,
                "auto_pause_domain_leads_on_reply": True,
                "ooo_restart_delay_days": 10,
            },
        },
        "is_twin": False,
    }


def _make_mock_smartlead_for_doc(doc, smartlead_id=123):
    """Build a mock SmartleadService whose get_sequences returns data matching the plan."""
    plan_steps = doc["current_plan"]["sequence"]
    # Build matching smartlead sequence data for email-only steps
    # (Smartlead never stores LinkedIn steps — only email steps are synced)
    sl_sequences = []
    for step in plan_steps:
        if step.get("channel", "email") != "email":
            continue
        sl_sequences.append(
            {
                "seq_number": step["step_number"],
                "sequence_variants": [
                    {
                        "variant_label": v.get("variant_label", "A"),
                        "subject": v.get("subject", ""),
                        "email_body": v.get("body", ""),
                    }
                    for v in step["variants"]
                ],
            }
        )

    mock_sl = MagicMock()
    mock_sl.create_campaign = AsyncMock(return_value={"id": smartlead_id})
    mock_sl.apply_v1_settings = AsyncMock()
    mock_sl.update_schedule = AsyncMock()
    mock_sl.update_sequences = AsyncMock()
    mock_sl.get_sequences = AsyncMock(return_value={"data": sl_sequences})
    mock_sl.attach_email_accounts = AsyncMock()
    return mock_sl


def test_sync_triggers_heyreach_when_linkedin_steps_present():
    """Smartlead sync auto-triggers HeyReach creation when plan has LinkedIn steps."""
    from app.workers.sync_campaign import _sync_campaign_async

    doc = _make_doc(with_linkedin=True)
    campaign_id = str(doc["_id"])
    mock_smartlead = _make_mock_smartlead_for_doc(doc, smartlead_id=123)

    heyreach_called_with = []

    async def fake_heyreach_async(cid):
        heyreach_called_with.append(cid)

    with (
        patch("app.workers.sync_campaign.store") as mock_store,
        patch("app.workers.sync_campaign.SmartleadService", return_value=mock_smartlead),
        patch(
            "app.workers.sync_campaign.get_workspace_config",
            return_value={"key": "preciselead", "api_key": "sl-key", "self_client_name": "PreciseLeads"},
        ),
        patch("app.workers.sync_campaign.validate_campaign_plan", return_value=[]),
        patch("app.workers.sync_campaign._sync_async", side_effect=fake_heyreach_async),
    ):
        mock_store.get_campaign.return_value = doc
        mock_store.campaigns_collection.return_value.update_one = MagicMock()
        mock_store.campaigns_collection.return_value.find_one_and_update = MagicMock()
        mock_store.attach_smartlead = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()
        mock_store.now_utc = MagicMock()

        asyncio.run(_sync_campaign_async(campaign_id))

    assert heyreach_called_with == [campaign_id]
    mock_store.set_heyreach_creating.assert_called_once_with(campaign_id, True)


def test_sync_skips_heyreach_when_no_linkedin_steps():
    """Smartlead sync does NOT trigger HeyReach when plan has no LinkedIn steps."""
    from app.workers.sync_campaign import _sync_campaign_async

    doc = _make_doc(with_linkedin=False)
    campaign_id = str(doc["_id"])
    mock_smartlead = _make_mock_smartlead_for_doc(doc, smartlead_id=456)

    heyreach_called = []

    async def fake_heyreach_async(cid):
        heyreach_called.append(cid)

    with (
        patch("app.workers.sync_campaign.store") as mock_store,
        patch("app.workers.sync_campaign.SmartleadService", return_value=mock_smartlead),
        patch(
            "app.workers.sync_campaign.get_workspace_config",
            return_value={"key": "preciselead", "api_key": "sl-key", "self_client_name": "PreciseLeads"},
        ),
        patch("app.workers.sync_campaign.validate_campaign_plan", return_value=[]),
        patch("app.workers.sync_campaign._sync_async", side_effect=fake_heyreach_async),
    ):
        mock_store.get_campaign.return_value = doc
        mock_store.campaigns_collection.return_value.update_one = MagicMock()
        mock_store.campaigns_collection.return_value.find_one_and_update = MagicMock()
        mock_store.attach_smartlead = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()
        mock_store.now_utc = MagicMock()

        asyncio.run(_sync_campaign_async(campaign_id))

    assert heyreach_called == []
    mock_store.set_heyreach_creating.assert_not_called()


def test_sync_heyreach_failure_does_not_fail_smartlead():
    """HeyReach creation error doesn't mark the Smartlead sync as failed."""
    from app.workers.sync_campaign import _sync_campaign_async

    doc = _make_doc(with_linkedin=True)
    campaign_id = str(doc["_id"])
    mock_smartlead = _make_mock_smartlead_for_doc(doc, smartlead_id=789)

    async def boom(cid):
        raise RuntimeError("HeyReach API down")

    with (
        patch("app.workers.sync_campaign.store") as mock_store,
        patch("app.workers.sync_campaign.SmartleadService", return_value=mock_smartlead),
        patch(
            "app.workers.sync_campaign.get_workspace_config",
            return_value={"key": "preciselead", "api_key": "sl-key", "self_client_name": "PreciseLeads"},
        ),
        patch("app.workers.sync_campaign.validate_campaign_plan", return_value=[]),
        patch("app.workers.sync_campaign._sync_async", side_effect=boom),
    ):
        mock_store.get_campaign.return_value = doc
        mock_store.campaigns_collection.return_value.update_one = MagicMock()
        mock_store.campaigns_collection.return_value.find_one_and_update = MagicMock()
        mock_store.attach_smartlead = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()
        mock_store.save_heyreach_result = MagicMock()
        mock_store.mark_sync_failed = MagicMock()
        mock_store.now_utc = MagicMock()

        asyncio.run(_sync_campaign_async(campaign_id))

    # Smartlead attach still called (sync succeeded)
    mock_store.attach_smartlead.assert_called_once()
    # Smartlead sync NOT marked failed
    mock_store.mark_sync_failed.assert_not_called()
    # HeyReach error stored separately
    mock_store.save_heyreach_result.assert_called_once()
    err_kwarg = mock_store.save_heyreach_result.call_args[1].get("error") or mock_store.save_heyreach_result.call_args[0]
    assert err_kwarg  # error text populated


def test_sync_always_calls_heyreach_sync_when_linkedin_steps_present():
    """Resync always calls _sync_async (which handles create-vs-update internally)."""
    from app.workers.sync_campaign import _sync_campaign_async

    doc = _make_doc(with_linkedin=True)
    doc["smartlead_campaign_id"] = 123
    doc["heyreach_campaign_id"] = 999
    doc["heyreach_status"] = "draft_created"
    campaign_id = str(doc["_id"])
    mock_smartlead = _make_mock_smartlead_for_doc(doc, smartlead_id=123)

    heyreach_called = []

    async def fake_heyreach_async(cid):
        heyreach_called.append(cid)

    with (
        patch("app.workers.sync_campaign.store") as mock_store,
        patch("app.workers.sync_campaign.SmartleadService", return_value=mock_smartlead),
        patch(
            "app.workers.sync_campaign.get_workspace_config",
            return_value={"key": "preciselead", "api_key": "sl-key", "self_client_name": "PreciseLeads"},
        ),
        patch("app.workers.sync_campaign.validate_campaign_plan", return_value=[]),
        patch("app.workers.sync_campaign._sync_async", side_effect=fake_heyreach_async),
    ):
        mock_store.get_campaign.return_value = doc
        mock_store.campaigns_collection.return_value.update_one = MagicMock()
        mock_store.attach_smartlead = MagicMock()
        mock_store.set_heyreach_creating = MagicMock()
        mock_store.now_utc = MagicMock()

        asyncio.run(_sync_campaign_async(campaign_id))

    assert heyreach_called == [campaign_id]
    mock_store.set_heyreach_creating.assert_called_once_with(campaign_id, True)
