import os
import json
import random
import re
import asyncio
import html
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
AI_TRIGGER_PHRASE = "oh kp baa"
AI_USER_COOLDOWNS = {}
AI_COOLDOWN_MINUTES = 5
GEMINI_API_KEY = None

# Weather API key (OpenWeatherMap - free tier)
WEATHER_API_KEY = None

TARGET_CHANNEL_ID = 762775973816696863

# Confession storage: maps message_id -> author_id (for mod reference only, never shown publicly)
confession_store = {}

# ==================== NEPALI CALENDAR DATA ====================

NEPALI_FESTIVALS = {
    # Format: (BS_month, BS_day): "Festival Name"
    # Month 1 = Baisakh, 2 = Jestha, ..., 12 = Chaitra
    (1, 1):   "🎉 Nepali New Year (Naya Barsha)!",
    (1, 15):  "🌸 Ubhauli Parwa",
    (3, 15):  "🌧️ Sithi Nakha",
    (5, 29):  "🐍 Nag Panchami",
    (5, 30):  "💫 Janai Purnima / Rakshya Bandhan",
    (6, 2):   "🐮 Gaijatra",
    (6, 12):  "🎭 Indra Jatra",
    (6, 18):  "🙏 Haritalika Teej",
    (6, 21):  "🌿 Rishi Panchami",
    (7, 1):   "💡 Ghatasthapana (Dashain begins)",
    (7, 8):   "🌺 Maha Ashtami",
    (7, 9):   "🐃 Maha Navami",
    (7, 10):  "🎊 Bijaya Dashami (Dashain)!",
    (7, 15):  "🌕 Kojagrat Purnima",
    (7, 29):  "🪔 Tihar begins – Kaag Tihar",
    (7, 30):  "🐕 Kukur Tihar",
    (8, 1):   "🐮 Gai Tihar & Laxmi Puja",
    (8, 2):   "🎆 Mha Puja & Gobardhan Puja",
    (8, 3):   "👫 Bhai Tika (Tihar ends)!",
    (8, 16):  "🌕 Chhath Parwa begins",
    (9, 1):   "❄️ Udhauli Parwa",
    (10, 1):  "🎋 Maghe Sankranti",
    (10, 15): "🎵 Sonam Lhosar",
    (11, 6):  "🌺 Maha Shivaratri",
    (11, 15): "🌸 Gyalpo Lhosar",
    (12, 15): "🌈 Fagu Purnima (Holi)!",
    (12, 30): "🎊 Ghode Jatra",
}

NEPALI_MONTHS = [
    "Baisakh", "Jestha", "Ashadh", "Shrawan",
    "Bhadra", "Ashwin", "Kartik", "Mangsir",
    "Poush", "Magh", "Falgun", "Chaitra"
]

def get_upcoming_nepali_festivals(days_ahead: int = 30) -> list:
    """Return upcoming festivals within the next N days"""
    if not NEPALI_DATETIME_AVAILABLE:
        return []
    upcoming = []
    nepal_tz = pytz.timezone('Asia/Kathmandu')
    now = datetime.now(nepal_tz)
    for i in range(days_ahead):
        future_date = now + timedelta(days=i)
        try:
            nepali_d = nepali_datetime.date.from_datetime_date(future_date.date())
            key = (nepali_d.month, nepali_d.day)
            if key in NEPALI_FESTIVALS:
                upcoming.append({
                    "days_away": i,
                    "bs_date": f"{NEPALI_MONTHS[nepali_d.month - 1]} {nepali_d.day}",
                    "ad_date": future_date.strftime("%b %d"),
                    "name": NEPALI_FESTIVALS[key]
                })
        except Exception:
            continue
    return upcoming

# ==================== 8-BALL RESPONSES ====================

EIGHTBALL_RESPONSES = [
    "It is certain! 🟢",
    "Without a doubt! 🟢",
    "Yes, definitely! 🟢",
    "You may rely on it! 🟢",
    "As I see it, yes! 🟢",
    "Most likely! 🟢",
    "Outlook good! 🟢",
    "Signs point to yes! 🟢",
    "Reply hazy, try again 🟡",
    "Ask again later 🟡",
    "Better not tell you now 🟡",
    "Cannot predict now 🟡",
    "Concentrate and ask again 🟡",
    "Don't count on it 🔴",
    "My reply is no 🔴",
    "My sources say no 🔴",
    "Outlook not so good 🔴",
    "Very doubtful 🔴",
]

# ==================== AI RATE LIMITER ====================

class AIRateLimiter:
    """Handle rate limiting for AI queries"""

    def __init__(self, cooldown_minutes: int = 5):
        self.cooldown_minutes = cooldown_minutes
        self.user_last_query = {}

    def can_query(self, user_id: int) -> tuple[bool, int]:
        now = time.time()
        last_query = self.user_last_query.get(user_id, 0)
        time_passed = now - last_query
        cooldown_seconds = self.cooldown_minutes * 60
        if time_passed >= cooldown_seconds:
            return True, 0
        return False, int(cooldown_seconds - time_passed)

    def record_query(self, user_id: int):
        self.user_last_query[user_id] = time.time()

    def get_remaining_time(self, user_id: int) -> str:
        _, seconds = self.can_query(user_id)
        if seconds <= 0:
            return "Ready to use"
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}m {secs}s" if minutes > 0 else f"{secs}s"

ai_rate_limiter = AIRateLimiter(AI_COOLDOWN_MINUTES)

# ==================== GEMINI API ====================

async def query_gemini_api(prompt: str) -> str:
    """Query Google's Gemini API"""
    if not GEMINI_API_KEY:
        return "❌ Gemini API key not configured. Please add GEMINI_API_KEY to your .env file."

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
    headers = {'Content-Type': 'application/json'}
    data = {
        "system_instruction": {
            "parts": [{
                "text": """You are KP Oli - a goofy, funny Nepali politician.
            Your personality:
            - You ARE a server member, not an AI assistant. Act like a real person in the chat.
            - Use Nepali slang mixed with English (Nepali slang words: "ke garne", "yaar", "bro", "sala", "kta/ktis", "timepass", "jugaad", "khoi", "aile", "ekdam", "haina", "ni", "aba" and many more)
            - Be humorous and sarcastic
            - Make jokes or funny observations when relevant
            - Act clueless and goofy sometimes but still give the correct answer
            - You are being used as an ai chat bot in a nepali discord server, so keep the tone light and fun.

            Always answer in as few words as possible. Maximum 300 words. No filler phrases."""
            }]
        },
        "contents": [{"parts": [{"text": prompt}]}],
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
                    if 'candidates' in result and result['candidates']:
                        candidate = result['candidates'][0]
                        if 'content' in candidate and 'parts' in candidate['content']:
                            return candidate['content']['parts'][0]['text']
                    return "❌ No content in API response"
                else:
                    error_text = await response.text()
                    print(f"Gemini API Error {response.status}: {error_text}")
                    return f"❌ API Error: {response.status}. Please try again later."
    except asyncio.TimeoutError:
        return "❌ Request timed out. Please try again."
    except Exception as e:
        print(f"Gemini API Exception: {e}")
        return f"❌ Error connecting to KP: {str(e)}"

# ==================== DATA LOADING ====================

def load_bot_data():
    """Load bot configuration and responses from JSON file"""
    global BOT_DATA, WITTY_RESPONSES, WELCOME_MESSAGES, CONFIG, TRIGGER_WORDS
    global GEMINI_API_KEY, WEATHER_API_KEY

    GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
    WEATHER_API_KEY = os.getenv("WEATHER_API_KEY")

    if not GEMINI_API_KEY:
        print("⚠️  WARNING: GEMINI_API_KEY not found! AI features disabled.")
    else:
        print("✅ Gemini API key loaded")

    if not WEATHER_API_KEY:
        print("⚠️  WARNING: WEATHER_API_KEY not found! /weather will be disabled.")
        print("   Get a free key at: https://openweathermap.org/api")
    else:
        print("✅ Weather API key loaded")

    try:
        with open('bot_data.json', 'r', encoding='utf-8') as f:
            BOT_DATA = json.load(f)
        WITTY_RESPONSES = BOT_DATA.get("witty_responses", {})
        WELCOME_MESSAGES = BOT_DATA.get("welcome_messages", [])
        CONFIG = BOT_DATA.get("bot_config", {})
        TRIGGER_WORDS = list(WITTY_RESPONSES.keys())
        print(f"Loaded {len(WITTY_RESPONSES)} trigger categories")
        print(f"Loaded {len(WELCOME_MESSAGES)} welcome messages")
    except FileNotFoundError:
        print("bot_data.json not found! Creating default configuration...")
        create_default_config()
    except json.JSONDecodeError as e:
        print(f"Error reading bot_data.json: {e}")
        create_default_config()

def create_default_config():
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
            "confession_channel_id": 0,
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
    global BOT_DATA, WITTY_RESPONSES, WELCOME_MESSAGES, CONFIG, TRIGGER_WORDS
    with open('bot_data.json', 'r', encoding='utf-8') as f:
        BOT_DATA = json.load(f)
    WITTY_RESPONSES = BOT_DATA.get("witty_responses", {})
    WELCOME_MESSAGES = BOT_DATA.get("welcome_messages", [])
    CONFIG = BOT_DATA.get("bot_config", {})
    TRIGGER_WORDS = list(WITTY_RESPONSES.keys())

# ==================== BOT EVENTS ====================

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
            name="Vote for values, not symbols!"
        )
    )

@bot.event
async def on_member_join(member):
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
    if message.author.bot:
        return
    await bot.process_commands(message)
    content_lower = message.content.lower()

    # AI trigger phrase
    if content_lower.startswith(AI_TRIGGER_PHRASE.lower()):
        user_id = message.author.id
        is_admin = message.author.guild_permissions.administrator
        if not is_admin:
            can_query, _ = ai_rate_limiter.can_query(user_id)
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

    # Trigger words
    for trigger in TRIGGER_WORDS:
        if trigger.lower() in content_lower:
            responses = WITTY_RESPONSES.get(trigger, [])
            if responses:
                await message.channel.send(random.choice(responses))
                break

    # Random reactions (1% chance)
    if random.random() < 0.01:
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
            await target.timeout(timedelta(minutes=5), reason=reason)
            await message.reply(f"✅ Muted {target.mention} for 5 minutes. Reason: {reason}")
        elif 'unmute' in prompt.lower():
            await target.timeout(None, reason=reason)
            await message.reply(f"✅ Unmuted {target.mention}")
    except discord.Forbidden:
        await message.reply("❌ I don't have permission to do that!")
    except Exception as e:
        await message.reply(f"❌ Error: {str(e)}")

# ==================== POLL ====================

@bot.tree.command(name="poll", description="Create a poll with up to 4 options")
@app_commands.describe(
    question="The poll question",
    option1="First option",
    option2="Second option",
    option3="Third option (optional)",
    option4="Fourth option (optional)"
)
async def poll_command(
    interaction: discord.Interaction,
    question: str,
    option1: str,
    option2: str,
    option3: str = None,
    option4: str = None
):
    options = [opt for opt in [option1, option2, option3, option4] if opt]
    emojis = ["1️⃣", "2️⃣", "3️⃣", "4️⃣"]

    description = ""
    for i, opt in enumerate(options):
        description += f"{emojis[i]} {opt}\n\n"

    embed = discord.Embed(
        title=f"📊 {question}",
        description=description,
        color=discord.Color.blurple()
    )
    embed.set_footer(text=f"Poll by {interaction.user.display_name}")
    embed.timestamp = discord.utils.utcnow()

    await interaction.response.send_message(embed=embed)
    poll_message = await interaction.original_response()
    for i in range(len(options)):
        await poll_message.add_reaction(emojis[i])

# ==================== CONFESSION ====================

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
        # Store author in memory for mod reference only — never shown publicly
        confession_store[confession_msg.id] = interaction.user.id

        await interaction.response.send_message(
            "✅ Your confession has been submitted anonymously!", ephemeral=True
        )

@bot.tree.command(name="confess", description="Submit an anonymous confession")
async def confess_command(interaction: discord.Interaction):
    await interaction.response.send_modal(ConfessionModal())

# ==================== 8-BALL ====================

@bot.tree.command(name="8ball", description="Ask the magic 8-ball a yes/no question")
@app_commands.describe(question="Your yes/no question")
async def eightball_command(interaction: discord.Interaction, question: str):
    answer = random.choice(EIGHTBALL_RESPONSES)
    embed = discord.Embed(color=discord.Color.dark_purple())
    embed.add_field(name="🎱 Question", value=question, inline=False)
    embed.add_field(name="🔮 Answer", value=f"**{answer}**", inline=False)
    embed.set_footer(text=f"Asked by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

# ==================== COIN FLIP ====================

@bot.tree.command(name="coinflip", description="Flip a coin!")
async def coinflip_command(interaction: discord.Interaction):
    result = random.choice(["Heads", "Tails"])
    emoji = "🪙" if result == "Heads" else "🟤"
    embed = discord.Embed(
        title="🪙 Coin Flip",
        description=f"## {emoji} {result}!",
        color=discord.Color.gold() if result == "Heads" else discord.Color.dark_grey()
    )
    embed.set_footer(text=f"Flipped by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

# ==================== URBAN DICTIONARY ====================

@bot.tree.command(name="define", description="Look up a word or slang on Urban Dictionary")
@app_commands.describe(word="Word or phrase to define")
async def define_command(interaction: discord.Interaction, word: str):
    await interaction.response.defer()
    url = f"https://api.urbandictionary.com/v0/define?term={word}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    await interaction.followup.send("❌ Could not reach Urban Dictionary. Try again later.")
                    return
                data = await response.json()

        definitions = data.get("list", [])
        if not definitions:
            await interaction.followup.send(f"❌ No definition found for **{word}**.")
            return

        top = definitions[0]
        definition = top.get("definition", "N/A").replace("[", "").replace("]", "")
        example = top.get("example", "").replace("[", "").replace("]", "")
        thumbs_up = top.get("thumbs_up", 0)
        thumbs_down = top.get("thumbs_down", 0)

        if len(definition) > 900:
            definition = definition[:900] + "..."
        if len(example) > 400:
            example = example[:400] + "..."

        embed = discord.Embed(
            title=f"📖 {top.get('word', word)}",
            url=top.get("permalink", ""),
            color=discord.Color.orange()
        )
        embed.add_field(name="Definition", value=definition, inline=False)
        if example:
            embed.add_field(name="Example", value=f"*{example}*", inline=False)
        embed.set_footer(text=f"👍 {thumbs_up}  👎 {thumbs_down} | Urban Dictionary")
        await interaction.followup.send(embed=embed)

    except asyncio.TimeoutError:
        await interaction.followup.send("❌ Request timed out. Please try again.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")

# ==================== WEATHER ====================

WEATHER_EMOJIS = {
    "Clear": "☀️", "Clouds": "☁️", "Rain": "🌧️",
    "Drizzle": "🌦️", "Thunderstorm": "⛈️", "Snow": "❄️",
    "Mist": "🌫️", "Smoke": "🌫️", "Haze": "🌫️",
    "Dust": "🌪️", "Fog": "🌫️", "Sand": "🌪️",
    "Ash": "🌋", "Squall": "💨", "Tornado": "🌪️"
}

@bot.tree.command(name="weather", description="Get current weather for a city")
@app_commands.describe(city="City name (e.g. Kathmandu, Pokhara, London)")
async def weather_command(interaction: discord.Interaction, city: str):
    if not WEATHER_API_KEY:
        await interaction.response.send_message(
            "❌ Weather feature not configured.\n"
            "Add `WEATHER_API_KEY=your_key` to your .env file.\n"
            "Get a free key at: https://openweathermap.org/api",
            ephemeral=True
        )
        return

    await interaction.response.defer()
    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?q={city}&appid={WEATHER_API_KEY}&units=metric"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status == 404:
                    await interaction.followup.send(f"❌ City **{city}** not found. Check the spelling!")
                    return
                if response.status != 200:
                    await interaction.followup.send("❌ Weather service unavailable. Try again later.")
                    return
                data = await response.json()

        weather_main = data["weather"][0]["main"]
        description = data["weather"][0]["description"].title()
        emoji = WEATHER_EMOJIS.get(weather_main, "🌡️")

        temp = data["main"]["temp"]
        feels_like = data["main"]["feels_like"]
        temp_min = data["main"]["temp_min"]
        temp_max = data["main"]["temp_max"]
        humidity = data["main"]["humidity"]
        wind_speed = data["wind"]["speed"]
        visibility = data.get("visibility", 0) / 1000  # metres -> km
        country = data["sys"]["country"]
        city_name = data["name"]

        embed = discord.Embed(
            title=f"{emoji} Weather in {city_name}, {country}",
            description=f"**{description}**",
            color=discord.Color.blue()
        )
        embed.add_field(name="🌡️ Temperature", value=f"{temp:.1f}°C (feels like {feels_like:.1f}°C)", inline=True)
        embed.add_field(name="🔼🔽 High / Low", value=f"{temp_max:.1f}°C / {temp_min:.1f}°C", inline=True)
        embed.add_field(name="💧 Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="💨 Wind Speed", value=f"{wind_speed} m/s", inline=True)
        embed.add_field(name="👁️ Visibility", value=f"{visibility:.1f} km", inline=True)
        embed.set_footer(text="Data from OpenWeatherMap")
        embed.timestamp = discord.utils.utcnow()
        await interaction.followup.send(embed=embed)

    except asyncio.TimeoutError:
        await interaction.followup.send("❌ Weather request timed out. Please try again.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error fetching weather: {str(e)}")

# ==================== NEPALI CALENDAR ====================

@bot.tree.command(name="calendar", description="Show upcoming Nepali festivals and holidays")
@app_commands.describe(days="How many days ahead to look (default: 30, max: 90)")
async def calendar_command(interaction: discord.Interaction, days: int = 30):
    if days < 1 or days > 90:
        await interaction.response.send_message("❌ Please choose between 1 and 90 days.", ephemeral=True)
        return

    await interaction.response.defer()

    if not NEPALI_DATETIME_AVAILABLE:
        await interaction.followup.send(
            "❌ Nepali calendar requires the `nepali-datetime` package.\n"
            "Install it with: `pip install nepali-datetime`"
        )
        return

    festivals = get_upcoming_nepali_festivals(days)

    if not festivals:
        await interaction.followup.send(f"📅 No major Nepali festivals found in the next **{days} days**.")
        return

    embed = discord.Embed(
        title=f"🇳🇵 Upcoming Nepali Festivals (Next {days} Days)",
        color=discord.Color.red()
    )

    for fest in festivals:
        if fest["days_away"] == 0:
            label = "🎉 **TODAY!**"
        elif fest["days_away"] == 1:
            label = "⏰ Tomorrow"
        else:
            label = f"📅 In {fest['days_away']} days"

        embed.add_field(
            name=fest["name"],
            value=f"{label}\n📆 BS: {fest['bs_date']} | AD: {fest['ad_date']}",
            inline=False
        )

    nepal_tz = pytz.timezone('Asia/Kathmandu')
    now = datetime.now(nepal_tz)
    embed.set_footer(text=f"Nepal Time: {now.strftime('%I:%M %p, %b %d %Y')}")
    await interaction.followup.send(embed=embed)

# ==================== SLOWMODE ====================

@bot.tree.command(name="slowmode", description="Set slowmode for the current channel (Moderators only)")
@app_commands.describe(seconds="Slowmode delay in seconds (0 = disable, max 21600)")
async def slowmode_command(interaction: discord.Interaction, seconds: int):
    if not interaction.user.guild_permissions.manage_channels:
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

# ==================== PURGE ====================

@bot.tree.command(name="purge", description="Delete messages from this channel (Moderators only)")
@app_commands.describe(amount="Number of messages to delete (1–100)")
async def purge_command(interaction: discord.Interaction, amount: int):
    if not interaction.user.guild_permissions.manage_messages:
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

# ==================== AVATAR ====================

@bot.tree.command(name="avatar", description="View a user's full-size avatar")
@app_commands.describe(user="The user whose avatar you want to see (leave empty for yourself)")
async def avatar_command(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    avatar_url = target.display_avatar.url

    embed = discord.Embed(
        title=f"🖼️ {target.display_name}'s Avatar",
        color=target.color if target.color.value != 0 else discord.Color.blurple()
    )
    embed.set_image(url=avatar_url)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")

    # Download link buttons
    view = discord.ui.View()
    view.add_item(discord.ui.Button(
        label="PNG",
        url=target.display_avatar.replace(format='png', size=1024).url,
        style=discord.ButtonStyle.link
    ))
    view.add_item(discord.ui.Button(
        label="WEBP",
        url=target.display_avatar.replace(format='webp', size=1024).url,
        style=discord.ButtonStyle.link
    ))
    if target.display_avatar.is_animated():
        view.add_item(discord.ui.Button(
            label="GIF",
            url=target.display_avatar.replace(format='gif', size=1024).url,
            style=discord.ButtonStyle.link
        ))

    await interaction.response.send_message(embed=embed, view=view)

# ==================== EXISTING COMMANDS ====================

@bot.tree.command(name="kpwrite", description="Send a message to the general channel")
@app_commands.describe(message="Message to send")
async def kpwrite_command(interaction: discord.Interaction, message: str):
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
        embed = discord.Embed(title="📢 Announcement", description=message, color=discord.Color.blue())
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
        can_query, _ = ai_rate_limiter.can_query(user_id)
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
            "❌ Your question is too long! Please keep it under 500 characters.", ephemeral=True
        )
        return
    await interaction.response.defer()
    if not is_admin:
        ai_rate_limiter.record_query(user_id)
    try:
        response = await query_gemini_api(prompt)
        if len(response) > 2000:
            await interaction.followup.send(response[:1990] + "...")
            for chunk in [response[i:i+1990] for i in range(1990, len(response), 1990)]:
                await interaction.channel.send(chunk)
        else:
            await interaction.followup.send(response)
    except Exception as e:
        print(f"Error in AI slash command: {e}")
        await interaction.followup.send("❌ Sorry, I encountered an error. Please try again later.")

@bot.tree.command(name="aistatus", description="Check your AI cooldown status")
async def ai_status_command(interaction: discord.Interaction):
    user_id = interaction.user.id
    can_query, _ = ai_rate_limiter.can_query(user_id)
    if can_query:
        status = "✅ **Ready to use AI!**\nYou can ask me a question now."
    else:
        status = f"⏰ **Cooldown Active**\nYou can ask me again in **{ai_rate_limiter.get_remaining_time(user_id)}**"
    await interaction.response.send_message(
        f"{status}\n\n*Rate limit: 1 query every {AI_COOLDOWN_MINUTES} minutes per user*\n"
        f"*Use: `{AI_TRIGGER_PHRASE} your question` or `/ai your question`*",
        ephemeral=True
    )

@bot.tree.command(name="ping", description="Check bot status")
async def ping_command(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")

@bot.tree.command(name="date", description="Get current date and time in both English and Nepali (Bikram Sambat)")
async def date_command(interaction: discord.Interaction):
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
            except Exception:
                try:
                    nepali_d = nepali_datetime.date.from_datetime_date(now.date())
                    nepali_date_str = nepali_d.strftime("%A, %d %B %Y")
                except Exception:
                    nepali_date_str = "BS conversion failed"
        if "conversion" in nepali_date_str.lower():
            nepali_days = {
                'Monday': 'सोमबार', 'Tuesday': 'मंगलबार', 'Wednesday': 'बुधबार',
                'Thursday': 'बिहिबार', 'Friday': 'शुक्रबार', 'Saturday': 'शनिबार',
                'Sunday': 'आइतबार'
            }
            weekday_nepali = nepali_days.get(now.strftime("%A"), now.strftime("%A"))
            nepali_date_str = f"{weekday_nepali} (BS date conversion issue)"
        response = (
            f"📅 **Current Date & Time:**\n\n"
            f"🇬🇧 **English (AD):** {english_date}\n"
            f"🇳🇵 **Nepali (BS):** {nepali_date_str}\n\n"
            f"🕐 **Time:** {english_time} (Nepal Time)\n"
            f"🌍 **Timezone:** Asia/Kathmandu (NPT)"
        )
        await interaction.followup.send(response)
    except Exception as e:
        await interaction.followup.send(f"❌ Error getting date: {str(e)}")

@bot.tree.command(name="serverinfo", description="Get server information")
async def serverinfo_command(interaction: discord.Interaction):
    guild = interaction.guild
    info = (
        f"🏰 **Server Information:**\n\n"
        f"**Name:** {guild.name}\n"
        f"**ID:** {guild.id}\n"
        f"**Owner:** {guild.owner.mention if guild.owner else 'Unknown'}\n"
        f"**Created:** {guild.created_at.strftime('%B %d, %Y')}\n"
        f"**Members:** {guild.member_count}\n"
        f"**Text Channels:** {len(guild.text_channels)}\n"
        f"**Voice Channels:** {len(guild.voice_channels)}\n"
        f"**Boost Level:** {guild.premium_tier}\n"
        f"**Boosts:** {guild.premium_subscription_count}"
    )
    await interaction.response.send_message(info)

@bot.tree.command(name="reload", description="Reload bot configuration (Admin only)")
async def reload_command(interaction: discord.Interaction):
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
    help_text = f"""**Discord Bot Commands:**

**Fun & Games:**
• `/trivia` — Random trivia question with buttons
• `/wyr` — Would You Rather question
• `/truth` — Random truth question
• `/dare` — Random dare
• `/rps` — Rock Paper Scissors vs the bot
• `/8ball <question>` — Ask the magic 8-ball
• `/coinflip` — Flip a coin
• `/poll <question> <opt1> <opt2> [opt3] [opt4]` — Create a reaction poll

**Info:**
• `/userinfo [@user]` — View a user's info
• `/roleinfo <@role>` — View a role's info
• `/avatar [@user]` — View someone's full-size avatar
• `/define <word>` — Urban Dictionary lookup
• `/weather <city>` — Current weather for any city
• `/calendar [days]` — Upcoming Nepali festivals

**Utility:**
• `/remind <time> <message>` — Set a reminder (e.g. 30m, 2h, 1d)
• `/afk [reason]` — Set yourself as AFK
• `/confess` — Submit an anonymous confession

**Moderation:**
• `/lock [reason]` — Lock the current channel
• `/unlock [reason]` — Unlock the current channel
• `/slowmode <seconds>` — Set channel slowmode (0 to disable)
• `/purge <amount>` — Bulk delete messages (1–100)

**Slash Commands:**
• `/ping` — Check bot status
• `/date` — Current date/time (AD + BS)
• `/serverinfo` — Server information
• `/ai <prompt>` — Ask AI a question (rate limited)
• `/aistatus` — Check your AI cooldown
• `/kpwrite <message>` — Send message (authorized users)
• `/kpannounce <message>` — Send announcement (authorized users)
• `/reload` — Reload configuration (admins)

**Text Commands:**
• `!help` — This help message
• `!words` — Show trigger words
• `!reload-data` — Reload config (admins)

**AI Features:**
• Type `{AI_TRIGGER_PHRASE} your question` to ask AI
• Rate limit: 1 query per user every {AI_COOLDOWN_MINUTES} minutes
• Max prompt length: 500 characters

**AI Moderation (with permissions):**
• `{AI_TRIGGER_PHRASE} kick @user [reason]`
• `{AI_TRIGGER_PHRASE} ban @user [reason]`
• `{AI_TRIGGER_PHRASE} mute @user [reason]`
• `{AI_TRIGGER_PHRASE} unmute @user [reason]`

**Trigger Words:** {', '.join(TRIGGER_WORDS[:10])}{'...' if len(TRIGGER_WORDS) > 10 else ''}"""
    await ctx.send(help_text)

@bot.command(name="words")
async def words_command(ctx):
    if TRIGGER_WORDS:
        word_list = "📝 **Current trigger words:**\n" + "\n".join([f"• {word}" for word in TRIGGER_WORDS])
        if len(word_list) > 2000:
            for chunk in [word_list[i:i+1900] for i in range(0, len(word_list), 1900)]:
                await ctx.send(chunk)
        else:
            await ctx.send(word_list)
    else:
        await ctx.send("No trigger words configured.")

@bot.command(name="reload-data")
async def reload_data_command(ctx):
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

# ==================== TRIVIA ====================

TRIVIA_CACHE = []  # Cache fetched questions

async def fetch_trivia_question() -> dict | None:
    """Fetch a trivia question from Open Trivia DB (free, no key needed)"""
    url = "https://opentdb.com/api.php?amount=1&type=multiple"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                if data.get("response_code") == 0 and data.get("results"):
                    return data["results"][0]
    except Exception as e:
        print(f"Trivia fetch error: {e}")
    return None

class TriviaView(discord.ui.View):
    def __init__(self, correct: str, options: list[str], question: str):
        super().__init__(timeout=30)
        self.correct = correct
        self.answered = set()

        emojis = ["🇦", "🇧", "🇨", "🇩"]
        for i, option in enumerate(options):
            btn = discord.ui.Button(
                label=f"{emojis[i]} {option[:60]}",
                custom_id=f"trivia_{i}",
                style=discord.ButtonStyle.secondary
            )
            btn.callback = self.make_callback(option)
            self.add_item(btn)

    def make_callback(self, option: str):
        async def callback(interaction: discord.Interaction):
            if interaction.user.id in self.answered:
                await interaction.response.send_message("You already answered!", ephemeral=True)
                return
            self.answered.add(interaction.user.id)
            if option == self.correct:
                await interaction.response.send_message(f"✅ Correct! The answer was **{self.correct}**", ephemeral=True)
            else:
                await interaction.response.send_message(f"❌ Wrong! The correct answer was **{self.correct}**", ephemeral=True)
        return callback

@bot.tree.command(name="trivia", description="Get a random trivia question")
async def trivia_command(interaction: discord.Interaction):
    await interaction.response.defer()
    question_data = await fetch_trivia_question()
    if not question_data:
        await interaction.followup.send("❌ Could not fetch a trivia question. Try again in a moment.")
        return

    question = html.unescape(question_data["question"])
    correct = html.unescape(question_data["correct_answer"])
    incorrects = [html.unescape(a) for a in question_data["incorrect_answers"]]
    options = incorrects + [correct]
    random.shuffle(options)

    category = question_data.get("category", "General")
    difficulty = question_data.get("difficulty", "medium").title()
    diff_colors = {"Easy": discord.Color.green(), "Medium": discord.Color.orange(), "Hard": discord.Color.red()}

    embed = discord.Embed(
        title="🧠 Trivia Time!",
        description=f"**{question}**",
        color=diff_colors.get(difficulty, discord.Color.blurple())
    )
    embed.add_field(name="Category", value=category, inline=True)
    embed.add_field(name="Difficulty", value=difficulty, inline=True)
    embed.set_footer(text="You have 30 seconds to answer!")

    view = TriviaView(correct, options, question)
    await interaction.followup.send(embed=embed, view=view)

# ==================== WOULD YOU RATHER ====================

WYR_QUESTIONS = [
    ("be able to fly", "be able to breathe underwater"),
    ("always speak your mind", "never speak again"),
    ("live without music", "live without TV/movies"),
    ("be the funniest person in the room", "be the smartest person in the room"),
    ("have unlimited money but no friends", "have amazing friends but always be broke"),
    ("know when you'll die", "know how you'll die"),
    ("be famous but hated", "be unknown but loved"),
    ("only eat dal bhat every day", "never eat dal bhat again"),
    ("lose all your memories", "never make new ones"),
    ("be able to talk to animals", "speak all human languages"),
    ("always be 10 minutes late", "always be 2 hours early"),
    ("have free WiFi everywhere", "have free food everywhere"),
    ("never use social media again", "never watch Netflix again"),
    ("fight 100 duck-sized horses", "fight 1 horse-sized duck"),
    ("have 3 arms", "have 3 legs"),
    ("wake up every day in a new country", "never leave your home country"),
    ("be the best player on a losing team", "be the worst player on a winning team"),
    ("give up chai/coffee forever", "give up your favourite food forever"),
    ("have to sing everything you say", "have to dance everywhere you go"),
    ("have no internet for a month", "have no friends for a month"),
]

@bot.tree.command(name="wyr", description="Get a Would You Rather question")
async def wyr_command(interaction: discord.Interaction):
    option_a, option_b = random.choice(WYR_QUESTIONS)
    embed = discord.Embed(
        title="🤔 Would You Rather...",
        color=discord.Color.purple()
    )
    embed.add_field(name="🅰️ Option A", value=option_a.capitalize(), inline=False)
    embed.add_field(name="🅱️ Option B", value=option_b.capitalize(), inline=False)
    embed.set_footer(text="React with 🅰️ or 🅱️ to vote!")
    await interaction.response.send_message(embed=embed)
    msg = await interaction.original_response()
    await msg.add_reaction("🅰️")
    await msg.add_reaction("🅱️")

# ==================== TRUTH OR DARE ====================

TRUTHS = [
    "What's the most embarrassing thing you've done in public?",
    "What's a secret you've never told anyone in this server?",
    "Who in this server do you have a crush on?",
    "What's the biggest lie you've ever told?",
    "What's the most childish thing you still do?",
    "What's your most embarrassing childhood memory?",
    "Have you ever cheated on a test?",
    "What's the worst gift you've ever received?",
    "What's something you pretend to like but actually hate?",
    "What's the pettiest thing you've ever done?",
    "Have you ever blamed someone else for something you did?",
    "What's your biggest irrational fear?",
    "What's the most awkward date you've been on?",
    "What's a bad habit you have that no one knows about?",
    "What's the most embarrassing text you've sent to the wrong person?",
]

DARES = [
    "Type 'I love KP Oli' in the server chat.",
    "Change your nickname to 'Sala Boka' for 10 minutes.",
    "Send a voice message singing the first 10 seconds of a Nepali song.",
    "DM a random server member a compliment right now.",
    "Type everything in CAPS for the next 5 minutes.",
    "Send your most recent photo from your camera roll (no deleting!).",
    "Write a poem about dal bhat in 2 minutes.",
    "Let the person to your right pick your profile picture for 1 hour.",
    "Send a GIF that describes your mood right now.",
    "Use only emojis for your next 5 messages.",
    "Tag two people and say something genuinely nice about each.",
    "React to the last 10 messages in this channel.",
    "Send a voice note saying 'I am the best person in this server'.",
    "Speak only in questions for the next 3 minutes.",
    "Write a haiku about the last person who messaged in this channel.",
]

@bot.tree.command(name="truth", description="Get a random truth question")
async def truth_command(interaction: discord.Interaction):
    question = random.choice(TRUTHS)
    embed = discord.Embed(
        title="🫣 Truth!",
        description=f"**{question}**",
        color=discord.Color.blue()
    )
    embed.set_footer(text=f"Dare for {interaction.user.display_name} — no lying!")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="dare", description="Get a random dare")
async def dare_command(interaction: discord.Interaction):
    dare = random.choice(DARES)
    embed = discord.Embed(
        title="😈 Dare!",
        description=f"**{dare}**",
        color=discord.Color.red()
    )
    embed.set_footer(text=f"Dare for {interaction.user.display_name} — no chickening out!")
    await interaction.response.send_message(embed=embed)

# ==================== ROCK PAPER SCISSORS ====================

RPS_CHOICES = {"rock": "🪨", "paper": "📄", "scissors": "✂️"}
RPS_WINS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

class RPSView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=30)

    async def play(self, interaction: discord.Interaction, player_choice: str):
        bot_choice = random.choice(list(RPS_CHOICES.keys()))
        player_emoji = RPS_CHOICES[player_choice]
        bot_emoji = RPS_CHOICES[bot_choice]

        if player_choice == bot_choice:
            result = "🤝 It's a tie!"
            color = discord.Color.yellow()
        elif RPS_WINS[player_choice] == bot_choice:
            result = "🎉 You win!"
            color = discord.Color.green()
        else:
            result = "😂 Bot wins!"
            color = discord.Color.red()

        embed = discord.Embed(title="🪨📄✂️ Rock Paper Scissors", color=color)
        embed.add_field(name=f"You ({interaction.user.display_name})", value=f"{player_emoji} {player_choice.title()}", inline=True)
        embed.add_field(name="KP Bot", value=f"{bot_emoji} {bot_choice.title()}", inline=True)
        embed.add_field(name="Result", value=f"**{result}**", inline=False)

        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🪨 Rock", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "rock")

    @discord.ui.button(label="📄 Paper", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "paper")

    @discord.ui.button(label="✂️ Scissors", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.play(interaction, "scissors")

@bot.tree.command(name="rps", description="Play Rock Paper Scissors against the bot")
async def rps_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="🪨📄✂️ Rock Paper Scissors",
        description="Choose your weapon!",
        color=discord.Color.blurple()
    )
    await interaction.response.send_message(embed=embed, view=RPSView())

# ==================== USER INFO ====================

@bot.tree.command(name="userinfo", description="View info about a user")
@app_commands.describe(user="The user to look up (leave empty for yourself)")
async def userinfo_command(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    now = discord.utils.utcnow()

    account_age = (now - target.created_at).days
    join_age = (now - target.joined_at).days if target.joined_at else 0

    roles = [r.mention for r in reversed(target.roles) if r.name != "@everyone"]
    roles_str = " ".join(roles[:10]) if roles else "None"
    if len(target.roles) - 1 > 10:
        roles_str += f" *+{len(target.roles) - 11} more*"

    status_emojis = {
        discord.Status.online: "🟢 Online",
        discord.Status.idle: "🟡 Idle",
        discord.Status.dnd: "🔴 Do Not Disturb",
        discord.Status.offline: "⚫ Offline",
    }
    status = status_emojis.get(target.status, "⚫ Offline")

    badges = []
    if target.bot:
        badges.append("🤖 Bot")
    if target.guild_permissions.administrator:
        badges.append("👑 Admin")
    if target.premium_since:
        badges.append("💎 Server Booster")

    embed = discord.Embed(
        title=f"👤 {target.display_name}",
        color=target.color if target.color.value != 0 else discord.Color.blurple()
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="Username", value=str(target), inline=True)
    embed.add_field(name="ID", value=target.id, inline=True)
    embed.add_field(name="Status", value=status, inline=True)
    embed.add_field(name="Account Created", value=f"{target.created_at.strftime('%b %d, %Y')}\n*{account_age} days ago*", inline=True)
    embed.add_field(name="Joined Server", value=f"{target.joined_at.strftime('%b %d, %Y') if target.joined_at else 'Unknown'}\n*{join_age} days ago*", inline=True)
    embed.add_field(name="Nickname", value=target.nick or "None", inline=True)
    embed.add_field(name=f"Roles ({len(target.roles) - 1})", value=roles_str or "None", inline=False)
    if badges:
        embed.add_field(name="Badges", value=" · ".join(badges), inline=False)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

# ==================== ROLE INFO ====================

@bot.tree.command(name="roleinfo", description="View info about a role")
@app_commands.describe(role="The role to look up")
async def roleinfo_command(interaction: discord.Interaction, role: discord.Role):
    now = discord.utils.utcnow()
    age = (now - role.created_at).days
    member_count = len(role.members)

    # Key permissions to highlight
    key_perms = []
    perms = role.permissions
    if perms.administrator:       key_perms.append("Administrator")
    if perms.manage_guild:        key_perms.append("Manage Server")
    if perms.manage_channels:     key_perms.append("Manage Channels")
    if perms.manage_roles:        key_perms.append("Manage Roles")
    if perms.manage_messages:     key_perms.append("Manage Messages")
    if perms.kick_members:        key_perms.append("Kick Members")
    if perms.ban_members:         key_perms.append("Ban Members")
    if perms.mention_everyone:    key_perms.append("Mention Everyone")
    if perms.moderate_members:    key_perms.append("Timeout Members")

    color = role.color if role.color.value != 0 else discord.Color.light_grey()
    hex_color = str(role.color) if role.color.value != 0 else "#000000"

    embed = discord.Embed(
        title=f"🏷️ Role: {role.name}",
        color=color
    )
    embed.add_field(name="ID", value=role.id, inline=True)
    embed.add_field(name="Color", value=hex_color, inline=True)
    embed.add_field(name="Members", value=member_count, inline=True)
    embed.add_field(name="Created", value=f"{role.created_at.strftime('%b %d, %Y')}\n*{age} days ago*", inline=True)
    embed.add_field(name="Mentionable", value="✅ Yes" if role.mentionable else "❌ No", inline=True)
    embed.add_field(name="Hoisted", value="✅ Yes" if role.hoist else "❌ No", inline=True)
    embed.add_field(
        name="Key Permissions",
        value=", ".join(key_perms) if key_perms else "No special permissions",
        inline=False
    )
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed)

# ==================== REMINDER ====================

active_reminders = {}  # user_id -> list of reminder tasks

@bot.tree.command(name="remind", description="Set a reminder (e.g. 30m, 2h, 1d)")
@app_commands.describe(
    time="Time until reminder (e.g. 10m, 2h, 1d)",
    reminder="What to remind you about"
)
async def remind_command(interaction: discord.Interaction, time: str, reminder: str):
    # Parse time string
    time = time.lower().strip()
    seconds = 0
    pattern = re.findall(r'(\d+)([smhd])', time)
    if not pattern:
        await interaction.response.send_message(
            "❌ Invalid time format! Use: `30s`, `10m`, `2h`, `1d` or combinations like `1h30m`",
            ephemeral=True
        )
        return

    unit_map = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
    for value, unit in pattern:
        seconds += int(value) * unit_map[unit]

    if seconds < 10:
        await interaction.response.send_message("❌ Minimum reminder time is 10 seconds.", ephemeral=True)
        return
    if seconds > 7 * 86400:
        await interaction.response.send_message("❌ Maximum reminder time is 7 days.", ephemeral=True)
        return

    fire_time = discord.utils.utcnow() + timedelta(seconds=seconds)

    # Format display time
    parts = []
    remaining = seconds
    for unit, name in [(86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")]:
        if remaining >= unit:
            val = remaining // unit
            remaining %= unit
            parts.append(f"{val} {name}{'s' if val != 1 else ''}")
    time_str = ", ".join(parts)

    await interaction.response.send_message(
        f"⏰ Got it! I'll remind you about **{reminder}** in **{time_str}**.",
        ephemeral=False
    )

    async def send_reminder():
        await asyncio.sleep(seconds)
        try:
            embed = discord.Embed(
                title="⏰ Reminder!",
                description=reminder,
                color=discord.Color.yellow()
            )
            embed.set_footer(text=f"Set {time_str} ago")
            await interaction.user.send(embed=embed)
        except discord.Forbidden:
            # DMs closed — send in channel instead
            try:
                await interaction.channel.send(
                    f"⏰ {interaction.user.mention} — reminder: **{reminder}**"
                )
            except Exception:
                pass

    task = asyncio.create_task(send_reminder())
    user_reminders = active_reminders.setdefault(interaction.user.id, [])
    user_reminders.append(task)

# ==================== AFK SYSTEM ====================

afk_users = {}  # user_id -> {"reason": str, "time": datetime}

@bot.tree.command(name="afk", description="Set yourself as AFK")
@app_commands.describe(reason="Reason for being AFK (optional)")
async def afk_command(interaction: discord.Interaction, reason: str = "AFK"):
    afk_users[interaction.user.id] = {
        "reason": reason,
        "time": discord.utils.utcnow()
    }
    await interaction.response.send_message(
        f"💤 **{interaction.user.display_name}** is now AFK: *{reason}*"
    )
    # Try to add [AFK] to nickname
    try:
        current_nick = interaction.user.display_name
        if not current_nick.startswith("[AFK]"):
            await interaction.user.edit(nick=f"[AFK] {current_nick}"[:32])
    except discord.Forbidden:
        pass

# AFK check is handled in on_message — we add it there via a hook
_original_on_message = bot.on_message if hasattr(bot, '_afk_patched') else None

@bot.listen('on_message')
async def afk_listener(message):
    if message.author.bot:
        return

    # Remove AFK if the AFK user sends a message
    if message.author.id in afk_users:
        afk_data = afk_users.pop(message.author.id)
        elapsed = discord.utils.utcnow() - afk_data["time"]
        minutes = int(elapsed.total_seconds() // 60)
        time_str = f"{minutes} minute{'s' if minutes != 1 else ''}" if minutes else "less than a minute"
        await message.channel.send(
            f"👋 Welcome back, {message.author.mention}! You were AFK for **{time_str}**.",
            delete_after=10
        )
        # Remove [AFK] from nickname
        try:
            if message.author.display_name.startswith("[AFK]"):
                new_nick = message.author.display_name[6:].strip() or None
                await message.author.edit(nick=new_nick)
        except discord.Forbidden:
            pass

    # Notify if someone pings an AFK user
    for mentioned in message.mentions:
        if mentioned.id in afk_users and mentioned.id != message.author.id:
            afk_data = afk_users[mentioned.id]
            elapsed = discord.utils.utcnow() - afk_data["time"]
            minutes = int(elapsed.total_seconds() // 60)
            time_str = f"{minutes} minute{'s' if minutes != 1 else ''}" if minutes else "just now"
            await message.channel.send(
                f"💤 **{mentioned.display_name}** is AFK: *{afk_data['reason']}* — went AFK {time_str} ago.",
                delete_after=10
            )

# ==================== LOCK / UNLOCK ====================

@bot.tree.command(name="lock", description="Lock the current channel so members can't send messages")
@app_commands.describe(reason="Reason for locking (optional)")
async def lock_command(interaction: discord.Interaction, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ You need **Manage Channels** permission!", ephemeral=True)
        return

    channel = interaction.channel
    everyone = interaction.guild.default_role

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
async def unlock_command(interaction: discord.Interaction, reason: str = "No reason provided"):
    if not interaction.user.guild_permissions.manage_channels:
        await interaction.response.send_message("❌ You need **Manage Channels** permission!", ephemeral=True)
        return

    channel = interaction.channel
    everyone = interaction.guild.default_role

    try:
        await channel.set_permissions(everyone, send_messages=None)  # Reset to default
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

# ==================== MAIN ====================

def main():
    load_bot_data()
    token = os.getenv("TOKEN")
    if not token:
        print("❌ ERROR: No bot token found!")
        print("Please create a .env file with:")
        print("TOKEN=your_bot_token_here")
        print("GEMINI_API_KEY=your_gemini_api_key_here")
        print("WEATHER_API_KEY=your_openweathermap_key_here")
        return
    try:
        print("🚀 Starting Discord Bot...")
        bot.run(token)
    except discord.LoginFailure:
        print("❌ ERROR: Invalid bot token!")
    except Exception as e:
        print(f"❌ ERROR: Failed to start bot: {e}")

if __name__ == "__main__":
    main()