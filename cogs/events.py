"""
cogs/events.py — core bot lifecycle + message events:
  - on_ready (sync commands, restore giveaways, optionally start PC-control
    bridge / World Cup scheduler if those features are enabled)
  - on_member_join (welcome messages)
  - on_message_delete (snipe cache)
  - on_message (AI trigger phrase, natural-language AI moderation commands,
    witty trigger-word responses, random reactions)
"""
import asyncio
import random
import re
from datetime import timedelta

import discord

from bot_instance import bot
from config import CONFIG, FEATURES, TRIGGER_WORDS, WELCOME_MESSAGES, WITTY_RESPONSES
from core.ai_client import ai_rate_limiter, query_gemini_api
from core.agentai_provider import get_agentai_v2_runtime
from core.agentai_runtime import ToolCallStatus
from core.mention_safety import is_prompt_safe, neutralize_mentions, sanitize_ai_response
from core.permissions import is_admin_user
from core.state import snipe_store


@bot.event
async def on_ready():
    print(f'✅ Logged in as {bot.user.name} (ID: {bot.user.id})')
    print(f'Connected to {len(bot.guilds)} guilds')
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="If words hurt you, this isn't your place."
        )
    )

    if FEATURES.get("giveaways", True):
        from cogs.giveaway import restore_giveaways
        await restore_giveaways()

    if FEATURES.get("pc_control", False):
        from cogs.pc_control import start_ahk_server
        await start_ahk_server()
        print("🖥️  PC control (AHK bridge) armed.")
    else:
        print("🖥️  PC control disabled (features.json -> pc_control=false).")

    if FEATURES.get("worldcup_tracker", False):
        from cogs.worldcup import wc_scheduler_loop, wc_live_score_loop
        bot.loop.create_task(wc_scheduler_loop())
        bot.loop.create_task(wc_live_score_loop())
        print("⚽ World Cup scheduler + live score tracker armed.")
    else:
        print("⚽ World Cup tracker disabled (features.json -> worldcup_tracker=false).")


@bot.event
async def on_member_join(member):
    if not FEATURES.get("welcome_messages", True):
        return
    if not WELCOME_MESSAGES:
        return
    welcome_channel_id = CONFIG.get("welcome_channel_id", 0)
    if welcome_channel_id:
        channel = bot.get_channel(welcome_channel_id)
        if channel:
            message = random.choice(WELCOME_MESSAGES).format(user=member.mention)
            await channel.send(message)


@bot.event
async def on_message_delete(message):
    """Cache the last deleted message per channel for /snipe."""
    if message.author.bot:
        return
    attachment_url = None
    if message.attachments:
        attachment_url = message.attachments[0].proxy_url  # proxy_url survives deletion longer

    snipe_store[message.channel.id] = {
        "content": message.content or "",
        "author_id": message.author.id,
        "author_name": message.author.display_name,
        "author_avatar": message.author.display_avatar.url,
        "deleted_at": discord.utils.utcnow(),
        "attachment_url": attachment_url,
    }


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)
    content_lower = message.content.lower()

    ai_trigger_phrase = FEATURES.get("ai_trigger_phrase", "oh kp baa")
    ai_cooldown_minutes = FEATURES.get("ai_cooldown_minutes", 15)

    # Two ways to trigger the AI: the trigger phrase, or @-mentioning the bot.
    # `message.mention_everyone` is excluded so an @everyone/@here ping (which
    # technically "mentions" every member, including the bot) never fires this.
    phrase_triggered = content_lower.startswith(ai_trigger_phrase.lower())
    mention_triggered = (
        bot.user is not None
        and bot.user in message.mentions
        and not message.mention_everyone
    )

    if FEATURES.get("ai_chat", True) and (phrase_triggered or mention_triggered):
        user_id = message.author.id
        is_admin = is_admin_user(message.author)

        if not is_admin:
            can_query, _ = ai_rate_limiter.can_query(user_id)
            if not can_query:
                remaining_time = ai_rate_limiter.get_remaining_time(user_id)
                notice = await message.reply(
                    f"⏰ {message.author.mention} Please wait **{remaining_time}** before asking me another question!\n"
                    f"*Rate limit: 1 query every {ai_cooldown_minutes} minutes per user*"
                )
                await asyncio.sleep(8)
                try:
                    await notice.delete()
                    await message.delete()
                except Exception:
                    pass
                return

        if phrase_triggered:
            prompt = message.content[len(ai_trigger_phrase):].strip()
        else:
            # Strip every <@id> / <@!id> mention of the bot itself (it can
            # appear anywhere in the message, not just at the start), then
            # collapse extra whitespace left behind.
            prompt = re.sub(rf'<@!?{bot.user.id}>', '', message.content).strip()
            prompt = re.sub(r'\s+', ' ', prompt).strip()

        if not prompt:
            example = f"`@{bot.user.display_name} what is python?`" if mention_triggered else f"`{ai_trigger_phrase} what is python?`"
            await message.reply(f"Please ask me a question!\nExample: {example}")
            return
        if len(prompt) > 500:
            await message.reply("❌ Your question is too long! Please keep it under 500 characters.")
            return
        if not is_prompt_safe(prompt):
            await message.reply("❌ Ayo bro, त्यस्तो prompt chai hudaina! Afno kaam gara na yaar 😂")
            return
        if FEATURES.get("ai_moderation_commands", True) and any(
            word in prompt.lower() for word in ['kick', 'ban', 'mute', 'unmute']
        ):
            await handle_moderation_command(message, prompt)
            return
        if not is_admin:
            ai_rate_limiter.record_query(user_id)
        async with message.channel.typing():
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
                chunks = [response[i:i + 1990] for i in range(0, len(response), 1990)]
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply(chunk)
                    else:
                        await message.channel.send(chunk)
            else:
                await message.reply(response)

    # Trigger words — 30% chance of responding
    if FEATURES.get("trigger_word_responses", True):
        for trigger in TRIGGER_WORDS:
            if trigger.lower() in content_lower:
                responses = WITTY_RESPONSES.get(trigger, [])
                if responses and random.random() < 0.30:
                    await message.reply(random.choice(responses))
                break

    # Random reactions (1% chance)
    if FEATURES.get("random_reactions", True) and random.random() < 0.01:
        samu_id = CONFIG.get("samu_user_id", 0)
        if samu_id and message.author.id == samu_id:
            reactions = CONFIG.get("samu_tag_reactions", ["👋"])
        else:
            reactions = CONFIG.get("general_reactions", ["😊"])
        if reactions:
            try:
                await message.add_reaction(random.choice(reactions))
            except Exception:
                pass


async def handle_moderation_command(message, prompt):
    """Natural-language kick/ban/mute/unmute triggered via the AI trigger phrase,
    e.g. `oh kp baa kick @user because they were spamming`.

    NOTE ON SAFETY: `reason` below comes straight from user-typed free text, and
    is echoed back in a plain message reply. It is passed through
    `neutralize_mentions()` and — more importantly — the bot's global
    `allowed_mentions` default (see bot_instance.py) blocks any @everyone/@here
    the user tries to sneak into a "reason", even though this reply is a raw
    reply, not an embed.
    """
    if not (is_admin_user(message.author) or message.author.guild_permissions.moderate_members):
        await message.reply("❌ You don't have permission to use moderation commands!")
        return
    mentioned_users = [u for u in message.mentions if u.id != bot.user.id]
    if not mentioned_users:
        await message.reply("❌ Please mention a user to moderate!")
        return
    target = mentioned_users[0]
    raw_reason = re.sub(r'(kick|ban|mute|unmute)\s*<@!?\d+>\s*', '', prompt, flags=re.IGNORECASE).strip()
    reason = neutralize_mentions(raw_reason or "No reason provided")
    try:
        if 'kick' in prompt.lower():
            await target.kick(reason=reason)
            await message.reply(f"✅ Kicked {target.mention}. Reason: {reason}")
        elif 'ban' in prompt.lower():
            await target.ban(reason=reason)
            await message.reply(f"✅ Banned {target.mention}. Reason: {reason}")
        elif 'mute' in prompt.lower():
            await target.timeout(timedelta(minutes=5), reason=reason)
            await message.reply(f"✅ Muted {target.mention} for 5 minutes. Reason: {reason}")
        elif 'unmute' in prompt.lower():
            await target.timeout(None, reason=reason)
            await message.reply(f"✅ Unmuted {target.mention}")
    except discord.Forbidden:
        await message.reply("❌ I don't have permission to do that!")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")
