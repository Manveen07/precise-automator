from app.services.inbox_selection_service import select_inboxes


def _row(**overrides):
    base = {
        "Client": "PRECISE_LEADS",
        "Email": "a@x.com",
        "Provider": "Gmail",
        "Account ID": "1001",
        "Availability": "FREE",
        "Busy Reason": "",
        "# Campaigns": 0,
        "Avail. Capacity": 10,
        "Capacity Left": 10,
        "Warmup State": "ramped",
        "Warmup Rep %": "100%",
        "Test Status": "inbox",
        "Test Date": "2026-06-18",
    }
    base.update(overrides)
    return base


def test_eligibility_requires_free_warmup_test_and_capacity():
    rows = [
        _row(Email="ok@x.com", **{"Account ID": "1"}),
        _row(Email="busy@x.com", Availability="BUSY", **{"Account ID": "2"}),
        _row(Email="lowwarmup@x.com", **{"Account ID": "3", "Warmup Rep %": "80%"}),
        _row(Email="failtest@x.com", **{"Account ID": "4", "Test Status": "fail"}),
        _row(Email="nocap@x.com", **{"Account ID": "5", "Avail. Capacity": 0}),
    ]
    result = select_inboxes(rows, client="PRECISE_LEADS", needed_daily_volume=5)
    emails = {r["email"] for r in result["free_pool"]}
    assert emails == {"ok@x.com"}


def test_cross_client_inboxes_are_excluded():
    rows = [
        _row(Email="mine@x.com", Client="PRECISE_LEADS", **{"Account ID": "1"}),
        _row(Email="other@x.com", Client="DARLEAN", **{"Account ID": "2"}),
    ]
    result = select_inboxes(rows, client="PRECISE_LEADS", needed_daily_volume=5)
    assert [r["email"] for r in result["free_pool"]] == ["mine@x.com"]
    assert all(r["account_id"] != 2 for r in result["recommended"])


def test_dedup_by_account_id_keeps_lowest_capacity():
    rows = [
        _row(**{"Account ID": "1", "Avail. Capacity": 30, "# Campaigns": 1}),
        _row(**{"Account ID": "1", "Avail. Capacity": 10, "# Campaigns": 2}),
    ]
    result = select_inboxes(rows, client="PRECISE_LEADS", needed_daily_volume=5)
    assert len(result["free_pool"]) == 1
    assert result["free_pool"][0]["avail_capacity"] == 10


def test_inboxes_needed_is_ceil_volume_over_avg_capacity():
    # avg capacity 10, need 25 -> ceil(2.5) = 3 inboxes
    rows = [_row(Email=f"{i}@x.com", **{"Account ID": str(i), "Avail. Capacity": 10}) for i in range(1, 6)]
    result = select_inboxes(rows, client="PRECISE_LEADS", needed_daily_volume=25)
    assert len(result["recommended"]) == 3
    assert result["email_account_ids"] == [1, 2, 3]


def test_provider_counts_and_estimated_capacity():
    rows = [
        _row(Email="g@x.com", Provider="Gmail", **{"Account ID": "1", "Avail. Capacity": 10}),
        _row(Email="o@x.com", Provider="Outlook", **{"Account ID": "2", "Avail. Capacity": 20}),
    ]
    result = select_inboxes(rows, client="PRECISE_LEADS", needed_daily_volume=100)
    assert result["provider_counts"] == {"gmail": 1, "outlook": 1}
    assert result["estimated_daily_capacity"] == 30


def test_shortfall_flag_when_capacity_below_volume():
    rows = [_row(**{"Account ID": "1", "Avail. Capacity": 10})]
    result = select_inboxes(rows, client="PRECISE_LEADS", needed_daily_volume=100)
    assert result["shortfall"] is True


def test_ranking_prefers_unassigned_then_higher_capacity():
    rows = [
        _row(Email="assigned@x.com", **{"Account ID": "1", "Avail. Capacity": 40, "# Campaigns": 2}),
        _row(Email="fresh-big@x.com", **{"Account ID": "2", "Avail. Capacity": 30, "# Campaigns": 0}),
        _row(Email="fresh-small@x.com", **{"Account ID": "3", "Avail. Capacity": 5, "# Campaigns": 0}),
    ]
    result = select_inboxes(rows, client="PRECISE_LEADS", needed_daily_volume=1000)
    order = [r["email"] for r in result["recommended"]]
    assert order[0] == "fresh-big@x.com"
    assert order[1] == "fresh-small@x.com"
    assert order[2] == "assigned@x.com"


def test_busy_rows_grouped_by_reason_for_diagnostics():
    rows = [
        _row(**{"Account ID": "1"}),
        _row(Email="b1@x.com", Availability="BUSY", **{"Account ID": "2", "Busy Reason": "no_capacity"}),
        _row(Email="b2@x.com", Availability="BUSY", **{"Account ID": "3", "Busy Reason": "stale_test"}),
    ]
    result = select_inboxes(rows, client="PRECISE_LEADS", needed_daily_volume=5)
    assert set(result["busy"].keys()) == {"no_capacity", "stale_test"}
    assert result["busy"]["no_capacity"][0]["email"] == "b1@x.com"
