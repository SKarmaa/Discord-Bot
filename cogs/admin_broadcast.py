"""
cogs/admin_broadcast.py — admin-only broadcast commands (/kpwrite,
/kpannounce) and data-reload commands (/reload, .reload-data, .words).

Ping safety note: /kpwrite sends the admin's raw text as plain message
content. Even though only admins can run it, the bot's global
allowed_mentions default (bot_instance.py) still blocks @everyone/@here on
it unless `admin_broadcast_everyone_ping` is explicitly turned on in
features.json (default: off).
"""
import discord
from discord import app_commands

from bot_instance import bot
from config import CONFIG, FEATURES, TRIGGER_WORDS, WELCOME_MESSAGES, reload_all
from core.features import require_feature
from core.mention_safety import EVERYONE_PING
from core.permissions import is_admin_user

FEATURE = "admin_broadcast"


def _broadcast_allowed_mentions():
    return EVERYONE_PING if FEATURES.get("admin_broadcast_everyone_ping", False) else None


@bot.tree.command(name="kpwrite", description="Send a message to the general channel")
@app_commands.describe(message="Message to send")
@require_feature(FEATURE)
async def kpwrite_command(interaction: discord.Interaction, message: str):
    if not is_admin_user(interaction.user):
        await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
        return
    channel_id = CONFIG.get("write_command_channel_id", 0)
    if not channel_id:
        await interaction.response.send_message("❌ Write channel not configured!", ephemeral=True)
        return
    channel = bot.get_channel(channel_id)
    if channel:
        kwargs = {}
        allowed = _broadcast_allowed_mentions()
        if allowed is not None:
            kwargs["allowed_mentions"] = allowed
        await channel.send(message, **kwargs)
        await interaction.response.send_message("✅ Message sent!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Channel not found!", ephemeral=True)


@bot.tree.command(name="kpannounce", description="Send an announcement message")
@app_commands.describe(message="Announcement message")
@require_feature(FEATURE)
async def kpannounce_command(interaction: discord.Interaction, message: str):
    if not is_admin_user(interaction.user):
        await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
        return
    general_channel_id = CONFIG.get("general_channel_id", 0)
    if not general_channel_id:
        await interaction.response.send_message("❌ General channel not configured!", ephemeral=True)
        return
    channel = bot.get_channel(general_channel_id)
    if channel:
        embed = discord.Embed(title="📢 Announcement", description=message, color=discord.Color.blue())
        # Announcement text lives in an embed, which Discord never resolves
        # mentions from, so no allowed_mentions override is needed here.
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Announcement sent!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Channel not found!", ephemeral=True)


@bot.tree.command(name="reload", description="Reload bot configuration (Admin only)")
async def reload_command(interaction: discord.Interaction):
    if interaction.user.guild_permissions.administrator:
        try:
            reload_all()
            await interaction.response.send_message(
                f"✅ Data reloaded!\n📚 {len(TRIGGER_WORDS)} trigger words\n🎉 {len(WELCOME_MESSAGES)} welcome messages"
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Reload failed: {str(e)}")
    else:
        await interaction.response.send_message("❌ Only administrators can reload data!")


@bot.command(name="words")
async def words_command(ctx):
    if TRIGGER_WORDS:
        word_list = "📝 **Current trigger words:**\n" + "\n".join([f"• {word}" for word in TRIGGER_WORDS])
        if len(word_list) > 2000:
            for chunk in [word_list[i:i + 1900] for i in range(0, len(word_list), 1900)]:
                await ctx.send(chunk)
        else:
            await ctx.send(word_list)
    else:
        await ctx.send("No trigger words configured.")


@bot.command(name="reload-data")
async def reload_data_command(ctx):
    if ctx.author.guild_permissions.administrator:
        try:
            reload_all()
            await ctx.send(
                f"✅ Data reloaded!\n📚 {len(TRIGGER_WORDS)} trigger words\n🎉 {len(WELCOME_MESSAGES)} welcome messages"
            )
        except Exception as e:
            await ctx.send(f"❌ Reload failed: {str(e)}")
    else:
        await ctx.send("❌ Only administrators can reload data!")
