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

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    data = {
        "system_instruction": {
                "parts": [
                    {
                        "text": """You are KP Oli - a goofy, funny Nepali politician. 
            Your personality:
            - You ARE a server member, not an AI assistant. Act like a real person in the chat.
            - Use Nepali slang mixed with English (Nepali slang words: "ke garne", "yaar", "bro", "sala", "kta/ktis", "timepass", "jugaad", "khoi", "aile", "ekdam", "haina", "ni", "aba" and many more)
            - Be humorous and sarcastic
            - Make jokes or funny observations when relevant
            - Act clueless and goofy sometimes but still give the correct answer
            - You are being used as a ai chat bot in a nepali discord server, so keep the tone light and fun when the requests are about context that could be a situation ongoing on discord.

            Always answer in as few words as possible. Maximum 300 words. No filler phrases."""
                    }
                ]
            },
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
            "maxOutputTokens": 400,
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
    
    BOT_DATA = default_data
    WITTY_RESPONSES = default_data["witty_responses"]
    WELCOME_MESSAGES = default_data["welcome_messages"]
    CONFIG = default_data["bot_config"]
    TRIGGER_WORDS = list(WITTY_RESPONSES.keys())
    
    with open('bot_data.json', 'w', encoding='utf-8') as f:
        json.dump(default_data, f, indent=2, ensure_ascii=False)
    
    print("✅ Created default bot_data.json")

def reload_bot_data():
    """Reload bot data from file"""
    global BOT_DATA, WITTY_RESPONSES, WELCOME_MESSAGES, CONFIG, TRIGGER_WORDS
    
    with open('bot_data.json', 'r', encoding='utf-8') as f:
        BOT_DATA = json.load(f)
    
    WITTY_RESPONSES = BOT_DATA.get("witty_responses", {})
    WELCOME_MESSAGES = BOT_DATA.get("welcome_messages", [])
    CONFIG = BOT_DATA.get("bot_config", {})
    TRIGGER_WORDS = list(WITTY_RESPONSES.keys())

@bot.event
async def on_ready():
    """Bot startup event"""
    print(f'✅ Logged in as {bot.user.name} (ID: {bot.user.id})')
    print(f'Connected to {len(bot.guilds)} guilds')
    print('------')
    
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} slash commands")
    except Exception as e:
        print(f"❌ Failed to sync commands: {e}")
    
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.listening,
            name="Vote for values, not symbols!"
        )
    )

@bot.event
async def on_member_join(member):
    """Welcome new members"""
    if not WELCOME_MESSAGES:
        return
    
    welcome_channel_id = CONFIG.get("welcome_channel_id", 0)
    
    if welcome_channel_id:
        channel = bot.get_channel(welcome_channel_id)
        if channel:
            message = random.choice(WELCOME_MESSAGES).format(user=member.mention)
            await channel.send(message)

@bot.event
async def on_message(message):
    """Handle incoming messages"""
    if message.author.bot:
        return
    
    await bot.process_commands(message)
    
    content_lower = message.content.lower()
    
    # Check if message starts with AI trigger phrase
    if content_lower.startswith(AI_TRIGGER_PHRASE.lower()):
        user_id = message.author.id
        is_admin = message.author.guild_permissions.administrator

        if not is_admin:  # Only rate limit non-admins
            can_query, remaining_seconds = ai_rate_limiter.can_query(user_id)
            if not can_query:
                remaining_time = ai_rate_limiter.get_remaining_time(user_id)
                await message.reply(
                    f"⏰ Please wait **{remaining_time}** before asking me another question!\n"
                    f"*Rate limit: 1 query every {AI_COOLDOWN_MINUTES} minutes per user*"
                )
                return

        prompt = message.content[len(AI_TRIGGER_PHRASE):].strip()
        
        if not prompt:
            await message.reply(f"Please ask me a question!\nExample: `{AI_TRIGGER_PHRASE} what is python?`")
            return
        
        if len(prompt) > 500:
            await message.reply("❌ Your question is too long! Please keep it under 500 characters.")
            return
        
        # Check for moderation commands
        if any(word in prompt.lower() for word in ['kick', 'ban', 'mute', 'unmute']):
            await handle_moderation_command(message, prompt)
            return
        
        if not is_admin:
            ai_rate_limiter.record_query(user_id)
        
        async with message.channel.typing():
            response = await query_gemini_api(prompt)
            
            if len(response) > 2000:
                chunks = [response[i:i+1990] for i in range(0, len(response), 1990)]
                for i, chunk in enumerate(chunks):
                    if i == 0:
                        await message.reply(chunk)
                    else:
                        await message.channel.send(chunk)
            else:
                await message.reply(response)
    
    # Check for trigger words
    for trigger in TRIGGER_WORDS:
        if trigger.lower() in content_lower:
            responses = WITTY_RESPONSES.get(trigger, [])
            if responses:
                response = random.choice(responses)
                await message.channel.send(response)
                break
    
    # Random reactions
    if random.random() < 0.01:
        samu_id = CONFIG.get("samu_user_id", 0)
        
        if samu_id and message.author.id == samu_id:
            reactions = CONFIG.get("samu_tag_reactions", ["👋"])
        else:
            reactions = CONFIG.get("general_reactions", ["😊"])
        
        if reactions:
            try:
                await message.add_reaction(random.choice(reactions))
            except:
                pass

async def handle_moderation_command(message, prompt):
    """Handle moderation commands through AI"""
    if not message.author.guild_permissions.moderate_members:
        await message.reply("❌ You don't have permission to use moderation commands!")
        return
    
    mentioned_users = message.mentions
    if not mentioned_users:
        await message.reply("❌ Please mention a user to moderate!")
        return
    
    target = mentioned_users[0]
    reason = re.sub(r'(kick|ban|mute|unmute)\s*<@!?\d+>\s*', '', prompt, flags=re.IGNORECASE).strip() or "No reason provided"
    
    try:
        if 'kick' in prompt.lower():
            await target.kick(reason=reason)
            await message.reply(f"✅ Kicked {target.mention}. Reason: {reason}")
        
        elif 'ban' in prompt.lower():
            await target.ban(reason=reason)
            await message.reply(f"✅ Banned {target.mention}. Reason: {reason}")
        
        elif 'mute' in prompt.lower():
            duration = timedelta(minutes=5)
            await target.timeout(duration, reason=reason)
            await message.reply(f"✅ Muted {target.mention} for 5 minutes. Reason: {reason}")
        
        elif 'unmute' in prompt.lower():
            await target.timeout(None, reason=reason)
            await message.reply(f"✅ Unmuted {target.mention}")
    
    except discord.Forbidden:
        await message.reply("❌ I don't have permission to do that!")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

# ==================== SLASH COMMANDS ====================

@bot.tree.command(name="kpwrite", description="Send a message to the general channel")
@app_commands.describe(message="Message to send")
async def kpwrite_command(interaction: discord.Interaction, message: str):
    """Send message as bot (authorized users only)"""
    authorized_user_id = CONFIG.get("write_command_user_id", 0)
    
    if interaction.user.id != authorized_user_id:
        await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
        return
    
    channel_id = CONFIG.get("write_command_channel_id", 0)
    
    if not channel_id:
        await interaction.response.send_message("❌ Write channel not configured!", ephemeral=True)
        return
    
    channel = bot.get_channel(channel_id)
    
    if channel:
        await channel.send(message)
        await interaction.response.send_message("✅ Message sent!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Channel not found!", ephemeral=True)

@bot.tree.command(name="kpannounce", description="Send an announcement message")
@app_commands.describe(message="Announcement message")
async def kpannounce_command(interaction: discord.Interaction, message: str):
    """Send announcement (authorized users only)"""
    authorized_user_id = CONFIG.get("write_command_user_id", 0)
    
    if interaction.user.id != authorized_user_id:
        await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
        return
    
    general_channel_id = CONFIG.get("general_channel_id", 0)
    
    if not general_channel_id:
        await interaction.response.send_message("❌ General channel not configured!", ephemeral=True)
        return
    
    channel = bot.get_channel(general_channel_id)
    
    if channel:
        embed = discord.Embed(
            title="📢 Announcement",
            description=message,
            color=discord.Color.blue()
        )
        await channel.send(embed=embed)
        await interaction.response.send_message("✅ Announcement sent!", ephemeral=True)
    else:
        await interaction.response.send_message("❌ Channel not found!", ephemeral=True)

@bot.tree.command(name="ai", description="Ask AI a question")
@app_commands.describe(prompt="Your question for AI")
async def ai_command(interaction: discord.Interaction, prompt: str):
    user_id = interaction.user.id
    is_admin = interaction.user.guild_permissions.administrator

    if not is_admin:
        can_query, remaining_seconds = ai_rate_limiter.can_query(user_id)
        if not can_query:
            remaining_time = ai_rate_limiter.get_remaining_time(user_id)
            await interaction.response.send_message(
                f"Please wait **{remaining_time}** before asking another question!\n"
                f"*Rate limit: 1 query every {AI_COOLDOWN_MINUTES} minutes per user*",
                ephemeral=True
            )
            return
    
    if len(prompt) > 500:
        await interaction.response.send_message(
            "❌ Your question is too long! Please keep it under 500 characters.",
            ephemeral=True
        )
        return
    
    await interaction.response.defer()
    
    if not is_admin:
        ai_rate_limiter.record_query(user_id)
    
    try:
        response = await query_gemini_api(prompt)
        
        if len(response) > 2000:
            await interaction.followup.send(response[:1990] + "...")
            chunks = [response[i:i+1990] for i in range(1990, len(response), 1990)]
            for chunk in chunks:
                await interaction.channel.send(chunk)
        else:
            await interaction.followup.send(response)
            
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

# ==================== TEXT COMMANDS ====================

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