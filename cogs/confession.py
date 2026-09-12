"""cogs/confession.py — anonymous confession box."""
import discord

from bot_instance import bot
from config import CONFIG
from core.features import require_feature
from core.state import confession_store

FEATURE = "confessions"


class ConfessionModal(discord.ui.Modal, title="Submit a Confession"):
    confession_text = discord.ui.TextInput(
        label="Your Confession",
        placeholder="Type your confession here... it will be anonymous.",
        style=discord.TextStyle.long,
        max_length=1000,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        confession_channel_id = CONFIG.get("confession_channel_id", 0)
        if not confession_channel_id:
            await interaction.response.send_message(
                "❌ Confession channel not configured! Ask an admin to set `confession_channel_id` in bot_data.json.",
                ephemeral=True
            )
            return
        channel = bot.get_channel(confession_channel_id)
        if not channel:
            await interaction.response.send_message("❌ Confession channel not found!", ephemeral=True)
            return

        embed = discord.Embed(
            title="🤫 Anonymous Confession",
            description=self.confession_text.value,
            color=discord.Color.dark_grey()
        )
        embed.set_footer(text="This confession was submitted anonymously.")
        embed.timestamp = discord.utils.utcnow()

        confession_msg = await channel.send(embed=embed)
        confession_store[confession_msg.id] = interaction.user.id

        await interaction.response.send_message(
            "✅ Your confession has been submitted anonymously!", ephemeral=True
        )


@bot.tree.command(name="confess", description="Submit an anonymous confession")
@require_feature(FEATURE)
async def confess_command(interaction: discord.Interaction):
    await interaction.response.send_modal(ConfessionModal())
