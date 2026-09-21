"""
main.py — entry point. Loads config, creates the bot, imports every cog
(which registers their commands/listeners as an import side effect via the
shared `bot` object from bot_instance.py), then logs in and runs.

Run with:  python main.py
"""
import config  # noqa: F401  (loads bot_data.json + features.json on import)
import discord
from bot_instance import bot

# Each import below registers that module's commands/listeners onto `bot`.
# Order doesn't matter except that cogs which reference another cog's
# functions (worldcup.py -> pc_control.py) import what they need directly.
from cogs import (  # noqa: F401
    events,
    ai_commands,
    moderation,
    fun_games,
    utility,
    confession,
    giveaway,
    admin_broadcast,
    deploy,
    pc_control,
    worldcup,
    epl,
)


def main():
    if not config.DISCORD_TOKEN:
        raise SystemExit(
            "❌ No bot token found. Add TOKEN=your_bot_token to your .env file."
        )
    try:
        print("Starting Discord Bot...")
        bot.run(config.DISCORD_TOKEN)
    except discord.LoginFailure:
        print("ERROR: Invalid bot token!")
    except Exception as e:
        print(f"ERROR: Failed to start bot: {e}")


if __name__ == "__main__":
    main()