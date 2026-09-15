"""core/nepali_calendar.py — Nepali (Bikram Sambat) festival calendar data/helpers.

Festival and month data is loaded from data/nepali_festivals.json via
core.data_loader so it can be edited without touching Python code.
"""
from datetime import datetime, timedelta

import pytz

try:
    import nepali_datetime
    NEPALI_DATETIME_AVAILABLE = True
    print("nepali-datetime imported successfully")
except ImportError as e:
    print(f"nepali-datetime import error: {e}")
    NEPALI_DATETIME_AVAILABLE = False

from core.data_loader import get_data


def _festivals_map() -> dict:
    """Return the (month, day) → name mapping, converting JSON string keys
    like ``"7-10"`` to tuple keys ``(7, 10)`` on the fly."""
    raw = get_data("nepali_festivals").get("festivals", {})
    result = {}
    for key, name in raw.items():
        parts = key.split("-")
        if len(parts) == 2:
            try:
                result[(int(parts[0]), int(parts[1]))] = name
            except ValueError:
                continue
    return result


def _months_list() -> list:
    """Return the list of Nepali month names."""
    return get_data("nepali_festivals").get("months", [])


# Keep module-level aliases for any code that imports these directly
# (lazily computed on first access via the functions above).
NEPALI_FESTIVALS = property(lambda self: _festivals_map())
NEPALI_MONTHS = property(lambda self: _months_list())


def get_upcoming_nepali_festivals(days_ahead: int = 30) -> list:
    """Return upcoming festivals within the next N days."""
    if not NEPALI_DATETIME_AVAILABLE:
        return []

    festivals = _festivals_map()
    months = _months_list()
    upcoming = []
    nepal_tz = pytz.timezone('Asia/Kathmandu')
    now = datetime.now(nepal_tz)

    for i in range(days_ahead):
        future_date = now + timedelta(days=i)
        try:
            nepali_d = nepali_datetime.date.from_datetime_date(future_date.date())
            key = (nepali_d.month, nepali_d.day)
            if key in festivals:
                month_name = months[nepali_d.month - 1] if nepali_d.month <= len(months) else "?"
                upcoming.append({
                    "days_away": i,
                    "bs_date": f"{month_name} {nepali_d.day}",
                    "ad_date": future_date.strftime("%b %d"),
                    "name": festivals[key]
                })
        except Exception:
            continue
    return upcoming
