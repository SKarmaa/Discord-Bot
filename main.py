import os
import json
import random
import re
import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import aiohttp
import time

try:
    import nepali_datetime
    NEPALI_DATETIME_AVAILABLE = True
    print("nepali-datetime imported successfully")
except ImportError as e:
    print(f"nepali-datetime import error: {e}")
    NEPALI_DATETIME_AVAILABLE = False

import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import pytz

# Load environment variables
load_dotenv()

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)

# Global variables for bot data
BOT_DATA = {}
WITTY_RESPONSES = {}
WELCOME_MESSAGES = []
CONFIG = {}
TRIGGER_WORDS = []

# AI Integration variables
AI_TRIGGER_PHRASE = "oh kp baa"  # AI Trigger phrase
AI_USER_COOLDOWNS = {}  # Track user cooldowns
AI_COOLDOWN_MINUTES = 5  # Cooldown time in minutes
GEMINI_API_KEY = None

class AIRateLimiter:
    """Handle rate limiting for AI queries"""
    
    def __init__(self, cooldown_minutes: int = 5):
        self.cooldown_minutes = cooldown_minutes
        self.user_last_query = {}
    
    def can_query(self, user_id: int) -> tuple[bool, int]:
        """Check if user can make a query. Returns (can_query, seconds_remaining)"""
        now = time.time()
        last_query = self.user_last_query.get(user_id, 0)
        time_passed = now - last_query
        cooldown_seconds = self.cooldown_minutes * 60
        
        if time_passed >= cooldown_seconds:
            return True, 0
        else:
            remaining = int(cooldown_seconds - time_passed)
            return False, remaining
    
    def record_query(self, user_id: int):
        """Record that user made a query"""
        self.user_last_query[user_id] = time.time()
    
    def get_remaining_time(self, user_id: int) -> str:
        """Get formatted remaining time string"""
        _, seconds = self.can_query(user_id)
        if seconds <= 0:
            return "Ready to use"
        
        minutes = seconds // 60
        secs = seconds % 60
        if minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

# Initialize rate limiter
ai_rate_limiter = AIRateLimiter(AI_COOLDOWN_MINUTES)

async def query_gemini_api(prompt: str) -> str:
    """Query Google's Gemini API"""
    if not GEMINI_API_KEY:
        return "❌ Gemini API key not configured. Please add GEMINI_API_KEY to your .env file."

    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    data = {
        "contents": [
            {
                "parts": [
                    {
                        "text": prompt
                    }
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.7,
            "topK": 40,
            "topP": 0.95,
            "maxOutputTokens": 1028,
        }
    }
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, headers=headers, json=data, timeout=30) as response:
                if response.status == 200:
                    result = await response.json()
                    
                    if 'candidates' in result and len(result['candidates']) > 0:
                        candidate = result['candidates'][0]
                        if 'content' in candidate and 'parts' in candidate['content']:
                            return candidate['content']['parts'][0]['text']
                        else:
                            return "❌ No content in API response"
                    else:
                        return "❌ No candidates in API response"
                else:
                    error_text = await response.text()
                    print(f"Gemini API Error {response.status}: {error_text}")
                    return f"❌ API Error: {response.status}. Please try again later."
                    
    except asyncio.TimeoutError:
        return "❌ Request timed out. Please try again."
    except Exception as e:
        print(f"Gemini API Exception: {e}")
        return f"❌ Error connecting to KP: {str(e)}"

def load_bot_data():
    """Load bot configuration and responses from JSON file"""
    global BOT_DATA, WITTY_RESPONSES, WELCOME_MESSAGES, CONFIG, TRIGGER_WORDS, GEMINI_API_KEY
    
    # Load Gemini API key from environment
    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    if not GEMINI_API_KEY:
        print("⚠️  WARNING: GEMINI_API_KEY not found in environment variables!")
        print("AI functionality will be disabled. Add GEMINI_API_KEY=your_key to .env file")
    else:
        print("✅ Gemini API key loaded successfully")
    
    try:
        with open('bot_data.json', 'r', encoding='utf-8') as f:
            BOT_DATA = json.load(f)
            
        WITTY_RESPONSES = BOT_DATA.get("witty_responses", {})
        WELCOME_MESSAGES = BOT_DATA.get("welcome_messages", [])
        CONFIG = BOT_DATA.get("bot_config", {})
        TRIGGER_WORDS = list(WITTY_RESPONSES.keys())
        
        print(f"Loaded {len(WITTY_RESPONSES)} trigger categories")
        print(f"Loaded {len(WELCOME_MESSAGES)} welcome messages")
        print(f"Loaded {len(CONFIG)} config settings")
        
    except FileNotFoundError:
        print("bot_data.json not found! Creating default configuration...")
        create_default_config()
        
    except json.JSONDecodeError as e:
        print(f"Error reading bot_data.json: {e}")
        create_default_config()

def create_default_config():
    """Create default configuration file"""
    global BOT_DATA, WITTY_RESPONSES, WELCOME_MESSAGES, CONFIG, TRIGGER_WORDS
    
    default_data = {
        "witty_responses": {
            "hello": ["Hello there!", "Hi! How are you?", "Hey! What's up?"],
            "thanks": ["You're welcome!", "No problem!", "Glad to help!"],
            "test": ["Test successful!", "All systems working!", "Everything's good!"],
            "good morning": ["Good morning!", "Morning! Have a great day!"],
            "good night": ["Good night!", "Sleep well!", "Sweet dreams!"],
            "how are you": ["I'm doing great!", "All good here!", "Living my best life!"],
            "awesome": ["That's awesome!", "Totally agree!", "Right on!"],
            "nice": ["Nice!", "Pretty cool!", "I agree!"],
            "lol": ["Glad I made you laugh!", "Haha!", "That's funny!"],
            "help": ["I'm here to help!", "What do you need?", "Happy to assist!"]
        },
        "welcome_messages": [
            "Welcome {user} to the server!",
            "Hey {user}, great to have you here!",
            "{user} has joined the party!",
            "Welcome aboard, {user}!"
        ],
        "bot_config": {
            "samu_user_id": 0,
            "welcome_channel_id": 0,
            "samu_tag_reactions": ["👋", "😊", "🎉"],
            "general_reactions": ["😂", "👍", "🤔", "😎", "🔥", "✨"],
            "write_command_user_id": 0,
            "write_command_channel_id": 0,
            "general_channel_id": 0
        }
    }
    
    with open('bot_data.json', 'w', encoding='utf-8') as f:
        json.dump(default_data, f, indent=2, ensure_ascii=False)
    
    BOT_DATA = default_data
    WITTY_RESPONSES = default_data["witty_responses"]
    WELCOME_MESSAGES = default_data["welcome_messages"]
    CONFIG = default_data["bot_config"]
    TRIGGER_WORDS = list(WITTY_RESPONSES.keys())

def reload_bot_data():
    """Reload bot data"""
    load_bot_data()

@bot.event
async def on_ready():
    """Bot startup event"""
    print(f"✅ Logged in as {bot.user.name} (ID: {bot.user.id})")
    print(f"📚 Loaded {len(TRIGGER_WORDS)} trigger words")
    print(f"🎉 Loaded {len(WELCOME_MESSAGES)} welcome messages")
    print(f"🔧 Loaded {len(CONFIG)} config settings")
    print(f"🤖 AI Trigger Phrase: '{AI_TRIGGER_PHRASE}'")
    print(f"⏰ AI Rate Limit: 1 query per {AI_COOLDOWN_MINUTES} minutes per user")
    
    # Sync commands with Discord
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    
    # Set bot status
    activity = discord.Activity(type=discord.ActivityType.listening, name=f"{AI_TRIGGER_PHRASE} for AI")
    await bot.change_presence(status=discord.Status.online, activity=activity)
    
    print("🚀 Bot is ready!")

@bot.event
async def on_member_join(member):
    """Welcome new members"""
    if not WELCOME_MESSAGES:
        return
    
    welcome_channel_id = CONFIG.get("welcome_channel_id")
    
    if welcome_channel_id and welcome_channel_id != 0:
        channel = bot.get_channel(welcome_channel_id)
        if channel:
            welcome_msg = random.choice(WELCOME_MESSAGES).format(user=member.mention)
            await channel.send(welcome_msg)

@bot.event
async def on_message(message):
    """Handle incoming messages"""
    if message.author.bot:
        return
    
    # Process commands first
    await bot.process_commands(message)
    
    content_lower = message.content.lower()
    
    # Check for moderation commands FIRST (before AI trigger)
    # Check for kick command: "oh kp baa kick @user"
    if content_lower.startswith(f"{AI_TRIGGER_PHRASE.lower()} kick"):
        await handle_moderation_command(message, "kick")
        return
    
    # Check for ban command: "oh kp baa ban @user"
    if content_lower.startswith(f"{AI_TRIGGER_PHRASE.lower()} ban"):
        await handle_moderation_command(message, "ban")
        return
    
    # Check for mute command: "oh kp baa mute @user"
    if content_lower.startswith(f"{AI_TRIGGER_PHRASE.lower()} mute"):
        await handle_moderation_command(message, "mute")
        return
    
    # Check for unmute command: "oh kp baa unmute @user"
    if content_lower.startswith(f"{AI_TRIGGER_PHRASE.lower()} unmute"):
        await handle_moderation_command(message, "unmute")
        return
    
    # NOW check for AI trigger phrase (after moderation commands)
    if message.content.lower().startswith(AI_TRIGGER_PHRASE.lower()):
        # Extract prompt after trigger phrase
        prompt = message.content[len(AI_TRIGGER_PHRASE):].strip()
        
        if not prompt:
            await message.reply("❓ Please provide a question after the trigger phrase!\nExample: `oh kp baa what is the weather?`")
            return
        
        # Check cooldown
        user_id = message.author.id
        can_query, remaining_seconds = ai_rate_limiter.can_query(user_id)
        
        if not can_query:
            remaining_time = ai_rate_limiter.get_remaining_time(user_id)
            await message.reply(f"⏰ Please wait **{remaining_time}** before asking again.\n*Rate limit: 1 query every {AI_COOLDOWN_MINUTES} minutes*")
            return
        
        # Validate prompt length
        if len(prompt) > 500:
            await message.reply("❌ Your question is too long! Please keep it under 500 characters.")
            return
        
        # Record query and send thinking message
        ai_rate_limiter.record_query(user_id)
        thinking_msg = await message.reply("🤔 *Thinking...*")
        
        try:
            # Query Gemini API
            response = await query_gemini_api(prompt)
            
            # Update message with response
            if len(response) > 2000:
                # Split long responses
                chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
                await thinking_msg.edit(content=f"💬 **Answer:**\n{chunks[0]}")
                for chunk in chunks[1:]:
                    await message.channel.send(chunk)
            else:
                await thinking_msg.edit(content=f"💬 **Answer:**\n{response}")
                
        except Exception as e:
            print(f"Error in AI message handler: {e}")
            await thinking_msg.edit(content="❌ Sorry, I encountered an error while processing your question.")
        
        return
    
    # Handle trigger word responses
    for trigger_word in TRIGGER_WORDS:
        if trigger_word.lower() in message.content.lower():
            responses = WITTY_RESPONSES.get(trigger_word, [])
            if responses:
                await message.channel.send(random.choice(responses))
                break
    
    # Special reactions for Samu
    samu_id = CONFIG.get("samu_user_id")
    if samu_id and samu_id != 0:
        if bot.user.mentioned_in(message) or str(samu_id) in message.content:
            reactions = CONFIG.get("samu_tag_reactions", ["👋"])
            for emoji in reactions:
                try:
                    await message.add_reaction(emoji)
                except:
                    pass
    
    # Random reactions
    if random.random() < 0.01:
        general_reactions = CONFIG.get("general_reactions", ["😊"])
        try:
            await message.add_reaction(random.choice(general_reactions))
        except:
            pass

async def handle_moderation_command(message, action: str):
    """Handle kick, ban, mute, and unmute commands from text messages"""
    # Check if user has permissions
    if not message.author.guild_permissions.kick_members and action in ["kick", "mute", "unmute"]:
        await message.reply(f"Muji ta ko hos ra!")
        return
    
    if not message.author.guild_permissions.ban_members and action == "ban":
        await message.reply("Muji ta ko hos ra!")
        return
    
    # Extract mentioned users
    if not message.mentions:
        await message.reply(f"Kaslai {action}?")
        return
    
    target = message.mentions[0]
    
    # Check if target is the bot itself
    if target == bot.user:
        await message.reply(f"Chuss mero!")
        return
    
    # Check if target is the command author
    if target == message.author:
        await message.reply(f"Who hurt you baby")
        return
    
    # Check role hierarchy (except for unmute)
    if action != "unmute":
        if message.guild.owner != message.author:
            if target.top_role >= message.author.top_role:
                await message.reply(f"Aukat ma bas muji!")
                return
        
        # Check if bot can perform the action
        if target.top_role >= message.guild.me.top_role:
            await message.reply(f"Mero aukat pugena :(")
            return
    
    # Extract reason (everything after the mention)
    words = message.content.split()
    reason_start = -1
    for i, word in enumerate(words):
        if word.startswith('<@') and word.endswith('>'):
            reason_start = i + 1
            break
    
    reason = " ".join(words[reason_start:]) if reason_start > 0 and reason_start < len(words) else "Manmarji"
    
    try:
        if action == "kick":
            await target.kick(reason=f"Kicked by {message.author} | {reason}")
            gif_url = "https://tenor.com/view/talakjung-v-tulke-bhag-muji-na-farkis-talke-gif-10907239385633824846"
            await message.reply(f"**{target}** khais chickne.\n**Reason:** {reason}\n\n{gif_url}")
        
        elif action == "ban":
            await target.ban(reason=f"Banned by {message.author} | {reason}")
            gif_url = "https://tenor.com/view/talakjung-v-tulke-bhag-muji-na-farkis-talke-gif-10907239385633824846"
            await message.reply(f"**{target}** khais chickne.\n**Reason:** {reason}\n\n{gif_url}")
        
        elif action == "mute":
            # Timeout for 5 minutes
            await target.timeout(timedelta(minutes=5), reason=f"Muted by {message.author} | {reason}")
            await message.reply(f"**{target}** ekchin chuplag muji.\n**Reason:** {reason}")
        
        elif action == "unmute":
            # Remove timeout
            await target.timeout(None, reason=f"Unmuted by {message.author} | {reason}")
            await message.reply(f"**{target}** la bol aba.\n**Reason:** {reason}")
        
        # Log the action
        print(f"[MODERATION] {action.upper()}: {target} ({target.id}) by {message.author} ({message.author.id}) - Reason: {reason}")
        
    except discord.Forbidden:
        await message.reply(f"❌ I don't have permission to {action} this user!")
    except discord.HTTPException as e:
        await message.reply(f"❌ Failed to {action} the user: {str(e)}")
    except Exception as e:
        await message.reply(f"❌ An error occurred: {str(e)}")
        print(f"Error in {action} command: {e}")

# Slash Commands
@bot.tree.command(name="kpwrite", description="Send a message as KP (authorized users only)")
@app_commands.describe(message="The message to send")
async def kpwrite_command(interaction: discord.Interaction, message: str):
    """Send message as KP"""
    authorized_user_id = CONFIG.get("write_command_user_id")
    target_channel_id = CONFIG.get("write_command_channel_id")
    
    if not authorized_user_id or authorized_user_id == 0:
        await interaction.response.send_message("❌ This command is not configured!", ephemeral=True)
        return
    
    if interaction.user.id != authorized_user_id:
        await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
        return
    
    if not target_channel_id or target_channel_id == 0:
        await interaction.response.send_message("❌ Target channel not configured!", ephemeral=True)
        return
    
    channel = bot.get_channel(target_channel_id)
    if not channel:
        await interaction.response.send_message("❌ Target channel not found!", ephemeral=True)
        return
    
    try:
        await channel.send(message)
        await interaction.response.send_message(f"✅ Message sent to {channel.mention}!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to send message: {str(e)}", ephemeral=True)

@bot.tree.command(name="kpannounce", description="Send an announcement (authorized users only)")
@app_commands.describe(message="The announcement message")
async def kpannounce_command(interaction: discord.Interaction, message: str):
    """Send announcement"""
    authorized_user_id = CONFIG.get("write_command_user_id")
    target_channel_id = CONFIG.get("general_channel_id")
    
    if not authorized_user_id or authorized_user_id == 0:
        await interaction.response.send_message("❌ This command is not configured!", ephemeral=True)
        return
    
    if interaction.user.id != authorized_user_id:
        await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
        return
    
    if not target_channel_id or target_channel_id == 0:
        await interaction.response.send_message("❌ Target channel not configured!", ephemeral=True)
        return
    
    channel = bot.get_channel(target_channel_id)
    if not channel:
        await interaction.response.send_message("❌ Target channel not found!", ephemeral=True)
        return
    
    announcement = f"📢 **Announcement** 📢\n\n{message}\n\n— Management"
    
    try:
        await channel.send(announcement)
        await interaction.response.send_message(f"✅ Announcement sent to {channel.mention}!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to send announcement: {str(e)}", ephemeral=True)

@bot.tree.command(name="kpprotest", description="Send a protest message (authorized users only)")
async def kpprotest_command(interaction: discord.Interaction):
    """Send protest message"""
    authorized_user_id = CONFIG.get("write_command_user_id")
    target_channel_id = CONFIG.get("general_channel_id")
    
    if not authorized_user_id or authorized_user_id == 0:
        await interaction.response.send_message("❌ This command is not configured!", ephemeral=True)
        return
    
    if interaction.user.id != authorized_user_id:
        await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
        return
    
    if not target_channel_id or target_channel_id == 0:
        await interaction.response.send_message("❌ Target channel not configured!", ephemeral=True)
        return
    
    channel = bot.get_channel(target_channel_id)
    if not channel:
        await interaction.response.send_message("❌ Target channel not found!", ephemeral=True)
        return
    
    protest = "🚨 **PROTEST** 🚨\n\nThis is an official protest message!\n\n— KP Bot"
    
    try:
        await channel.send(protest)
        await interaction.response.send_message(f"✅ Protest sent to {channel.mention}!", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"❌ Failed to send protest: {str(e)}", ephemeral=True)

@bot.tree.command(name="ai", description="Ask KP a question (rate limited)")
@app_commands.describe(prompt="Your question for KP")
async def ai_command(interaction: discord.Interaction, prompt: str):
    """AI slash command with rate limiting"""
    await interaction.response.defer()
    
    try:
        user_id = interaction.user.id
        
        # Check cooldown
        can_query, remaining_seconds = ai_rate_limiter.can_query(user_id)
        
        if not can_query:
            remaining_time = ai_rate_limiter.get_remaining_time(user_id)
            await interaction.followup.send(
                f"⏰ Please wait **{remaining_time}** before asking again.\n*Rate limit: 1 query every {AI_COOLDOWN_MINUTES} minutes per user*"
            )
            return
        
        # Validate prompt
        if len(prompt) > 500:
            await interaction.followup.send("❌ Your question is too long! Please keep it under 500 characters.")
            return
        
        # Record query
        ai_rate_limiter.record_query(user_id)
        
        # Query Gemini
        response = await query_gemini_api(prompt)
        
        # Send response
        if len(response) > 2000:
            chunks = [response[i:i+1900] for i in range(0, len(response), 1900)]
            await interaction.followup.send(f"💬 **Answer:**\n{chunks[0]}")
            for chunk in chunks[1:]:
                await interaction.channel.send(chunk)
        else:
            await interaction.followup.send(f"💬 **Answer:**\n{response}")
            
    except Exception as e:
        print(f"Error in AI slash command: {e}")
        await interaction.followup.send("❌ Sorry, I encountered an error while processing your request. Please try again later.")

@bot.tree.command(name="aistatus", description="Check your AI cooldown status")
async def ai_status_command(interaction: discord.Interaction):
    """Check AI cooldown status"""
    user_id = interaction.user.id
    can_query, remaining_seconds = ai_rate_limiter.can_query(user_id)
    
    if can_query:
        status = "✅ **Ready to use AI!**\nYou can ask me a question now."
    else:
        remaining_time = ai_rate_limiter.get_remaining_time(user_id)
        status = f"⏰ **Cooldown Active**\nYou can ask me again in **{remaining_time}**"
    
    await interaction.response.send_message(
        f"{status}\n\n*Rate limit: 1 query every {AI_COOLDOWN_MINUTES} minutes per user*\n*Use: `{AI_TRIGGER_PHRASE} your question` or `/ai your question`*",
        ephemeral=True
    )

@bot.tree.command(name="ping", description="Check bot status")
async def ping_command(interaction: discord.Interaction):
    """Simple ping command"""
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")

@bot.tree.command(name="date", description="Get current date and time in both English and Nepali (Bikram Sambat)")
async def date_command(interaction: discord.Interaction):
    """Get current date/time with proper Nepali Bikram Sambat conversion"""
    await interaction.response.defer()
    
    try:
        nepal_tz = pytz.timezone('Asia/Kathmandu')
        now = datetime.now(nepal_tz)
        
        english_date = now.strftime("%A, %B %d, %Y")
        english_time = now.strftime("%I:%M %p")
        
        nepali_date_str = "BS conversion unavailable"
        
        if NEPALI_DATETIME_AVAILABLE:
            try:
                nepali_dt = nepali_datetime.datetime.from_datetime_datetime(now)
                nepali_date_str = nepali_dt.strftime("%A, %d %B %Y")
                print(f"Nepali datetime conversion successful: {nepali_date_str}")
            except Exception as e:
                print(f"Nepali datetime conversion error: {e}")
                try:
                    nepali_d = nepali_datetime.date.from_datetime_date(now.date())
                    nepali_date_str = nepali_d.strftime("%A, %d %B %Y")
                    print(f"Nepali date conversion successful: {nepali_date_str}")
                except Exception as e2:
                    print(f"Nepali date conversion error: {e2}")
                    nepali_date_str = "BS conversion failed"
        
        if "conversion" in nepali_date_str.lower():
            nepali_days = {
                'Monday': 'सोमबार', 'Tuesday': 'मंगलबार', 'Wednesday': 'बुधबार',
                'Thursday': 'बिहिबार', 'Friday': 'शुक्रबार', 'Saturday': 'शनिबार',
                'Sunday': 'आइतबार'
            }
            weekday_nepali = nepali_days.get(now.strftime("%A"), now.strftime("%A"))
            nepali_date_str = f"{weekday_nepali} (BS date conversion issue)"
        
        response = f"""📅 **Current Date & Time:**

🇬🇧 **English (AD):** {english_date}
🇳🇵 **Nepali (BS):** {nepali_date_str}

🕐 **Time:** {english_time} (Nepal Time)
🌍 **Timezone:** Asia/Kathmandu (NPT)"""
        
        await interaction.followup.send(response)
        
    except Exception as e:
        print(f"Date command error: {e}")
        try:
            await interaction.followup.send(f"❌ Error getting date: {str(e)}")
        except:
            print(f"Failed to send error message: {e}")

@bot.tree.command(name="serverinfo", description="Get server information")
async def serverinfo_command(interaction: discord.Interaction):
    """Display server information"""
    guild = interaction.guild
    
    info = f"""🏰 **Server Information:**

**Name:** {guild.name}
**ID:** {guild.id}
**Owner:** {guild.owner.mention if guild.owner else 'Unknown'}
**Created:** {guild.created_at.strftime('%B %d, %Y')}
**Members:** {guild.member_count}
**Text Channels:** {len(guild.text_channels)}
**Voice Channels:** {len(guild.voice_channels)}
**Boost Level:** {guild.premium_tier}
**Boosts:** {guild.premium_subscription_count}"""
    
    await interaction.response.send_message(info)

@bot.tree.command(name="reload", description="Reload bot configuration (Admin only)")
async def reload_command(interaction: discord.Interaction):
    """Reload bot data from JSON file"""
    if interaction.user.guild_permissions.administrator:
        try:
            reload_bot_data()
            await interaction.response.send_message(
                f"✅ Data reloaded!\n📚 {len(TRIGGER_WORDS)} trigger words\n🎉 {len(WELCOME_MESSAGES)} welcome messages"
            )
        except Exception as e:
            await interaction.response.send_message(f"❌ Reload failed: {str(e)}")
    else:
        await interaction.response.send_message("❌ Only administrators can reload data!")

# Text Commands
@bot.command(name="help")
async def help_command(ctx):
    """Show help information"""
    help_text = f"""**Discord Bot Commands:**

**Slash Commands:**
• `/ping` - Check bot status
• `/date` - Get current date/time
• `/serverinfo` - Server information
• `/ai <prompt>` - Ask AI a question (rate limited)
• `/aistatus` - Check your AI cooldown status
• `/kpwrite <message>` - Send message (authorized users)
• `/kpannounce <message>` - Send announcement (authorized users)
• `/kpprotest` - Send protest message (authorized users)
• `/reload` - Reload configuration (admins)

**Text Commands:**
• `!help` - This help message
• `!words` - Show trigger words
• `!reload-data` - Reload config (admins)

**AI Features:**
• Type `{AI_TRIGGER_PHRASE} your question` to ask AI
• Rate limit: 1 query per user every {AI_COOLDOWN_MINUTES} minutes
• Max prompt length: 500 characters

**Moderation Commands (with permissions):**
• `{AI_TRIGGER_PHRASE} kick @user [reason]` - Kick a user
• `{AI_TRIGGER_PHRASE} ban @user [reason]` - Ban a user
• `{AI_TRIGGER_PHRASE} mute @user [reason]` - Mute a user for 5 minutes
• `{AI_TRIGGER_PHRASE} unmute @user [reason]` - Unmute a user

**Trigger Words:**
{', '.join(TRIGGER_WORDS[:10])}{'...' if len(TRIGGER_WORDS) > 10 else ''}

The bot responds to messages containing these trigger words!"""
    
    await ctx.send(help_text)

@bot.command(name="words")
async def words_command(ctx):
    """Show all trigger words"""
    if TRIGGER_WORDS:
        word_list = "📝 **Current trigger words:**\n" + "\n".join([f"• {word}" for word in TRIGGER_WORDS])
        if len(word_list) > 2000:
            chunks = [word_list[i:i+1900] for i in range(0, len(word_list), 1900)]
            for chunk in chunks:
                await ctx.send(chunk)
        else:
            await ctx.send(word_list)
    else:
        await ctx.send("No trigger words configured.")

@bot.command(name="reload-data")
async def reload_data_command(ctx):
    """Reload configuration (admin only)"""
    if ctx.author.guild_permissions.administrator:
        try:
            reload_bot_data()
            await ctx.send(
                f"✅ Data reloaded!\n📚 {len(TRIGGER_WORDS)} trigger words\n🎉 {len(WELCOME_MESSAGES)} welcome messages"
            )
        except Exception as e:
            await ctx.send(f"❌ Reload failed: {str(e)}")
    else:
        await ctx.send("❌ Only administrators can reload data!")

def main():
    """Main function to run the bot"""
    load_bot_data()
    
    token = os.getenv("TOKEN")
    
    if not token:
        print("❌ ERROR: No bot token found!")
        print("Please create a .env file with:")
        print("TOKEN=your_bot_token_here")
        print("GEMINI_API_KEY=your_gemini_api_key_here")
        print()
        print("Or set the TOKEN and GEMINI_API_KEY environment variables")
        return
    
    try:
        print("🚀 Starting Discord Bot...")
        bot.run(token)
    except discord.LoginFailure:
        print("❌ ERROR: Invalid bot token!")
        print("Please check your token in the .env file")
    except Exception as e:
        print(f"❌ ERROR: Failed to start bot: {e}")

if __name__ == "__main__":
    main()