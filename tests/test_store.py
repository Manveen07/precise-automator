from datetime import datetime, timezone

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
