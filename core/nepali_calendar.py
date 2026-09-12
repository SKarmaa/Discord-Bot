"""core/nepali_calendar.py — Nepali (Bikram Sambat) festival calendar data/helpers."""
from datetime import timedelta

import pytz

try:
    import nepali_datetime
    NEPALI_DATETIME_AVAILABLE = True
    print("nepali-datetime imported successfully")
except ImportError as e:
    print(f"nepali-datetime import error: {e}")
    NEPALI_DATETIME_AVAILABLE = False

from datetime import datetime

NEPALI_FESTIVALS = {
    (1, 1):   "🎉 Nepali New Year (Naya Barsha)!",
    (1, 15):  "🌸 Ubhauli Parwa",
    (3, 15):  "🌧️ Sithi Nakha",
    (5, 29):  "🐍 Nag Panchami",
    (5, 30):  "💫 Janai Purnima / Rakshya Bandhan",
    (6, 2):   "🐮 Gaijatra",
    (6, 12):  "🎭 Indra Jatra",
    (6, 18):  "🙏 Haritalika Teej",
    (6, 21):  "🌿 Rishi Panchami",
    (7, 1):   "💡 Ghatasthapana (Dashain begins)",
    (7, 8):   "🌺 Maha Ashtami",
    (7, 9):   "🐃 Maha Navami",
    (7, 10):  "🎊 Bijaya Dashami (Dashain)!",
    (7, 15):  "🌕 Kojagrat Purnima",
    (7, 29):  "🪔 Tihar begins – Kaag Tihar",
    (7, 30):  "🐕 Kukur Tihar",
    (8, 1):   "🐮 Gai Tihar & Laxmi Puja",
    (8, 2):   "🎆 Mha Puja & Gobardhan Puja",
    (8, 3):   "👫 Bhai Tika (Tihar ends)!",
    (8, 16):  "🌕 Chhath Parwa begins",
    (9, 1):   "❄️ Udhauli Parwa",
    (10, 1):  "🎋 Maghe Sankranti",
    (10, 15): "🎵 Sonam Lhosar",
    (11, 6):  "🌺 Maha Shivaratri",
    (11, 15): "🌸 Gyalpo Lhosar",
    (12, 15): "🌈 Fagu Purnima (Holi)!",
    (12, 30): "🎊 Ghode Jatra",
}

NEPALI_MONTHS = [
    "Baisakh", "Jestha", "Ashadh", "Shrawan",
    "Bhadra", "Ashwin", "Kartik", "Mangsir",
    "Poush", "Magh", "Falgun", "Chaitra"
]


def get_upcoming_nepali_festivals(days_ahead: int = 30) -> list:
    """Return upcoming festivals within the next N days."""
    if not NEPALI_DATETIME_AVAILABLE:
        return []
    upcoming = []
    nepal_tz = pytz.timezone('Asia/Kathmandu')
    now = datetime.now(nepal_tz)
    for i in range(days_ahead):
        future_date = now + timedelta(days=i)
        try:
            nepali_d = nepali_datetime.date.from_datetime_date(future_date.date())
            key = (nepali_d.month, nepali_d.day)
            if key in NEPALI_FESTIVALS:
                upcoming.append({
                    "days_away": i,
                    "bs_date": f"{NEPALI_MONTHS[nepali_d.month - 1]} {nepali_d.day}",
                    "ad_date": future_date.strftime("%b %d"),
                    "name": NEPALI_FESTIVALS[key]
                })
        except Exception:
            continue
    return upcoming
