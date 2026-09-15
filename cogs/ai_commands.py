"""cogs/ai_commands.py — /ai and /aistatus slash commands."""
import discord
from discord import app_commands

from bot_instance import bot
from config import FEATURES
from core.ai_client import ai_rate_limiter, query_gemini_api
from core.agentai_provider import get_agentai_v2_runtime, get_active_provider_name
from core.agentai_runtime import ToolCallStatus
from core.features import require_feature
from core.mention_safety import is_prompt_safe, sanitize_ai_response
from core.permissions import is_admin_user


@bot.tree.command(name="ai", description="Ask AI a question")
@app_commands.describe(prompt="Your question for AI")
@require_feature("ai_chat")
async def ai_command(interaction: discord.Interaction, prompt: str):
    ai_cooldown_minutes = FEATURES.get("ai_cooldown_minutes", 15)
    user_id = interaction.user.id
    is_admin = is_admin_user(interaction.user)
    if not is_admin:
        can_query, _ = ai_rate_limiter.can_query(user_id)
        if not can_query:
            remaining_time = ai_rate_limiter.get_remaining_time(user_id)
            await interaction.response.send_message(
                f"Please wait **{remaining_time}** before asking another question!\n"
                f"*Rate limit: 1 query every {ai_cooldown_minutes} minutes per user*",
                ephemeral=True
            )
            return
    if len(prompt) > 500:
        await interaction.response.send_message(
            "❌ Your question is too long! Please keep it under 500 characters.", ephemeral=True
        )
        return
    if not is_prompt_safe(prompt):
        await interaction.response.send_message(
            "❌ Ayo bro, त्यस्तो prompt chai hudaina! Afno kaam gara na yaar 😂",
            ephemeral=True
        )
        return
    await interaction.response.defer()
    if not is_admin:
        ai_rate_limiter.record_query(user_id)
    try:
        provider = FEATURES.get("ai_provider", "gemini")
        if provider == "agentai":
            runtime = get_agentai_v2_runtime()
            agent_response = await runtime.run(prompt, enable_tools=True)
            raw_response = agent_response.content
            # if agent_response.tool_calls:
            #     tool_names = [tc.tool_name for tc in agent_response.tool_calls if tc.status == ToolCallStatus.SUCCESS]
            #     if tool_names:
            #         raw_response += "\n\n🔧 *Used tools: " + ", ".join(tool_names) + "*"
        else:
            raw_response = await query_gemini_api(prompt)
        response = sanitize_ai_response(raw_response)
        if len(response) > 2000:
            await interaction.followup.send(response[:1990] + "...")
            for chunk in [response[i:i + 1990] for i in range(1990, len(response), 1990)]:
                await interaction.channel.send(chunk)
        else:
            await interaction.followup.send(response)
    except Exception as e:
        print(f"Error in AI slash command: {e}")
        await interaction.followup.send("❌ Sorry, I encountered an error. Please try again later.")


@bot.tree.command(name="aistatus", description="Check your AI cooldown status")
@require_feature("ai_chat")
async def ai_status_command(interaction: discord.Interaction):
    ai_trigger_phrase = FEATURES.get("ai_trigger_phrase", "oh kp baa")
    ai_cooldown_minutes = FEATURES.get("ai_cooldown_minutes", 15)
    user_id = interaction.user.id
    can_query, _ = ai_rate_limiter.can_query(user_id)
    if can_query:
        status = "✅ **Ready to use AI!**\nYou can ask me a question now."
    else:
        status = f"⏰ **Cooldown Active**\nYou can ask me again in **{ai_rate_limiter.get_remaining_time(user_id)}**"
    
    provider_name = get_active_provider_name()
    await interaction.response.send_message(
        f"{status}\n\n"
        f"**Active Provider:** {provider_name}\n"
        f"*Rate limit: 1 query every {ai_cooldown_minutes} minutes per user*\n"
        f"*Use: `{ai_trigger_phrase} your question` or `/ai your question`*",
        ephemeral=True
    )
