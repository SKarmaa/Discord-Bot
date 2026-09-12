"""
cogs/moderation.py — all moderation commands (slash + prefix variants):
kick, ban, unban, mute, unmute, lock, unlock, purge, slowmode, massmove.

Ping safety note: every `reason` here is free text typed by whoever ran the
command. It is placed into embeds (which Discord never resolves mentions
from) in most cases, and additionally run through `neutralize_mentions()`
here for belt-and-braces protection, on top of the bot-wide
`allowed_mentions` default (see bot_instance.py) that blocks @everyone/@here
regardless of where the text ends up.
"""
import asyncio
import re
from datetime import timedelta

import discord
from discord import app_commands

from bot_instance import bot
from core.features import require_feature
from core.mention_safety import neutralize_mentions
from core.permissions import is_admin_user

FEATURE = "moderation"


# ── shared helper: resolve a member from mention, ID, or name search ───────
async def _resolve_member(ctx, query: str | None) -> discord.Member | None:
    if query is None:
        return ctx.author
    if ctx.message.mentions:
        return ctx.message.mentions[0]
    q = query.strip()
    if q.isdigit():
        m = ctx.guild.get_member(int(q))
        if m:
            return m
        await ctx.send("❌ No member found with that ID.")
        return None
    ql = q.lower()
    m = (
        discord.utils.find(lambda m: m.display_name.lower() == ql, ctx.guild.members)
        or discord.utils.find(lambda m: m.name.lower() == ql, ctx.guild.members)
        or discord.utils.find(lambda m: ql in m.display_name.lower(), ctx.guild.members)
        or discord.utils.find(lambda m: ql in m.name.lower(), ctx.guild.members)
    )
    if m is None:
        await ctx.send(f"❌ Couldn't find a member matching **{neutralize_mentions(q)}**.")
    return m


def _find_member_by_name(ctx_or_guild, name: str) -> discord.Member | None:
    guild = ctx_or_guild.guild if hasattr(ctx_or_guild, "guild") else ctx_or_guild
    ql = name.strip().lower()
    return (
        discord.utils.find(lambda m: m.display_name.lower() == ql, guild.members)
        or discord.utils.find(lambda m: m.name.lower() == ql, guild.members)
        or discord.utils.find(lambda m: ql in m.display_name.lower(), guild.members)
    )


# ==================== SLOWMODE ====================

@bot.tree.command(name="slowmode", description="Set slowmode for the current channel (Moderators only)")
@app_commands.describe(seconds="Slowmode delay in seconds (0 = disable, max 21600)")
@require_feature(FEATURE)
async def slowmode_command(interaction: discord.Interaction, seconds: int):
    if not (is_admin_user(interaction.user) or interaction.user.guild_permissions.manage_channels):
        await interaction.response.send_message(
            "❌ You need **Manage Channels** permission to use this!", ephemeral=True
        )
        return
    if seconds < 0 or seconds > 21600:
        await interaction.response.send_message(
            "❌ Slowmode must be between 0 and 21600 seconds (6 hours).", ephemeral=True
        )
        return
    try:
        await interaction.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await interaction.response.send_message("✅ Slowmode **disabled** for this channel.")
        else:
            minutes, secs = divmod(seconds, 60)
            time_str = f"{minutes}m {secs}s" if minutes else f"{secs}s"
            await interaction.response.send_message(f"✅ Slowmode set to **{time_str}** for this channel.")
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to edit this channel!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)


@bot.command(name="slowmode")
@require_feature(FEATURE)
async def slowmode_prefix(ctx, seconds: int = None):
    """Set slowmode for this channel. Usage: .slowmode <0-21600>"""
    if not (is_admin_user(ctx.author) or ctx.author.guild_permissions.manage_channels):
        await ctx.send("❌ You need **Manage Channels** permission!")
        return
    if seconds is None:
        await ctx.send("❌ Please specify seconds. Usage: `.slowmode 10` (0 to disable)")
        return
    if seconds < 0 or seconds > 21600:
        await ctx.send("❌ Slowmode must be between 0 and 21600 seconds (6 hours).")
        return
    try:
        await ctx.channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            await ctx.send("✅ Slowmode **disabled** for this channel.")
        else:
            minutes, secs = divmod(seconds, 60)
            time_str = f"{minutes}m {secs}s" if minutes else f"{secs}s"
            await ctx.send(f"✅ Slowmode set to **{time_str}** for this channel.")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to edit this channel!")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


# ==================== PURGE ====================

@bot.tree.command(name="purge", description="Delete messages from this channel (Moderators only)")
@app_commands.describe(amount="Number of messages to delete (1–100)")
@require_feature(FEATURE)
async def purge_command(interaction: discord.Interaction, amount: int):
    if not (is_admin_user(interaction.user) or interaction.user.guild_permissions.manage_messages):
        await interaction.response.send_message(
            "❌ You need **Manage Messages** permission to use this!", ephemeral=True
        )
        return
    if amount < 1 or amount > 100:
        await interaction.response.send_message("❌ Please choose between 1 and 100 messages.", ephemeral=True)
        return
    await interaction.response.defer(ephemeral=True)
    try:
        deleted = await interaction.channel.purge(limit=amount)
        await interaction.followup.send(f"🗑️ Deleted **{len(deleted)}** message(s).", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("❌ I don't have permission to delete messages here!", ephemeral=True)
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)


@bot.command(name="purge")
@require_feature(FEATURE)
async def purge_prefix(ctx, amount: int = None):
    """Delete messages. Usage: .purge <1-100>"""
    if not (is_admin_user(ctx.author) or ctx.author.guild_permissions.manage_messages):
        await ctx.send("❌ You need **Manage Messages** permission!")
        return
    if amount is None:
        await ctx.send("❌ Please specify how many messages to delete. Usage: `.purge 10`")
        return
    if amount < 1 or amount > 100:
        await ctx.send("❌ Please choose between 1 and 100 messages.")
        return
    try:
        deleted = await ctx.channel.purge(limit=amount + 1)  # +1 to also delete the command message itself
        msg = await ctx.send(f"🗑️ Deleted **{len(deleted) - 1}** message(s).")
        await asyncio.sleep(4)
        try:
            await msg.delete()
        except Exception:
            pass
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to delete messages here!")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


# ==================== LOCK / UNLOCK ====================

@bot.tree.command(name="lock", description="Lock the current channel so members can't send messages")
@app_commands.describe(reason="Reason for locking (optional)")
@require_feature(FEATURE)
async def lock_command(interaction: discord.Interaction, reason: str = "No reason provided"):
    if not (is_admin_user(interaction.user) or interaction.user.guild_permissions.manage_channels):
        await interaction.response.send_message("❌ You need **Manage Channels** permission!", ephemeral=True)
        return
    channel = interaction.channel
    everyone = interaction.guild.default_role
    reason = neutralize_mentions(reason)
    try:
        await channel.set_permissions(everyone, send_messages=False)
        embed = discord.Embed(
            title="🔒 Channel Locked",
            description=f"**{channel.name}** has been locked.\n**Reason:** {reason}",
            color=discord.Color.red()
        )
        embed.set_footer(text=f"Locked by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to manage this channel!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)


@bot.tree.command(name="unlock", description="Unlock the current channel")
@app_commands.describe(reason="Reason for unlocking (optional)")
@require_feature(FEATURE)
async def unlock_command(interaction: discord.Interaction, reason: str = "No reason provided"):
    if not (is_admin_user(interaction.user) or interaction.user.guild_permissions.manage_channels):
        await interaction.response.send_message("❌ You need **Manage Channels** permission!", ephemeral=True)
        return
    channel = interaction.channel
    everyone = interaction.guild.default_role
    reason = neutralize_mentions(reason)
    try:
        await channel.set_permissions(everyone, send_messages=None)
        embed = discord.Embed(
            title="🔓 Channel Unlocked",
            description=f"**{channel.name}** has been unlocked.\n**Reason:** {reason}",
            color=discord.Color.green()
        )
        embed.set_footer(text=f"Unlocked by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
    except discord.Forbidden:
        await interaction.response.send_message("❌ I don't have permission to manage this channel!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)


@bot.command(name="lock")
@require_feature(FEATURE)
async def lock_prefix(ctx, *, reason: str = None):
    """Lock the current channel. Usage: .lock [reason]"""
    if not (is_admin_user(ctx.author) or ctx.author.guild_permissions.manage_channels):
        await ctx.send("❌ You need **Manage Channels** permission!")
        return
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=False)
        desc = f"**{ctx.channel.name}** has been locked."
        if reason:
            desc += f"\n**Reason:** {neutralize_mentions(reason)}"
        embed = discord.Embed(title="🔒 Channel Locked", description=desc, color=discord.Color.red())
        embed.set_footer(text=f"Locked by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to manage this channel!")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


@bot.command(name="unlock")
@require_feature(FEATURE)
async def unlock_prefix(ctx, *, reason: str = None):
    """Unlock the current channel. Usage: .unlock [reason]"""
    if not (is_admin_user(ctx.author) or ctx.author.guild_permissions.manage_channels):
        await ctx.send("❌ You need **Manage Channels** permission!")
        return
    try:
        await ctx.channel.set_permissions(ctx.guild.default_role, send_messages=None)
        desc = f"**{ctx.channel.name}** has been unlocked."
        if reason:
            desc += f"\n**Reason:** {neutralize_mentions(reason)}"
        embed = discord.Embed(title="🔓 Channel Unlocked", description=desc, color=discord.Color.green())
        embed.set_footer(text=f"Unlocked by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to manage this channel!")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


# ==================== MUTE / UNMUTE (prefix) ====================

def _parse_mute_duration(token: str):
    match = re.fullmatch(r'(\d+)(s|m|h|hr|d)', token.lower())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2)
    return value * {'s': 1, 'm': 60, 'h': 3600, 'hr': 3600, 'd': 86400}[unit]


def _format_mute_duration(seconds: int) -> str:
    parts = []
    for unit, name in [(86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")]:
        if seconds >= unit:
            val = seconds // unit
            seconds %= unit
            parts.append(f"{val} {name}{'s' if val != 1 else ''}")
    return ", ".join(parts) if parts else "0 seconds"


@bot.command(name="mute")
@require_feature(FEATURE)
async def mute_prefix(ctx, *, query: str = None):
    """Timeout a user. Usage: .mute @user [duration] [reason]
    Examples: .mute @user | .mute @user 10m | .mute @user 2h spamming"""
    if not (is_admin_user(ctx.author) or ctx.author.guild_permissions.moderate_members):
        await ctx.send("❌ You need **Timeout Members** permission!")
        return
    if not query:
        await ctx.send("❌ Usage: `.mute @user [duration] [reason]`\nExamples: `.mute @user` · `.mute @user 10m` · `.mute @user 2h spamming`")
        return

    tokens = query.split()
    if ctx.message.mentions:
        target = ctx.message.mentions[0]
        remaining_tokens = [t for t in tokens if not re.fullmatch(r'<@!?\d+>', t)]
    else:
        ql = tokens[0].lower()
        target = (
            discord.utils.find(lambda m: m.display_name.lower() == ql, ctx.guild.members)
            or discord.utils.find(lambda m: m.name.lower() == ql, ctx.guild.members)
            or discord.utils.find(lambda m: ql in m.display_name.lower(), ctx.guild.members)
        )
        if target is None:
            await ctx.send(f"❌ Couldn't find a member matching **{neutralize_mentions(tokens[0])}**.")
            return
        remaining_tokens = tokens[1:]

    duration_seconds = None  # default: permanent (28 days — Discord max)
    duration_str = "permanently"
    reason = None

    if remaining_tokens:
        parsed = _parse_mute_duration(remaining_tokens[0])
        if parsed is not None:
            duration_seconds = parsed
            duration_str = _format_mute_duration(duration_seconds)
            reason_tokens = remaining_tokens[1:]
        else:
            reason_tokens = remaining_tokens
        reason = " ".join(reason_tokens) if reason_tokens else None

    if duration_seconds is not None and duration_seconds < 1:
        await ctx.send("❌ Duration must be at least 1 second.")
        return
    if duration_seconds is not None and duration_seconds > 28 * 86400:
        await ctx.send("❌ Discord's maximum timeout is 28 days.")
        return
    if target.top_role >= ctx.author.top_role and not is_admin_user(ctx.author):
        await ctx.send("❌ You can't mute someone with an equal or higher role!")
        return
    try:
        timeout_until = discord.utils.utcnow() + timedelta(seconds=duration_seconds if duration_seconds else 28 * 86400)
        await target.timeout(timeout_until, reason=reason)
        reason_display = neutralize_mentions(reason) if reason else None
        desc = f"**{target.display_name}** has been muted **{duration_str}**."
        if reason_display:
            desc += f"\n**Reason:** {reason_display}"
        embed = discord.Embed(title="🔇 User Muted", description=desc, color=discord.Color.orange())
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=f"Muted by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to timeout that user!")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


@bot.command(name="unmute")
@require_feature(FEATURE)
async def unmute_prefix(ctx, *, query: str = None):
    """Remove timeout from a user. Usage: .unmute @user"""
    if not (is_admin_user(ctx.author) or ctx.author.guild_permissions.moderate_members):
        await ctx.send("❌ You need **Timeout Members** permission!")
        return
    if not query:
        await ctx.send("❌ Usage: `.unmute @user`")
        return
    target = await _resolve_member(ctx, query)
    if target is None:
        return
    try:
        await target.timeout(None)
        embed = discord.Embed(
            title="🔊 User Unmuted",
            description=f"**{target.display_name}**'s timeout has been removed.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=f"Unmuted by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to remove that user's timeout!")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


# ==================== KICK / BAN / UNBAN (prefix) ====================

@bot.command(name="kick")
@require_feature(FEATURE)
async def kick_prefix(ctx, *, query: str = None):
    """Kick a member. Usage: .kick @user [reason]"""
    if not (is_admin_user(ctx.author) or ctx.author.guild_permissions.kick_members):
        await ctx.send("❌ You need **Kick Members** permission!")
        return
    if not query:
        await ctx.send("❌ Please mention a user or provide a name. Usage: `.kick @user [reason]`")
        return
    parts = query.split(None, 1)
    name_or_mention = parts[0]
    reason = parts[1] if len(parts) > 1 else None
    if ctx.message.mentions:
        target = ctx.message.mentions[0]
    else:
        target = _find_member_by_name(ctx, name_or_mention)
        if target is None:
            await ctx.send(f"❌ Couldn't find a member matching **{neutralize_mentions(name_or_mention)}**.")
            return
    if target == ctx.author:
        await ctx.send("❌ You can't kick yourself!")
        return
    if target.top_role >= ctx.author.top_role and not is_admin_user(ctx.author):
        await ctx.send("❌ You can't kick someone with an equal or higher role!")
        return
    try:
        reason_display = neutralize_mentions(reason) if reason else None
        embed = discord.Embed(
            title="👢 Member Kicked",
            description=(
                f"**{target.display_name}** has been kicked."
                + (f"\n**Reason:** {reason_display}" if reason_display else "")
            ),
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=f"Kicked by {ctx.author.display_name}")
        await target.kick(reason=reason)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to kick that user!")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


@bot.command(name="ban")
@require_feature(FEATURE)
async def ban_prefix(ctx, *, query: str = None):
    """Ban a member. Usage: .ban @user [reason]"""
    if not (is_admin_user(ctx.author) or ctx.author.guild_permissions.ban_members):
        await ctx.send("❌ You need **Ban Members** permission!")
        return
    if not query:
        await ctx.send("❌ Usage: `.ban @user [reason]`")
        return
    tokens = query.split(None, 1)
    name_or_mention = tokens[0]
    reason = tokens[1] if len(tokens) > 1 else None
    if ctx.message.mentions:
        target = ctx.message.mentions[0]
    else:
        target = _find_member_by_name(ctx, name_or_mention)
        if target is None:
            await ctx.send(f"❌ Couldn't find a member matching **{neutralize_mentions(name_or_mention)}**.")
            return
    if target == ctx.author:
        await ctx.send("❌ You can't ban yourself!")
        return
    if target.top_role >= ctx.author.top_role and not is_admin_user(ctx.author):
        await ctx.send("❌ You can't ban someone with an equal or higher role!")
        return
    try:
        reason_display = neutralize_mentions(reason) if reason else None
        desc = f"**{target.display_name}** has been banned."
        if reason_display:
            desc += f"\n**Reason:** {reason_display}"
        embed = discord.Embed(title="🔨 Member Banned", description=desc, color=discord.Color.red())
        embed.set_thumbnail(url=target.display_avatar.url)
        embed.set_footer(text=f"Banned by {ctx.author.display_name}")
        await target.ban(reason=reason, delete_message_days=0)
        await ctx.send(embed=embed)
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to ban that user!")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


@bot.command(name="unban")
@require_feature(FEATURE)
async def unban_prefix(ctx, user_id: str = None, *, reason: str = None):
    """Unban a user by their ID. Usage: .unban <user_id> [reason]"""
    if not (is_admin_user(ctx.author) or ctx.author.guild_permissions.ban_members):
        await ctx.send("❌ You need **Ban Members** permission!")
        return
    if not user_id:
        await ctx.send("❌ Usage: `.unban <user_id> [reason]`")
        return
    if not user_id.isdigit():
        await ctx.send("❌ Invalid user ID — must be a numeric Discord ID.\nUsage: `.unban 123456789012345678`")
        return
    try:
        user = await bot.fetch_user(int(user_id))
    except discord.NotFound:
        await ctx.send(f"❌ No Discord user found with ID `{user_id}`.")
        return
    except discord.HTTPException as e:
        await ctx.send(f"❌ Failed to fetch user: {str(e)}")
        return
    try:
        await ctx.guild.unban(user, reason=reason)
        reason_display = neutralize_mentions(reason) if reason else None
        desc = f"**{user}** (`{user.id}`) has been unbanned."
        if reason_display:
            desc += f"\n**Reason:** {reason_display}"
        embed = discord.Embed(title="✅ User Unbanned", description=desc, color=discord.Color.green())
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text=f"Unbanned by {ctx.author.display_name}")
        await ctx.send(embed=embed)
    except discord.NotFound:
        await ctx.send(f"❌ **{user}** is not banned on this server.")
    except discord.Forbidden:
        await ctx.send("❌ I don't have permission to unban users!")
    except Exception as e:
        await ctx.send(f"❌ Error: {str(e)}")


# ==================== MASS MOVE ====================

@bot.tree.command(name="massmove", description="Move all users from one voice channel to another (Moderators only)")
@app_commands.describe(
    from_channel="The voice channel to move users FROM",
    to_channel="The voice channel to move users TO"
)
@require_feature(FEATURE)
async def massmove_command(
    interaction: discord.Interaction,
    from_channel: discord.VoiceChannel,
    to_channel: discord.VoiceChannel
):
    if not (is_admin_user(interaction.user) or interaction.user.guild_permissions.move_members):
        await interaction.response.send_message(
            "❌ You need **Move Members** permission to use this!", ephemeral=True
        )
        return
    members = from_channel.members
    if not members:
        await interaction.response.send_message(
            f"❌ **{from_channel.name}** is empty — nobody to move!", ephemeral=True
        )
        return
    await interaction.response.defer(ephemeral=True)
    success, failed = 0, 0
    for member in members:
        try:
            await member.move_to(to_channel)
            success += 1
        except discord.Forbidden:
            failed += 1
        except Exception:
            failed += 1
    parts = [f"✅ Moved **{success}** member{'s' if success != 1 else ''}"]
    parts.append(f"from **{from_channel.name}** → **{to_channel.name}**")
    if failed:
        parts.append(f"\n⚠️ Failed to move **{failed}** member{'s' if failed != 1 else ''} (no permission or already disconnected)")
    await interaction.followup.send(" ".join(parts), ephemeral=False)
