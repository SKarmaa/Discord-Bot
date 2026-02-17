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

# Music-related imports
try:
    import yt_dlp as youtube_dl
    YTDL_AVAILABLE = True
    print("✅ yt-dlp imported successfully")
except ImportError:
    print("❌ yt-dlp not found. Install with: pip install yt-dlp")
    YTDL_AVAILABLE = False

# Load environment variables
load_dotenv()

# Bot configuration
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.voice_states = True  # Required for voice channel functionality

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

# Music Player Configuration - Optimized and Robust
YTDL_OPTIONS = {
    'cookiefile': 'cookies.txt',
    'format': 'bestaudio[ext=webm]/bestaudio/best',
    'noplaylist': False,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'extract_flat': False,
    'geo_bypass': True,
    'socket_timeout': 30,
    'retries': 10,
    'fragment_retries': 10,
    'extractor_retries': 5,
    'file_access_retries': 5,
    'http_chunk_size': 10485760,
    'noprogress': True,
    'no_check_certificate': True,
    'prefer_insecure': False,
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -af "loudnorm=I=-16:TP=-1.5:LRA=11, acompressor=threshold=0.089:ratio=9:attack=200:release=1000, equalizer=f=100:width_type=h:width=200:g=2, equalizer=f=3000:width_type=h:width=1000:g=-2"'
}

# Simpler options for compatibility (fallback)
FFMPEG_OPTIONS_SIMPLE = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn -af "loudnorm=I=-16:TP=-1.5:LRA=11"'
}

class MusicQueue:
    """Manages music queue for a guild"""
    
    def __init__(self):
        self.songs = []
        self.current = None
        self.loop = False
        self.volume = 1.0  # Set to 1.0 since FFmpeg handles normalization
    
    def add(self, song):
        """Add a song to queue"""
        self.songs.append(song)
    
    def next(self):
        """Get next song"""
        if self.loop and self.current:
            return self.current
        if self.songs:
            self.current = self.songs.pop(0)
            return self.current
        self.current = None
        return None
    
    def clear(self):
        """Clear the queue"""
        self.songs = []
        self.current = None
    
    def shuffle(self):
        """Shuffle the queue"""
        random.shuffle(self.songs)
    
    def remove(self, index):
        """Remove song at index"""
        if 0 <= index < len(self.songs):
            return self.songs.pop(index)
        return None
    
    def __len__(self):
        return len(self.songs)

class YTDLSource(discord.PCMVolumeTransformer):
    """YouTube audio source"""
    
    def __init__(self, source, *, data, volume=0.25):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.webpage_url = data.get('webpage_url')
        self.duration = data.get('duration')
        self.thumbnail = data.get('thumbnail')
        self.uploader = data.get('uploader')
    
    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        """Create audio source from URL - Robust extraction"""
        loop = loop or asyncio.get_event_loop()
        
        # Add ytsearch: prefix if not a URL
        if not url.startswith(('http://', 'https://', 'ytsearch:')):
            url = f"ytsearch:{url}"
        
        ytdl = youtube_dl.YoutubeDL(YTDL_OPTIONS)
        
        def extract():
            try:
                return ytdl.extract_info(url, download=False)
            except Exception as e:
                print(f"Extraction error: {e}")
                # Try with different search prefix
                if 'ytsearch:' in url:
                    alt_url = url.replace('ytsearch:', 'ytsearch1:')
                    print(f"Retrying with: {alt_url}")
                    return ytdl.extract_info(alt_url, download=False)
                raise
        
        try:
            data = await asyncio.wait_for(
                loop.run_in_executor(None, extract),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            print(f"⏱️ Timeout for: {url}")
            return None
        except Exception as e:
            print(f"❌ Extraction failed: {e}")
            return None
        
        if not data:
            return None
        
        # Handle search results
        if 'entries' in data:
            entries = []
            for entry in data['entries']:
                if entry:
                    entries.append(entry)
            return entries if entries else None
        else:
            return [data]
    
    @classmethod
    async def create_source(cls, data, *, loop=None, volume=1.0):
        """Create audio source from data with optimized audio processing"""
        loop = loop or asyncio.get_event_loop()
        
        try:
            filename = data['filename']
            
            # Log the URL being played for debugging
            print(f"Creating audio source from: {filename[:100]}...")
            
            # Try with full audio processing first
            try:
                source = discord.FFmpegPCMAudio(
                    filename,
                    **FFMPEG_OPTIONS
                )
                print("✅ Using advanced audio processing (normalization + compression + EQ)")
            except Exception as e:
                # Fallback to simple normalization if advanced filters fail
                print(f"⚠️  Advanced filters failed, using simple normalization: {e}")
                source = discord.FFmpegPCMAudio(
                    filename,
                    **FFMPEG_OPTIONS_SIMPLE
                )
            
            return cls(source, data=data['data'], volume=volume)
        except Exception as e:
            print(f"Error creating audio source: {e}")
            raise

class MusicPlayer:
    """Music player for each guild"""
    
    def __init__(self, bot, guild_id):
        self.bot = bot
        self.guild_id = guild_id
        self.queue = MusicQueue()
        self.current_source = None
        self.voice_client = None
        self.is_playing = False
        self.is_paused = False
        self.skip_requested = False
        self.last_channel = None  # Store last voice channel for reconnection
        
    async def ensure_voice_connection(self):
        """Ensure voice client is connected, attempt reconnection if needed"""
        if self.voice_client and self.voice_client.is_connected():
            return True
        
        if self.last_channel:
            try:
                print(f"🔄 Attempting to reconnect to voice channel...")
                self.voice_client = await self.last_channel.connect()
                print(f"✅ Reconnected to voice channel")
                return True
            except Exception as e:
                print(f"❌ Failed to reconnect: {e}")
                return False
        
        return False
        
    async def play_next(self):
        """Play next song in queue with error recovery"""
        if self.skip_requested:
            self.skip_requested = False
        
        # Check if voice client is still valid
        if not self.voice_client or not self.voice_client.is_connected():
            print("Voice client disconnected, stopping playback")
            self.is_playing = False
            self.current_source = None
            return
        
        song_data = self.queue.next()
        
        if song_data:
            max_retries = 3
            retry_count = 0
            
            while retry_count < max_retries:
                try:
                    # Re-fetch the audio URL to avoid expiration issues
                    if retry_count > 0:
                        print(f"Retry attempt {retry_count}/{max_retries} for: {song_data['data'].get('title', 'Unknown')}")
                        await asyncio.sleep(2)
                        
                        # Re-extract the URL
                        ytdl = youtube_dl.YoutubeDL(YTDL_OPTIONS)
                        video_url = song_data['data'].get('webpage_url')
                        if video_url:
                            fresh_data = await self.bot.loop.run_in_executor(
                                None, 
                                lambda: ytdl.extract_info(video_url, download=False)
                            )
                            song_data['filename'] = fresh_data.get('url')
                            song_data['data'] = fresh_data
                    
                    self.current_source = await YTDLSource.create_source(
                        song_data, 
                        loop=self.bot.loop, 
                        volume=self.queue.volume
                    )
                    
                    def after_playing(error):
                        if error:
                            print(f"Player error: {error}")
                            # Check if it's a known retriable error
                            error_str = str(error).lower()
                            if any(x in error_str for x in ['connection', 'timeout', 'reset']):
                                print("Network error detected, will retry next song")
                        
                        # Schedule the next song
                        coro = self.play_next()
                        future = asyncio.run_coroutine_threadsafe(coro, self.bot.loop)
                        try:
                            future.result(timeout=5)
                        except asyncio.TimeoutError:
                            print("Timeout scheduling next song")
                        except Exception as e:
                            print(f"Error in after_playing callback: {e}")
                    
                    self.voice_client.play(
                        self.current_source,
                        after=after_playing
                    )
                    
                    self.is_playing = True
                    self.is_paused = False
                    
                    print(f"✅ Now playing: {song_data['data'].get('title', 'Unknown')}")
                    break  # Success, exit retry loop
                    
                except Exception as e:
                    retry_count += 1
                    print(f"❌ Error playing song (attempt {retry_count}/{max_retries}): {e}")
                    
                    if retry_count >= max_retries:
                        print(f"Failed to play after {max_retries} attempts, skipping to next song")
                        import traceback
                        traceback.print_exc()
                        # Try next song after max retries
                        await asyncio.sleep(2)
                        await self.play_next()
                        return
        else:
            self.is_playing = False
            self.current_source = None
            print("📭 Queue is empty, playback stopped")
    
    async def add_to_queue(self, url):
        """Add song(s) to queue from URL or search query - Direct streaming"""
        try:
            print(f"🔍 Fetching stream URL for: {url}")
            entries = await YTDLSource.from_url(url, loop=self.bot.loop, stream=True)
            
            if not entries:
                print("❌ No entries returned from yt-dlp")
                return None
            
            added_songs = []
            for entry in entries:
                # Get the direct streaming URL
                audio_url = entry.get('url')
                webpage_url = entry.get('webpage_url')
                
                if not audio_url:
                    print(f"⚠️  No audio URL found for: {entry.get('title', 'Unknown')}")
                    continue
                
                song_info = {
                    'filename': audio_url,  # Direct streaming URL from YouTube
                    'data': entry
                }
                self.queue.add(song_info)
                title = entry.get('title', 'Unknown')
                added_songs.append(title)
                print(f"✅ Added to queue (direct stream): {title}")
            
            return added_songs
        except Exception as e:
            print(f"❌ Error adding to queue: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def pause(self):
        """Pause playback"""
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            self.is_paused = True
            return True
        return False
    
    def resume(self):
        """Resume playback"""
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            self.is_paused = False
            return True
        return False
    
    def skip(self):
        """Skip current song"""
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.skip_requested = True
            self.voice_client.stop()
            return True
        return False
    
    def stop(self):
        """Stop playback and clear queue"""
        self.queue.clear()
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()
            return True
        return False
    
    def set_volume(self, volume):
        """Set volume (0.0 to 1.0)"""
        self.queue.volume = max(0.0, min(1.0, volume))
        if self.current_source:
            self.current_source.volume = self.queue.volume

# Global music players dictionary
music_players = {}

def get_music_player(guild_id):
    """Get or create music player for guild"""
    if guild_id not in music_players:
        music_players[guild_id] = MusicPlayer(bot, guild_id)
    return music_players[guild_id]

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

    url = f"https://generativelanguage.googleapis.com/v1/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}"
    
    headers = {
        'Content-Type': 'application/json'
    }
    
    data = {
        "system_instruction": {
                "parts": [
                    {
                        "text": """You are KP - a goofy, funny Nepali boy from Kathmandu. 
            Your personality:
            - Use Nepali slang mixed with English (Nepali slang words: "ke garne", "yaar", "bro", "sala", "kta/ktis", "timepass", "jugaad", "khoi", "aile", "ekdam", "haina", "ni", "aba")
            - Be humorous and sarcastic but friendly
            - Occasionally throw in Nepali words naturally mid-sentence
            - Make jokes or funny observations when relevant
            - Act clueless and goofy sometimes but still give the correct answer
            - Use "haha", "lol", "oof" casually
            - Never be rude or offensive

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

def format_duration(seconds):
    """Format duration in seconds to MM:SS"""
    if not seconds:
        return "Unknown"
    mins = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{mins}:{secs:02d}"

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

# ==================== MUSIC COMMANDS ====================

@bot.tree.command(name="play", description="Play a song from YouTube (URL or search query)")
@app_commands.describe(query="YouTube URL or song name to search")
async def play_command(interaction: discord.Interaction, query: str):
    """Play music in voice channel"""
    if not YTDL_AVAILABLE:
        await interaction.response.send_message("❌ Music feature not available. Install yt-dlp: `pip install yt-dlp`")
        return
    
    # Check if user is in voice channel
    if not interaction.user.voice:
        await interaction.response.send_message("❌ You need to be in a voice channel to play music!")
        return
    
    # Send immediate response
    await interaction.response.send_message("🔍 Searching YouTube...", ephemeral=False)
    
    voice_channel = interaction.user.voice.channel
    player = get_music_player(interaction.guild.id)
    
    # Store the voice channel for potential reconnection
    player.last_channel = voice_channel
    
    # Connect to voice channel if not already connected
    if not player.voice_client or not player.voice_client.is_connected():
        try:
            player.voice_client = await voice_channel.connect()
            print(f"🔊 Connected to voice channel: {voice_channel.name}")
        except Exception as e:
            await interaction.edit_original_response(content=f"❌ Failed to connect to voice channel: {str(e)}")
            return
    elif player.voice_client.channel != voice_channel:
        await player.voice_client.move_to(voice_channel)
        print(f"🔄 Moved to voice channel: {voice_channel.name}")
    
    # Add to queue asynchronously
    try:
        # Format query properly - don't add prefix if already a URL
        search_query = query
        if not query.startswith(('http://', 'https://')):
            # Just pass the raw search query
            search_query = query
        
        added_songs = await player.add_to_queue(search_query)
        
        if not added_songs:
            await interaction.edit_original_response(content="❌ Could not find any results. Try:\n• Different search terms\n• Direct YouTube URL\n• More specific artist/song name")
            return
        
        # Create embed
        if len(added_songs) == 1:
            embed = discord.Embed(
                title="🎵 Added to Queue",
                description=f"**{added_songs[0]}**",
                color=discord.Color.green()
            )
            if player.is_playing:
                embed.set_footer(text=f"Position in queue: {len(player.queue)}")
            else:
                embed.set_footer(text="Playing now!")
        else:
            embed = discord.Embed(
                title="🎵 Playlist Added to Queue",
                description=f"Added **{len(added_songs)}** songs",
                color=discord.Color.green()
            )
        
        await interaction.edit_original_response(content=None, embed=embed)
        
        # Start playing if not already playing
        if not player.is_playing and not player.is_paused:
            await player.play_next()
            
    except asyncio.TimeoutError:
        await interaction.edit_original_response(content="❌ Search timed out. YouTube might be slow. Please try again.")
    except Exception as e:
        print(f"❌ Play command error: {e}")
        import traceback
        traceback.print_exc()
        await interaction.edit_original_response(content=f"❌ Error: Could not process request. Try a direct YouTube URL or update yt-dlp:\n`pip install --upgrade yt-dlp`")

@bot.tree.command(name="pause", description="Pause the current song")
async def pause_command(interaction: discord.Interaction):
    """Pause music playback"""
    player = get_music_player(interaction.guild.id)
    
    if player.pause():
        await interaction.response.send_message("⏸️ Paused playback")
    else:
        await interaction.response.send_message("❌ Nothing is playing!")

@bot.tree.command(name="resume", description="Resume the paused song")
async def resume_command(interaction: discord.Interaction):
    """Resume music playback"""
    player = get_music_player(interaction.guild.id)
    
    if player.resume():
        await interaction.response.send_message("▶️ Resumed playback")
    else:
        await interaction.response.send_message("❌ Nothing is paused!")

@bot.tree.command(name="skip", description="Skip the current song")
async def skip_command(interaction: discord.Interaction):
    """Skip current song"""
    player = get_music_player(interaction.guild.id)
    
    if player.skip():
        await interaction.response.send_message("⏭️ Skipped current song")
    else:
        await interaction.response.send_message("❌ Nothing is playing!")

@bot.tree.command(name="stop", description="Stop playback and clear the queue")
async def stop_command(interaction: discord.Interaction):
    """Stop music and clear queue"""
    player = get_music_player(interaction.guild.id)
    
    if player.stop():
        await interaction.response.send_message("⏹️ Stopped playback and cleared queue")
    else:
        await interaction.response.send_message("❌ Nothing is playing!")

@bot.tree.command(name="queue", description="Show the music queue")
async def queue_command(interaction: discord.Interaction):
    """Display music queue"""
    player = get_music_player(interaction.guild.id)
    
    if not player.current_source and len(player.queue) == 0:
        await interaction.response.send_message("📭 Queue is empty!")
        return
    
    embed = discord.Embed(
        title="🎵 Music Queue",
        color=discord.Color.blue()
    )
    
    # Current song
    if player.current_source:
        current = player.current_source.data
        duration = format_duration(current.get('duration'))
        status = "⏸️ Paused" if player.is_paused else "▶️ Playing"
        embed.add_field(
            name=f"{status} - Now",
            value=f"**{current.get('title', 'Unknown')}**\nDuration: {duration}",
            inline=False
        )
    
    # Queue
    if len(player.queue) > 0:
        queue_text = ""
        for i, song in enumerate(player.queue.songs[:10], 1):
            title = song['data'].get('title', 'Unknown')
            duration = format_duration(song['data'].get('duration'))
            queue_text += f"`{i}.` **{title}** ({duration})\n"
        
        if len(player.queue) > 10:
            queue_text += f"\n*...and {len(player.queue) - 10} more songs*"
        
        embed.add_field(
            name=f"Up Next ({len(player.queue)} songs)",
            value=queue_text,
            inline=False
        )
    
    embed.set_footer(text=f"Loop: {'✅ Enabled' if player.queue.loop else '❌ Disabled'}")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="nowplaying", description="Show currently playing song")
async def nowplaying_command(interaction: discord.Interaction):
    """Show current song"""
    player = get_music_player(interaction.guild.id)
    
    if not player.current_source:
        await interaction.response.send_message("❌ Nothing is playing!")
        return
    
    current = player.current_source.data
    
    embed = discord.Embed(
        title="🎵 Now Playing",
        description=f"**{current.get('title', 'Unknown')}**",
        color=discord.Color.green()
    )
    
    if current.get('thumbnail'):
        embed.set_thumbnail(url=current['thumbnail'])
    
    embed.add_field(name="Duration", value=format_duration(current.get('duration')), inline=True)
    embed.add_field(name="Uploader", value=current.get('uploader', 'Unknown'), inline=True)
    embed.add_field(name="Status", value="⏸️ Paused" if player.is_paused else "▶️ Playing", inline=True)
    
    if current.get('webpage_url'):
        embed.add_field(name="URL", value=f"[Watch on YouTube]({current['webpage_url']})", inline=False)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="volume", description="Set the playback volume (0-100) - Note: Audio is pre-normalized")
@app_commands.describe(level="Volume level (0-100)")
async def volume_command(interaction: discord.Interaction, level: int):
    """Set volume"""
    if not 0 <= level <= 100:
        await interaction.response.send_message("❌ Volume must be between 0 and 100!")
        return
    
    player = get_music_player(interaction.guild.id)
    player.set_volume(level / 100)
    
    await interaction.response.send_message(f"🔊 Volume set to {level}% (Audio is normalized for consistency)")

@bot.tree.command(name="loop", description="Toggle loop mode for current song")
async def loop_command(interaction: discord.Interaction):
    """Toggle loop mode"""
    player = get_music_player(interaction.guild.id)
    player.queue.loop = not player.queue.loop
    
    status = "✅ enabled" if player.queue.loop else "❌ disabled"
    await interaction.response.send_message(f"🔁 Loop mode {status}")

@bot.tree.command(name="shuffle", description="Shuffle the queue")
async def shuffle_command(interaction: discord.Interaction):
    """Shuffle queue"""
    player = get_music_player(interaction.guild.id)
    
    if len(player.queue) < 2:
        await interaction.response.send_message("❌ Not enough songs in queue to shuffle!")
        return
    
    player.queue.shuffle()
    await interaction.response.send_message("🔀 Queue shuffled!")

@bot.tree.command(name="remove", description="Remove a song from queue")
@app_commands.describe(position="Position in queue (1, 2, 3...)")
async def remove_command(interaction: discord.Interaction, position: int):
    """Remove song from queue"""
    player = get_music_player(interaction.guild.id)
    
    if position < 1 or position > len(player.queue):
        await interaction.response.send_message(f"❌ Invalid position! Queue has {len(player.queue)} songs.")
        return
    
    removed = player.queue.remove(position - 1)
    if removed:
        await interaction.response.send_message(f"✅ Removed **{removed['data'].get('title', 'Unknown')}** from queue")
    else:
        await interaction.response.send_message("❌ Failed to remove song!")

@bot.tree.command(name="clear", description="Clear the entire queue")
async def clear_command(interaction: discord.Interaction):
    """Clear queue"""
    player = get_music_player(interaction.guild.id)
    
    if len(player.queue) == 0:
        await interaction.response.send_message("❌ Queue is already empty!")
        return
    
    count = len(player.queue)
    player.queue.clear()
    await interaction.response.send_message(f"🗑️ Cleared {count} songs from queue")

@bot.tree.command(name="leave", description="Make the bot leave the voice channel")
async def leave_command(interaction: discord.Interaction):
    """Disconnect from voice"""
    player = get_music_player(interaction.guild.id)
    
    if player.voice_client and player.voice_client.is_connected():
        player.stop()
        await player.voice_client.disconnect()
        await interaction.response.send_message("👋 Left voice channel")
    else:
        await interaction.response.send_message("❌ Not connected to a voice channel!")

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
    """AI query via slash command"""
    user_id = interaction.user.id
    can_query, remaining_seconds = ai_rate_limiter.can_query(user_id)
    
    if not can_query:
        remaining_time = ai_rate_limiter.get_remaining_time(user_id)
        await interaction.response.send_message(
            f"⏰ Please wait **{remaining_time}** before asking another question!\n"
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

# Text Commands
@bot.command(name="help")
async def help_command(ctx):
    """Show help information"""
    help_text = f"""**Discord Bot Commands:**

**Music Commands:**
• `/play <song>` - Play a song (YouTube URL or search)
• `/pause` - Pause playback
• `/resume` - Resume playback
• `/skip` - Skip current song
• `/stop` - Stop and clear queue
• `/queue` - Show music queue
• `/nowplaying` - Show current song
• `/volume <0-100>` - Set volume
• `/loop` - Toggle loop mode
• `/shuffle` - Shuffle queue
• `/remove <position>` - Remove song from queue
• `/clear` - Clear queue
• `/leave` - Leave voice channel

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
    
    if not YTDL_AVAILABLE:
        print("⚠️  WARNING: yt-dlp not installed!")
        print("Music features will not work. Install with: pip install yt-dlp")
        print()
    
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