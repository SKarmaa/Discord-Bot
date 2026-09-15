"""cogs/ai_commands.py — /ai and /aistatus slash commands with enhanced AgentAI runtime."""
import discord
from discord import app_commands

from bot_instance import bot
from config import FEATURES
from core.ai_client import ai_rate_limiter
from core.agentai_runtime import get_agentai_runtime
from core.features import require_feature
from core.mention_safety import is_prompt_safe, sanitize_ai_response
from core.permissions import is_admin_user


@bot.tree.command(name="ai", description="Ask agentAI a question")
@app_commands.describe(prompt="Your question for agentAI")
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
        # Use the new AgentAI runtime
        runtime = get_agentai_runtime()
        response = await runtime.run(prompt, enable_tools=True)
        
        # Sanitize the response
        sanitized_content = sanitize_ai_response(response.content)
        
        # Add tool call info if tools were used
        if response.tool_calls:
            tool_info = "\n\n🔧 *Used tools: " + ", ".join([tc.tool_name for tc in response.tool_calls]) + "*"
            sanitized_content += tool_info
        
        # Handle long responses
        if len(sanitized_content) > 2000:
            await interaction.followup.send(sanitized_content[:1990] + "...")
            for chunk in [sanitized_content[i:i + 1990] for i in range(1990, len(sanitized_content), 1990)]:
                await interaction.channel.send(chunk)
        else:
            await interaction.followup.send(sanitized_content)
            
    except Exception as e:
        print(f"Error in AI slash command: {e}")
        await interaction.followup.send("❌ Sorry, I encountered an error. Please try again later.")


@bot.tree.command(name="aistatus", description="Check agentAI cooldown and performance status")
@require_feature("ai_chat")
async def ai_status_command(interaction: discord.Interaction):
    ai_trigger_phrase = FEATURES.get("ai_trigger_phrase", "oh kp baa")
    ai_cooldown_minutes = FEATURES.get("ai_cooldown_minutes", 15)
    user_id = interaction.user.id
    can_query, _ = ai_rate_limiter.can_query(user_id)
    
    if can_query:
        status = "✅ **agentAI Ready**\nYou can ask me a question now."
    else:
        status = f"⏰ **Cooldown Active**\nYou can ask me again in **{ai_rate_limiter.get_remaining_time(user_id)}**"
    
    # Get runtime info
    runtime = get_agentai_runtime()
    available_tools = runtime.tool_registry.list_tools()
    cache_status = "✅ Enabled" if runtime.performance_engine else "❌ Disabled"
    
    # Get cache stats if available
    cache_stats = ""
    if runtime.performance_engine:
        stats = runtime.performance_engine.get_cache_stats()
        cache_stats = f"• Cache hits: {stats['hits']}\n• Cache misses: {stats['misses']}\n• Hit rate: {stats['hit_rate']:.1%}\n"
    
    status_message = (
        f"{status}\n\n"
        f"**🤖 agentAI Status**\n"
        f"• Rate limit: 1 query every {ai_cooldown_minutes} minutes per user\n"
        f"• Available tools: {len(available_tools)} ({', '.join(available_tools[:3])}{'...' if len(available_tools) > 3 else ''})\n"
        f"• Performance cache: {cache_status}\n"
        f"{cache_stats}"
        f"• Model: {runtime.model}\n"
        f"• Language support: English, Nepali (Latin & Devanagari)\n\n"
        f"**Usage**\n"
        f"• `{ai_trigger_phrase} your question`\n"
        f"• `/ai your question`"
    )
    
    await interaction.response.send_message(status_message, ephemeral=True)
