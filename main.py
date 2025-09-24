import os
import json
import random
import re
import asyncio
from datetime import datetime
from typing import Dict, List, Optional
from nepali_datetime import nepali_datetime

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


def load_bot_data():
    """Load bot configuration and responses from JSON file"""
    global BOT_DATA, WITTY_RESPONSES, WELCOME_MESSAGES, CONFIG, TRIGGER_WORDS
    
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
    
    print("Created default bot_data.json - please configure your IDs!")


def reload_bot_data():
    """Reload configuration from file"""
    load_bot_data()
    print("Bot data reloaded successfully!")


def find_trigger_words(message_content: str) -> List[str]:
    """Find trigger words in message content"""
    found_words = []
    message_lower = message_content.lower()
    
    for word in TRIGGER_WORDS:
        pattern = r'\b' + re.escape(word.lower()) + r'\b'
        if re.search(pattern, message_lower):
            found_words.append(word)
    
    return found_words


def get_witty_response(trigger_word: str) -> Optional[str]:
    """Get random response for trigger word"""
    responses = WITTY_RESPONSES.get(trigger_word)
    return random.choice(responses) if responses else None


def get_welcome_message(member: discord.Member) -> str:
    """Get formatted welcome message"""
    template = random.choice(WELCOME_MESSAGES) if WELCOME_MESSAGES else "Welcome {user}!"
    return template.format(user=member.mention)


def process_mentions(message_text: str, guild: discord.Guild) -> str:
    """Convert @userID format to proper Discord mentions"""
    pattern = r'@(\d+)'
    
    def replace_mention(match):
        user_id = int(match.group(1))
        member = guild.get_member(user_id)
        return member.mention if member else f'<@{user_id}>'
    
    return re.sub(pattern, replace_mention, message_text)


@bot.event
async def on_ready():
    """Called when bot is ready"""
    print(f'Bot logged in as {bot.user}')
    print(f'Connected to {len(bot.guilds)} servers')
    
    for guild in bot.guilds:
        print(f'  - {guild.name} (ID: {guild.id}) - {guild.member_count} members')
    
    print(f'Watching for trigger words: {", ".join(TRIGGER_WORDS)}')
    print(f'Members Intent: {"✅ ENABLED" if bot.intents.members else "❌ DISABLED"}')
    print(f'Message Content Intent: {"✅ ENABLED" if bot.intents.message_content else "❌ DISABLED"}')
    
    # Sync slash commands
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} slash commands")
        for command in synced:
            print(f"  - /{command.name}: {command.description}")
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    print("Bot is ready!")


@bot.event
async def on_member_join(member: discord.Member):
    """Welcome new members"""
    print(f"New member joined: {member.name} (ID: {member.id})")
    
    # Get welcome channel
    welcome_channel_id = CONFIG.get("welcome_channel_id")
    channel = bot.get_channel(welcome_channel_id) if welcome_channel_id else None
    
    # Find suitable channel if config doesn't work
    if not channel:
        for ch in member.guild.text_channels:
            if ch.name.lower() in ['welcome', 'general', 'main', 'lobby']:
                channel = ch
                break
    
    # Use first available channel as fallback
    if not channel and member.guild.text_channels:
        channel = member.guild.text_channels[0]
    
    if channel:
        try:
            welcome_msg = get_welcome_message(member)
            await channel.send(welcome_msg)
            print(f"Welcome message sent to {member.name} in #{channel.name}")
        except Exception as e:
            print(f"Failed to send welcome message: {e}")


@bot.event
async def on_message(message):
    """Handle incoming messages"""
    # Ignore bot messages
    if message.author.bot:
        return
    
    # React to special user mentions
    samu_user_id = CONFIG.get("samu_user_id")
    if samu_user_id and any(user.id == samu_user_id for user in message.mentions):
        if not message.reference:  # Don't react to replies
            reactions = CONFIG.get("samu_tag_reactions", ["👋"])
            reaction = random.choice(reactions)
            await message.add_reaction(reaction)
            print(f"Added {reaction} reaction - special user was mentioned")
    
    # Check for trigger words
    trigger_words = find_trigger_words(message.content)
    if trigger_words:
        chosen_word = random.choice(trigger_words)
        response = get_witty_response(chosen_word)
        
        if response:
            # Sometimes mention the user (20% chance)
            if random.random() < 0.2:
                response = f"{message.author.mention} {response}"
            
            await message.channel.send(response)
            
            # Sometimes add reaction (10% chance)
            if random.random() < 0.1:
                reactions = CONFIG.get("general_reactions", ["👍"])
                await message.add_reaction(random.choice(reactions))
            
            print(f"Responded to '{chosen_word}' from {message.author}")
    
    # Process commands
    await bot.process_commands(message)


# Slash Commands
@bot.tree.command(name="kpannounce", description="Send announcement with @everyone (Authorized users only)")
async def announce_command(interaction: discord.Interaction, message: str):
    """Send announcement to general channel"""
    
    # Check authorization
    authorized_user_id = CONFIG.get("write_command_user_id")
    if not authorized_user_id or interaction.user.id != authorized_user_id:
        await interaction.response.send_message(
            "❌ Access Denied: You are not authorized to use this command.",
            ephemeral=True
        )
        print(f"Unauthorized /kpannounce attempt by {interaction.user}")
        return
    
    # Check channel restriction
    command_channel_id = CONFIG.get("write_command_channel_id")
    if command_channel_id and interaction.channel.id != command_channel_id:
        await interaction.response.send_message(
            f"❌ Wrong Channel: This command can only be used in <#{command_channel_id}>",
            ephemeral=True
        )
        return
    
    # Find target channel
    general_channel_id = CONFIG.get("general_channel_id")
    target_channel = bot.get_channel(general_channel_id) if general_channel_id else None
    
    if not target_channel:
        # Search for general channel
        for ch in interaction.guild.text_channels:
            if 'general' in ch.name.lower():
                target_channel = ch
                break
    
    if not target_channel:
        await interaction.response.send_message(
            "❌ Error: Could not find target channel.",
            ephemeral=True
        )
        return
    
    try:
        processed_message = process_mentions(message, interaction.guild)
        final_message = f"@everyone {processed_message}"
        
        await target_channel.send(final_message)
        await interaction.response.send_message("✅ Announcement sent successfully!", ephemeral=True)
        print(f"Announcement sent by {interaction.user}: {final_message[:50]}...")
        
    except discord.Forbidden:
        await interaction.response.send_message(
            "❌ Permission Error: Bot lacks permission to send messages or mention @everyone",
            ephemeral=True
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)
        print(f"Error in announce command: {e}")


@bot.tree.command(name="kpwrite", description="Send message to general chat (Authorized users only)")
async def write_command(interaction: discord.Interaction, message: str):
    """Send message to general channel"""
    
    # Check authorization
    authorized_user_id = CONFIG.get("write_command_user_id")
    if not authorized_user_id or interaction.user.id != authorized_user_id:
        await interaction.response.send_message(
            "❌ Access Denied: You are not authorized to use this command.",
            ephemeral=True
        )
        return
    
    # Check channel restriction
    command_channel_id = CONFIG.get("write_command_channel_id")
    if command_channel_id and interaction.channel.id != command_channel_id:
        await interaction.response.send_message(
            f"❌ Wrong Channel: This command can only be used in <#{command_channel_id}>",
            ephemeral=True
        )
        return
    
    # Find target channel
    general_channel_id = CONFIG.get("general_channel_id")
    target_channel = bot.get_channel(general_channel_id) if general_channel_id else None
    
    if not target_channel:
        for ch in interaction.guild.text_channels:
            if 'general' in ch.name.lower():
                target_channel = ch
                break
    
    if not target_channel:
        await interaction.response.send_message(
            "❌ Error: Could not find target channel.",
            ephemeral=True
        )
        return
    
    try:
        processed_message = process_mentions(message, interaction.guild)
        await target_channel.send(processed_message)
        await interaction.response.send_message("✅ Message sent", ephemeral=True)
        print(f"Message sent by {interaction.user}: {processed_message[:50]}...")
        
    except Exception as e:
        await interaction.response.send_message(f"❌ Error: {str(e)}", ephemeral=True)


@bot.tree.command(name="kpprotest", description="Send protest message (Authorized users only)")
async def protest_command(interaction: discord.Interaction):
    """Send multi-part protest message"""
    
    # Check authorization
    authorized_user_id = CONFIG.get("write_command_user_id")
    if not authorized_user_id or interaction.user.id != authorized_user_id:
        await interaction.response.send_message(
            "❌ Access Denied: You are not authorized to use this command.",
            ephemeral=True
        )
        return
    
    # Check channel restriction
    command_channel_id = CONFIG.get("write_command_channel_id")
    if command_channel_id and interaction.channel.id != command_channel_id:
        await interaction.response.send_message(
            f"❌ Wrong Channel: This command can only be used in <#{command_channel_id}>",
            ephemeral=True
        )
        return
    
    # Find target channel
    general_channel_id = CONFIG.get("general_channel_id")
    target_channel = bot.get_channel(general_channel_id) if general_channel_id else None
    
    if not target_channel:
        for ch in interaction.guild.text_channels:
            if 'general' in ch.name.lower():
                target_channel = ch
                break
    
    if not target_channel:
        await interaction.response.send_message(
            "❌ Error: Could not find target channel.",
            ephemeral=True
        )
        return
    
    # Respond immediately to avoid timeout
    await interaction.response.send_message("🚀 Sending protest message...", ephemeral=True)
    
    # Protest message parts
    protest_parts = [
        """@everyone Friends,

Take a step back and carefully think about the protest you are organizing against the government. While your enthusiasm and courage are admirable, protests are not something you can carry out in a short burst of optimism.

History has shown the dangers of rushing into such movements without proper preparation. Take example the recent protest led by a prominent political figure, Durga Parsai. It failed to achieve its goals. Instead, it resulted in chaos, injuries, and tragic loss of lives. If someone with experience, resources, and influence could not succeed, you must recognize the risks of moving forward unprepared.""",

        """You are challenging a deeply settled system. Such movements require:

- Grassroots organizations or advocacy groups to guide and protect participants.
- Funds to sustain protests and care for people involved.
- A clear, detailed agenda with realistic and actionable demands. Vague slogans like "stop corruption" or "bring change" may sound powerful but carry no weight in negotiations. The system only responds when demands are concrete, achievable, and tied to policies or reforms. Without this, protests lose focus and direction.
- Credible, respected leaders and advocates who can step up in moments of crisis, who the public will trust, and who know what they are doing.""",

        """This last point about leadership cannot be overstated. Credible advocates make all the difference. They bring legitimacy and knowledge. They understand how the executive and legislative bodies work, how policies are drafted, how decisions are made, and how to push pressure points effectively. They know how to speak to the media, how to negotiate when needed, and how to rally support from other influential circles like lawyers, journalists, or even sympathetic politicians.""",

        """Without such figures, protests often turn into loud street gatherings with no real impact. With them, however, a movement gains structure, strategy, and recognition. People listen when respected voices speak, and governments are far more cautious when they know credible advocates are involved. They can translate raw energy into actionable demands and protect protesters legally, socially, and politically.""",

        """Without these, any protest you attempt might not only fail but also backfire. The government has already started to feel pressure from the younger generation. But if you go into this recklessly, an unsuccessful protest will only prove to them, and to the whole country, that this generation is immature and not to be taken seriously. It will take away hope from your generation instead of inspiring it.

Real change takes time. It requires patience, organization, credibility, a proper agenda, and respected figures who can guide the movement through the complexities of the system. If you truly want to challenge the system, you must build a strong foundation first, not rush into actions that will only harm your cause.

Take this seriously. Think before acting."""
    ]
    
    try:
        # Send each part with delay
        for i, part in enumerate(protest_parts):
            await target_channel.send(part)
            if i < len(protest_parts) - 1:
                await asyncio.sleep(1)
        
        # Confirm completion
        try:
            await interaction.followup.send(
                f"✅ Protest message sent in {len(protest_parts)} parts!",
                ephemeral=True
            )
        except:
            print(f"Protest message sent by {interaction.user}")
            
    except Exception as e:
        try:
            await interaction.followup.send(f"❌ Error: {str(e)}", ephemeral=True)
        except:
            pass
        print(f"Error in protest command: {e}")


@bot.tree.command(name="ping", description="Check bot status")
async def ping_command(interaction: discord.Interaction):
    """Simple ping command"""
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")


@bot.tree.command(name="date", description="Get current date and time in both English and Nepali (Bikram Sambat)")
async def date_command(interaction: discord.Interaction):
    """Get current date/time with proper Nepali Bikram Sambat conversion"""
    try:
        nepal_tz = pytz.timezone('Asia/Kathmandu')
        now = datetime.now(nepal_tz)
        
        # English date and time
        english_date = now.strftime("%A, %B %d, %Y")
        english_time = now.strftime("%I:%M %p")
        
        # Convert to Nepali Bikram Sambat using nepali-datetime
        nepali_date_obj = nepali_datetime.from_datetime_datetime(now)
        
        # Format Nepali date with proper Devanagari script
        nepali_date_formatted = nepali_date_obj.strftime("%A, %d %B %Y")
        
        # Alternative: Get individual components if you want custom formatting
        # nepali_year = nepali_date_obj.year
        # nepali_month = nepali_date_obj.month
        # nepali_day = nepali_date_obj.day
        # nepali_weekday = nepali_date_obj.strftime("%A")
        # nepali_month_name = nepali_date_obj.strftime("%B")
        
        response = f"""📅 **Current Date & Time:**

🇬🇧 **English (AD):** {english_date}
🇳🇵 **Nepali (BS):** {nepali_date_formatted}

🕐 **Time:** {english_time} (Nepal Time)
🌍 **Timezone:** Asia/Kathmandu (NPT)"""
        
        await interaction.response.send_message(response)
        
    except ImportError:
        # Fallback if nepali-datetime is not installed
        await interaction.response.send_message(
            "❌ Error: nepali-datetime library is not installed.\n"
            "Please install it using: `pip install nepali-datetime`"
        )
    except Exception as e:
        await interaction.response.send_message(f"❌ Error getting date: {e}")


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
• `/kpwrite <message>` - Send message (authorized users)
• `/kpannounce <message>` - Send announcement (authorized users)
• `/kpprotest` - Send protest message (authorized users)
• `/reload` - Reload configuration (admins)

**Text Commands:**
• `!help` - This help message
• `!words` - Show trigger words
• `!reload-data` - Reload config (admins)

**Trigger Words:**
{', '.join(TRIGGER_WORDS[:10])}{'...' if len(TRIGGER_WORDS) > 10 else ''}

The bot responds to messages containing these trigger words!"""
    
    await ctx.send(help_text)


@bot.command(name="words")
async def words_command(ctx):
    """Show all trigger words"""
    if TRIGGER_WORDS:
        word_list = "📝 **Current trigger words:**\n" + "\n".join([f"• {word}" for word in TRIGGER_WORDS])
        # Split long messages
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
    # Load configuration first
    load_bot_data()
    
    # Get bot token
    token = os.getenv("TOKEN")
    
    if not token:
        print("❌ ERROR: No bot token found!")
        print("Please create a .env file with:")
        print("TOKEN=your_bot_token_here")
        print()
        print("Or set the TOKEN environment variable")
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