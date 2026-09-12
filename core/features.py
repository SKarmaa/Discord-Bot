"""
core/features.py — a tiny decorator that gates a command behind a
features.json toggle. Works for both slash commands (discord.Interaction as
first argument) and prefix commands (commands.Context as first argument).

Usage:

    @bot.tree.command(name="8ball", ...)
    @require_feature("fun_games")
    async def eightball_command(interaction: discord.Interaction, question: str):
        ...

    @bot.command(name="mute")
    @require_feature("moderation")
    async def mute_prefix(ctx, *, query: str = None):
        ...

Put `@require_feature(...)` directly under (closer to) the function, i.e.
*above* it in the decorator stack but *below* `@bot.tree.command(...)` /
`@bot.command(...)`, so discord.py wraps the already-feature-gated coroutine.
"""
import functools

import discord

from config import FEATURES


def require_feature(feature_key: str, disabled_message: str = None):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if not FEATURES.get(feature_key, True):
                msg = disabled_message or (
                    f"❌ This feature (`{feature_key}`) is currently turned off by the server admins."
                )
                target = args[0] if args else None
                try:
                    if isinstance(target, discord.Interaction):
                        if target.response.is_done():
                            await target.followup.send(msg, ephemeral=True)
                        else:
                            await target.response.send_message(msg, ephemeral=True)
                    elif target is not None:  # commands.Context (or similar with .send)
                        await target.send(msg)
                except Exception:
                    pass
                return
            return await func(*args, **kwargs)
        return wrapper
    return decorator
