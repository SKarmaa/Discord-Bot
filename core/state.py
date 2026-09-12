"""
core/state.py — small pieces of shared in-memory state used across cogs.

Kept in one place (rather than as globals scattered in each cog) so it's
obvious what state exists and so multiple cogs can share the same dict/list
object safely (importing a mutable object gives every module the same
underlying data).
"""

# Confession storage: maps message_id -> author_id (for mod reference only, never shown publicly)
confession_store: dict[int, int] = {}

# Snipe storage: channel_id -> last deleted message data
snipe_store: dict[int, dict] = {}

# AFK storage: user_id -> {"reason": str, "time": datetime}
afk_users: dict[int, dict] = {}

# Reminder tasks: user_id -> list[asyncio.Task]
active_reminders: dict[int, list] = {}

# Giveaways: message_id -> giveaway data dict
active_giveaways: dict[int, dict] = {}
