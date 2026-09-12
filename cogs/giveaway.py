"""
cogs/giveaway.py — giveaway system with disk persistence.

Ping safety note: this is the ONE place in the whole bot that is allowed to
send a real @everyone ping (when a giveaway starts), and only because it
explicitly passes `allowed_mentions=EVERYONE_PING` on that single `.send()`
call — everywhere else, the bot's global default (bot_instance.py) silently
blocks @everyone/@here regardless of message content. The ping is further
gated by the `giveaway_everyone_ping` toggle in features.json, on top of the
existing Manage Server permission check.
"""
import asyncio
import json
import os
import random
import re
from datetime import datetime, timedelta

import discord
import pytz
from discord import app_commands

from bot_instance import bot
from config import FEATURES, GIVEAWAYS_FILE
from core.features import require_feature
from core.mention_safety import EVERYONE_PING, neutralize_mentions
from core.permissions import is_admin_user
from core.state import active_giveaways

FEATURE = "giveaways"


def save_giveaways():
    """Persist active giveaways to disk so they survive restarts."""
    serialisable = {}
    for msg_id, data in active_giveaways.items():
        serialisable[str(msg_id)] = {
            "channel_id": data["channel"].id,
            "guild_id": data["guild_id"],
            "host_id": data["host"].id,
            "prize": data["prize"],
            "winners_count": data["winners_count"],
            "ends_at": data["ends_at"].isoformat(),
            "has_timer": data.get("timer_task") is not None and not data["timer_task"].done()
                         if data.get("timer_task") else False,
        }
    try:
        with open(GIVEAWAYS_FILE, "w", encoding="utf-8") as f:
            json.dump(serialisable, f, indent=2)
    except Exception as e:
        print(f"⚠️ Failed to save giveaways: {e}")


async def restore_giveaways():
    """On startup, reload any giveaways that were active before the bot went offline."""
    if not os.path.exists(GIVEAWAYS_FILE):
        return
    try:
        with open(GIVEAWAYS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load giveaways: {e}")
        return

    now_utc = datetime.utcnow().replace(tzinfo=pytz.utc)
    restored = 0

    for msg_id_str, gdata in data.items():
        msg_id = int(msg_id_str)
        ends_at = datetime.fromisoformat(gdata["ends_at"])
        if ends_at.tzinfo is None:
            ends_at = ends_at.replace(tzinfo=pytz.utc)

        channel = bot.get_channel(gdata["channel_id"])
        if channel is None:
            continue
        guild = bot.get_guild(gdata["guild_id"])
        if guild is None:
            continue
        try:
            host = guild.get_member(gdata["host_id"]) or await guild.fetch_member(gdata["host_id"])
        except Exception:
            continue

        active_giveaways[msg_id] = {
            "channel": channel,
            "guild_id": guild.id,
            "host": host,
            "prize": gdata["prize"],
            "winners_count": gdata["winners_count"],
            "ends_at": ends_at,
            "timer_task": None,
        }

        if ends_at <= now_utc:
            asyncio.create_task(conclude_giveaway(msg_id))
        elif gdata.get("has_timer", False):
            remaining = (ends_at - now_utc).total_seconds()
            task = asyncio.create_task(giveaway_timer(msg_id, remaining))
            active_giveaways[msg_id]["timer_task"] = task

        restored += 1

    if restored:
        print(f"✅ Restored {restored} active giveaway(s) from disk.")
    save_giveaways()


def parse_duration(time_str: str) -> int | None:
    time_str = time_str.lower().strip()
    pattern = re.findall(r'(\d+)([smhd])', time_str)
    if not pattern:
        return None
    unit_map = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    seconds = sum(int(v) * unit_map[u] for v, u in pattern)
    return seconds if seconds > 0 else None


def format_duration(seconds: int) -> str:
    parts = []
    for unit, name in [(86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")]:
        if seconds >= unit:
            val = seconds // unit
            seconds %= unit
            parts.append(f"{val} {name}{'s' if val != 1 else ''}")
    return ", ".join(parts) if parts else "0 seconds"


def build_giveaway_embed(prize: str, host: discord.Member, ends_at: datetime,
                          winners_count: int, ended: bool = False,
                          winners: list[discord.Member] = None) -> discord.Embed:
    if ended:
        color = discord.Color.dark_grey()
        title = "🎉 Giveaway Ended!"
        if winners:
            winner_mentions = ", ".join(w.mention for w in winners)
            desc = (
                f"**Prize:** {prize}\n"
                f"**Winner{'s' if len(winners) > 1 else ''}:** {winner_mentions}\n"
                f"**Hosted by:** {host.mention}"
            )
        else:
            desc = (
                f"**Prize:** {prize}\n"
                f"**Winner:** No valid participants 😔\n"
                f"**Hosted by:** {host.mention}"
            )
    else:
        color = discord.Color.gold()
        title = "🎉 GIVEAWAY 🎉"
        timestamp_unix = int(ends_at.timestamp())
        desc = (
            f"**Prize:** {prize}\n"
            f"**Winners:** {winners_count}\n"
            f"**Ends:** <t:{timestamp_unix}:R> (<t:{timestamp_unix}:f>)\n"
            f"**Hosted by:** {host.mention}\n\n"
            f"React with 🎉 to enter!"
        )

    embed = discord.Embed(title=title, description=desc, color=color)
    embed.set_footer(text=f"{'Ended' if ended else 'Ends'} at")
    embed.timestamp = ends_at
    return embed


async def conclude_giveaway(message_id: int, forced: bool = False):
    """Pick winners and update the giveaway message."""
    giveaway = active_giveaways.get(message_id)
    if not giveaway:
        return

    channel: discord.TextChannel = giveaway["channel"]
    host: discord.Member = giveaway["host"]
    prize: str = giveaway["prize"]
    winners_count: int = giveaway["winners_count"]
    ends_at: datetime = giveaway["ends_at"]

    try:
        msg = await channel.fetch_message(message_id)
    except Exception:
        active_giveaways.pop(message_id, None)
        save_giveaways()
        return

    reaction_users: list[discord.Member] = []
    for reaction in msg.reactions:
        if str(reaction.emoji) == "🎉":
            async for user in reaction.users():
                if not user.bot and user.id != host.id:
                    reaction_users.append(user)
            break

    winners = random.sample(reaction_users, min(winners_count, len(reaction_users))) if reaction_users else []

    ended_embed = build_giveaway_embed(prize, host, ends_at, winners_count, ended=True, winners=winners)
    await msg.edit(embed=ended_embed)

    # `prize` is free text set by whoever ran /giveaway; neutralize it before
    # it goes into a plain (non-embed) content message. The bot's global
    # allowed_mentions default already blocks @everyone/@here here regardless,
    # this is just an extra cosmetic layer.
    safe_prize = neutralize_mentions(prize)

    if winners:
        winner_mentions = ", ".join(w.mention for w in winners)
        await channel.send(
            f"🎊 Congratulations {winner_mentions}! You won **{safe_prize}**!\n"
            f"*(Giveaway hosted by {host.mention})*"
        )
    else:
        await channel.send(f"😔 The giveaway for **{safe_prize}** ended with no valid participants.")

    active_giveaways.pop(message_id, None)
    save_giveaways()


async def giveaway_timer(message_id: int, seconds: float):
    """Wait for the duration then auto-conclude."""
    await asyncio.sleep(seconds)
    if message_id in active_giveaways:
        await conclude_giveaway(message_id)


@bot.tree.command(name="giveaway", description="Start a giveaway (Moderators only)")
@app_commands.describe(
    prize="What you're giving away",
    duration="How long to run (e.g. 10m, 2h, 1d). Use 0 for manual end with /giveaway-end",
    winners="Number of winners (default: 1)"
)
@require_feature(FEATURE)
async def giveaway_command(interaction: discord.Interaction, prize: str, duration: str, winners: int = 1):
    if not (is_admin_user(interaction.user) or interaction.user.guild_permissions.manage_guild):
        await interaction.response.send_message(
            "❌ You need **Manage Server** permission to start a giveaway!", ephemeral=True
        )
        return

    if active_giveaways:
        existing = next(iter(active_giveaways.values()))
        await interaction.response.send_message(
            f"❌ There's already an active giveaway for **{neutralize_mentions(existing['prize'])}**!\n"
            f"End it first with `/giveaway-end` before starting a new one.",
            ephemeral=True
        )
        return

    if winners < 1 or winners > 20:
        await interaction.response.send_message("❌ Winners must be between 1 and 20.", ephemeral=True)
        return

    if duration.strip() == "0":
        seconds = 0
        ends_at = datetime.utcnow().replace(tzinfo=pytz.utc) + timedelta(days=365)
    else:
        seconds = parse_duration(duration)
        if seconds is None:
            await interaction.response.send_message(
                "❌ Invalid duration! Use formats like `30s`, `10m`, `2h`, `1d`.\n"
                "Use `0` to start a giveaway with no timer (end manually with `/giveaway-end`).",
                ephemeral=True
            )
            return
        if seconds < 10:
            await interaction.response.send_message("❌ Minimum giveaway duration is 10 seconds.", ephemeral=True)
            return
        if seconds > 7 * 86400:
            await interaction.response.send_message("❌ Maximum giveaway duration is 7 days.", ephemeral=True)
            return
        ends_at = datetime.utcnow().replace(tzinfo=pytz.utc) + timedelta(seconds=seconds)

    embed = build_giveaway_embed(prize, interaction.user, ends_at, winners)

    await interaction.response.send_message("✅ Giveaway started!", ephemeral=True)

    if FEATURES.get("giveaway_everyone_ping", True):
        giveaway_msg = await interaction.channel.send("@everyone", embed=embed, allowed_mentions=EVERYONE_PING)
    else:
        giveaway_msg = await interaction.channel.send(embed=embed)

    await giveaway_msg.add_reaction("🎉")

    active_giveaways[giveaway_msg.id] = {
        "channel": interaction.channel,
        "guild_id": interaction.guild.id,
        "host": interaction.user,
        "prize": prize,
        "winners_count": winners,
        "ends_at": ends_at,
        "timer_task": None,
    }

    if seconds > 0:
        task = asyncio.create_task(giveaway_timer(giveaway_msg.id, seconds))
        active_giveaways[giveaway_msg.id]["timer_task"] = task

    save_giveaways()

    if seconds == 0:
        await interaction.followup.send(
            f"⏳ Giveaway for **{neutralize_mentions(prize)}** is live with no timer.\n"
            f"Use `/giveaway-end` to pick winners whenever you're ready.",
            ephemeral=True
        )


@bot.tree.command(name="giveaway-end", description="Force-end the active giveaway and pick winners now (Moderators only)")
@app_commands.describe(message_id="The message ID of the giveaway to end")
@require_feature(FEATURE)
async def giveaway_end_command(interaction: discord.Interaction, message_id: str):
    if not (is_admin_user(interaction.user) or interaction.user.guild_permissions.manage_guild):
        await interaction.response.send_message("❌ You need **Manage Server** permission!", ephemeral=True)
        return
    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
        return
    if mid not in active_giveaways:
        await interaction.response.send_message(
            "❌ No active giveaway found with that message ID.\n"
            "Make sure you copied the correct message ID from the giveaway post.",
            ephemeral=True
        )
        return
    task = active_giveaways[mid].get("timer_task")
    if task and not task.done():
        task.cancel()
    await interaction.response.send_message("🎲 Ending giveaway and picking winners...", ephemeral=True)
    await conclude_giveaway(mid, forced=True)


@bot.tree.command(name="giveaway-reroll", description="Reroll a winner for a recently ended giveaway (Moderators only)")
@app_commands.describe(message_id="The message ID of the ended giveaway")
@require_feature(FEATURE)
async def giveaway_reroll_command(interaction: discord.Interaction, message_id: str):
    if not (is_admin_user(interaction.user) or interaction.user.guild_permissions.manage_guild):
        await interaction.response.send_message("❌ You need **Manage Server** permission!", ephemeral=True)
        return
    try:
        mid = int(message_id)
    except ValueError:
        await interaction.response.send_message("❌ Invalid message ID.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        msg = await interaction.channel.fetch_message(mid)
    except Exception:
        await interaction.followup.send("❌ Could not find that message in this channel.", ephemeral=True)
        return
    reaction_users: list[discord.Member] = []
    for reaction in msg.reactions:
        if str(reaction.emoji) == "🎉":
            async for user in reaction.users():
                if not user.bot:
                    reaction_users.append(user)
            break
    if not reaction_users:
        await interaction.followup.send("❌ No participants found to reroll from.", ephemeral=True)
        return
    new_winner = random.choice(reaction_users)
    await interaction.channel.send(f"🔄 **Reroll!** The new winner is {new_winner.mention}! Congratulations! 🎉")
    await interaction.followup.send("✅ Rerolled successfully!", ephemeral=True)


@bot.tree.command(name="giveaway-list", description="Show the currently active giveaway")
@require_feature(FEATURE)
async def giveaway_list_command(interaction: discord.Interaction):
    if not active_giveaways:
        await interaction.response.send_message("📭 There are no active giveaways right now.", ephemeral=True)
        return
    embed = discord.Embed(title="🎉 Active Giveaway", color=discord.Color.gold())
    for msg_id, data in active_giveaways.items():
        timestamp_unix = int(data["ends_at"].timestamp())
        has_timer = data.get("timer_task") is not None and not data["timer_task"].done()
        time_str = f"<t:{timestamp_unix}:R>" if has_timer else "Manual end"
        embed.add_field(
            name=f"🎁 {data['prize']}",
            value=(
                f"**Message ID:** `{msg_id}`\n"
                f"**Channel:** {data['channel'].mention}\n"
                f"**Host:** {data['host'].mention}\n"
                f"**Winners:** {data['winners_count']}\n"
                f"**Ends:** {time_str}"
            ),
            inline=False
        )
    await interaction.response.send_message(embed=embed, ephemeral=True)
