"""core/ai_client.py — Per-user AI rate limiting."""
import time

from config import FEATURES


class AIRateLimiter:
    """Handle rate limiting for AI queries."""

    def __init__(self, cooldown_minutes: int = 10):
        self.cooldown_minutes = cooldown_minutes
        self.user_last_query = {}

    def can_query(self, user_id: int) -> tuple[bool, int]:
        now = time.time()
        last_query = self.user_last_query.get(user_id, 0)
        time_passed = now - last_query
        cooldown_seconds = self.cooldown_minutes * 60
        if time_passed >= cooldown_seconds:
            return True, 0
        return False, int(cooldown_seconds - time_passed)

    def record_query(self, user_id: int):
        self.user_last_query[user_id] = time.time()

    def get_remaining_time(self, user_id: int) -> str:
        _, seconds = self.can_query(user_id)
        if seconds <= 0:
            return "Ready to use"
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s" if minutes > 0 else f"{secs}s"


# Cooldown length is configurable via features.json -> "ai_cooldown_minutes"
ai_rate_limiter = AIRateLimiter(int(FEATURES.get("ai_cooldown_minutes", 15)))