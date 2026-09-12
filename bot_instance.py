"""
bot_instance.py — creates the single shared `discord.ext.commands.Bot`
instance that every cog imports and attaches commands/listeners to.

This is also where the bot-wide mass-ping safety net is switched on: see
core/mention_safety.py for the full explanation. In short — `allowed_mentions`
below is the DEFAULT for every message the bot ever sends. It structurally
blocks @everyone/@here/role pings everywhere, unless a specific .send() call
explicitly opts back in (only done in cogs/giveaway.py and, if enabled in
features.json, cogs/admin_broadcast.py).
"""
import discord
from discord.ext import commands

from config import FEATURES
from core.mention_safety import RESTRICTED_MENTIONS

intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(
    command_prefix=FEATURES.get("command_prefix", "."),
    intents=intents,
    help_command=None,
    allowed_mentions=RESTRICTED_MENTIONS,
)
