"""core/permissions.py — admin/permission helpers shared across cogs."""
import discord

from config import FEATURES


def get_special_admin_id() -> int:
    return int(FEATURES.get("special_admin_id", 0) or 0)


def is_admin_user(user: "discord.Member | discord.User") -> bool:
    """Return True if the user is the special admin or has the administrator permission."""
    special_id = get_special_admin_id()
    if special_id and user.id == special_id:
        return True
    if isinstance(user, discord.Member):
        return user.guild_permissions.administrator
    return False
