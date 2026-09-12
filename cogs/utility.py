"""
cogs/utility.py — general utility & info commands: define, weather, Nepali
calendar, userinfo, serverinfo, roleinfo, avatar, snipe, date, ping, remind,
afk.
"""
import asyncio
import re
import urllib.parse
from datetime import datetime, timezone

import aiohttp
import discord
import pytz
from discord import app_commands

from bot_instance import bot
from core.features import require_feature
from core.mention_safety import neutralize_mentions
from core.nepali_calendar import NEPALI_DATETIME_AVAILABLE, get_upcoming_nepali_festivals
from core.state import afk_users, active_reminders, snipe_store

try:
    import nepali_datetime
except ImportError:
    nepali_datetime = None

FEATURE = "utility"

WMO_CODES = {
    0: ("Clear sky", "☀️"),
    1: ("Mainly clear", "🌤️"),
    2: ("Partly cloudy", "⛅"),
    3: ("Overcast", "☁️"),
    45: ("Foggy", "🌫️"),
    48: ("Icy fog", "🌫️"),
    51: ("Light drizzle", "🌦️"),
    53: ("Drizzle", "🌦️"),
    55: ("Heavy drizzle", "🌧️"),
    56: ("Freezing drizzle", "🌧️"),
    57: ("Heavy freezing drizzle", "🌧️"),
    61: ("Slight rain", "🌧️"),
    63: ("Moderate rain", "🌧️"),
    65: ("Heavy rain", "🌧️"),
    66: ("Freezing rain", "🌨️"),
    67: ("Heavy freezing rain", "🌨️"),
    71: ("Slight snow", "❄️"),
    73: ("Moderate snow", "❄️"),
    75: ("Heavy snow", "❄️"),
    77: ("Snow grains", "❄️"),
    80: ("Slight rain showers", "🌦️"),
    81: ("Moderate rain showers", "🌧️"),
    82: ("Violent rain showers", "⛈️"),
    85: ("Snow showers", "❄️"),
    86: ("Heavy snow showers", "❄️"),
    95: ("Thunderstorm", "⛈️"),
    96: ("Thunderstorm with hail", "⛈️"),
    99: ("Thunderstorm with heavy hail", "⛈️"),
}


async def geocode_city(city: str) -> dict | None:
    """Geocode a city name using Open-Meteo's geocoding API."""
    url = f"https://geocoding-api.open-meteo.com/v1/search?name={urllib.parse.quote(city)}&count=1&language=en&format=json"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as response:
                if response.status != 200:
                    return None
                data = await response.json()
                results = data.get("results")
                if not results:
                    return None
                return results[0]
    except Exception:
        return None


async def _fetch_and_build_weather_embed(city: str):
    """Shared logic for both the slash and prefix weather commands.
    Returns (embed, error_message)."""
    location = await geocode_city(city)
    if not location:
        return None, f"❌ City **{neutralize_mentions(city)}** not found. Check the spelling!"

    lat, lon = location["latitude"], location["longitude"]
    city_name = location.get("name", city)
    country = location.get("country", "")
    admin = location.get("admin1", "")

    weather_url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}"
        f"&current=temperature_2m,apparent_temperature,relative_humidity_2m,"
        f"wind_speed_10m,weathercode,visibility,precipitation"
        f"&daily=temperature_2m_max,temperature_2m_min"
        f"&timezone=auto&forecast_days=1"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(weather_url, timeout=10) as response:
            if response.status != 200:
                return None, "❌ Weather service unavailable. Try again later."
            data = await response.json()

    current = data["current"]
    daily = data["daily"]
    wmo = current.get("weathercode", 0)
    description, emoji = WMO_CODES.get(wmo, ("Unknown", "🌡️"))
    temp = current.get("temperature_2m", 0)
    feels_like = current.get("apparent_temperature", 0)
    humidity = current.get("relative_humidity_2m", 0)
    wind_speed = current.get("wind_speed_10m", 0)
    visibility_m = current.get("visibility", 0)
    visibility_km = visibility_m / 1000 if visibility_m else 0
    precipitation = current.get("precipitation", 0)
    temp_max = daily["temperature_2m_max"][0] if daily.get("temperature_2m_max") else temp
    temp_min = daily["temperature_2m_min"][0] if daily.get("temperature_2m_min") else temp

    location_str = city_name
    if admin:
        location_str += f", {admin}"
    if country:
        location_str += f", {country}"

    embed = discord.Embed(
        title=f"{emoji} Weather in {location_str}",
        description=f"**{description}**",
        color=discord.Color.blue()
    )
    embed.add_field(name="🌡️ Temperature", value=f"{temp:.1f}°C (feels like {feels_like:.1f}°C)", inline=True)
    embed.add_field(name="🔼🔽 High / Low", value=f"{temp_max:.1f}°C / {temp_min:.1f}°C", inline=True)
    embed.add_field(name="💧 Humidity", value=f"{humidity}%", inline=True)
    embed.add_field(name="💨 Wind Speed", value=f"{wind_speed:.1f} km/h", inline=True)
    if visibility_km > 0:
        embed.add_field(name="👁️ Visibility", value=f"{visibility_km:.1f} km", inline=True)
    if precipitation > 0:
        embed.add_field(name="🌧️ Precipitation", value=f"{precipitation} mm", inline=True)
    embed.set_footer(text="Data from Open-Meteo (open-meteo.com) · No API key required")
    embed.timestamp = discord.utils.utcnow()
    return embed, None


@bot.tree.command(name="weather", description="Get current weather for a city")
@app_commands.describe(city="City name (e.g. Kathmandu, Pokhara, London)")
@require_feature(FEATURE)
async def weather_command(interaction: discord.Interaction, city: str):
    await interaction.response.defer()
    try:
        embed, error = await _fetch_and_build_weather_embed(city)
        if error:
            await interaction.followup.send(error)
        else:
            await interaction.followup.send(embed=embed)
    except asyncio.TimeoutError:
        await interaction.followup.send("❌ Weather request timed out. Please try again.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error fetching weather: {str(e)}")


@bot.command(name="weather")
@require_feature(FEATURE)
async def weather_prefix(ctx, *, city: str = None):
    """Get current weather. Usage: .weather <city>"""
    if not city:
        await ctx.send("❌ Please provide a city name. Usage: `.weather Kathmandu`")
        return
    async with ctx.typing():
        try:
            embed, error = await _fetch_and_build_weather_embed(city)
            if error:
                await ctx.send(error)
            else:
                await ctx.send(embed=embed)
        except asyncio.TimeoutError:
            await ctx.send("❌ Weather request timed out. Please try again.")
        except Exception as e:
            await ctx.send(f"❌ Error fetching weather: {str(e)}")


# ==================== DEFINE (Free Dictionary API) ====================

async def _fetch_definition_embed(word: str):
    clean_word = re.sub(r"[^a-zA-Z\s\-]", "", word).strip()
    if not clean_word:
        return None, "❌ Please enter a valid word (letters only)."

    url = f"https://api.dictionaryapi.dev/api/v2/entries/en/{urllib.parse.quote(clean_word)}"
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as response:
            if response.status == 404:
                return None, f"❌ No definition found for **{clean_word}**."
            if response.status != 200:
                return None, "❌ Dictionary service unavailable. Try again later."
            data = await response.json()

    entry = data[0]
    word_title = entry.get("word", clean_word)
    phonetic = entry.get("phonetic", "")
    embed = discord.Embed(title=f"📖 {word_title}", color=discord.Color.orange())
    if phonetic:
        embed.description = f"*{phonetic}*"

    meanings_shown = 0
    for meaning in entry.get("meanings", []):
        if meanings_shown >= 3:
            break
        pos = meaning.get("partOfSpeech", "")
        defs = meaning.get("definitions", [])
        if not defs:
            continue
        defn = defs[0].get("definition", "")
        example = defs[0].get("example", "")
        synonyms = meaning.get("synonyms", [])[:4]
        field_value = defn
        if example:
            field_value += f"\n*e.g. {example}*"
        if synonyms:
            field_value += f"\n**Synonyms:** {', '.join(synonyms)}"
        if len(field_value) > 900:
            field_value = field_value[:900] + "..."
        embed.add_field(name=f"*{pos}*" if pos else "Definition", value=field_value, inline=False)
        meanings_shown += 1

    for phonetic_entry in entry.get("phonetics", []):
        if phonetic_entry.get("audio"):
            embed.add_field(name="🔊 Pronunciation", value=f"[Listen]({phonetic_entry['audio']})", inline=False)
            break

    embed.set_footer(text="Free Dictionary API")
    return embed, None


@bot.tree.command(name="define", description="Look up the definition of a word")
@app_commands.describe(word="Word to define")
@require_feature(FEATURE)
async def define_command(interaction: discord.Interaction, word: str):
    await interaction.response.defer()
    try:
        embed, error = await _fetch_definition_embed(word)
        if error:
            await interaction.followup.send(error)
        else:
            await interaction.followup.send(embed=embed)
    except asyncio.TimeoutError:
        await interaction.followup.send("❌ Request timed out. Please try again.")
    except Exception as e:
        await interaction.followup.send(f"❌ Error: {str(e)}")


@bot.command(name="define")
@require_feature(FEATURE)
async def define_prefix(ctx, *, word: str = None):
    """Look up a word definition. Usage: .define <word>"""
    if not word:
        await ctx.send("❌ Please provide a word. Usage: `.define serendipity`")
        return
    async with ctx.typing():
        try:
            embed, error = await _fetch_definition_embed(word)
            if error:
                await ctx.send(error)
            else:
                await ctx.send(embed=embed)
        except asyncio.TimeoutError:
            await ctx.send("❌ Request timed out. Please try again.")
        except Exception as e:
            await ctx.send(f"❌ Error: {str(e)}")


# ==================== NEPALI CALENDAR ====================

@bot.tree.command(name="calendar", description="Show upcoming Nepali festivals and holidays")
@app_commands.describe(days="How many days ahead to look (default: 30, max: 90)")
@require_feature(FEATURE)
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


# ==================== SNIPE ====================

@bot.tree.command(name="snipe", description="Show the last deleted message in this channel")
@require_feature(FEATURE)
async def snipe_command(interaction: discord.Interaction):
    data = snipe_store.get(interaction.channel.id)
    if not data:
        await interaction.response.send_message(
            "🔍 Nothing to snipe! No deleted messages cached in this channel.", ephemeral=True
        )
        return
    elapsed = (datetime.now(timezone.utc) - data["deleted_at"]).total_seconds()
    time_ago = f"{int(elapsed)}s ago" if elapsed < 60 else (f"{int(elapsed // 60)}m ago" if elapsed < 3600 else f"{int(elapsed // 3600)}h ago")
    embed = discord.Embed(
        description=data["content"] if data["content"] else "*[no text content]*",
        color=discord.Color.red(),
        timestamp=data["deleted_at"]
    )
    embed.set_author(name=data["author_name"], icon_url=data["author_avatar"])
    embed.set_footer(text=f"🗑️ Deleted {time_ago} · sniped by {interaction.user.display_name}")
    if data.get("attachment_url"):
        embed.set_image(url=data["attachment_url"])
    await interaction.response.send_message(embed=embed)


@bot.command(name="snipe")
@require_feature(FEATURE)
async def snipe_prefix(ctx):
    """Show the last deleted message in this channel."""
    data = snipe_store.get(ctx.channel.id)
    if not data:
        await ctx.send("🔍 Nothing to snipe! No deleted messages cached in this channel.")
        return
    elapsed = (datetime.now(timezone.utc) - data["deleted_at"]).total_seconds()
    time_ago = f"{int(elapsed)}s ago" if elapsed < 60 else (f"{int(elapsed // 60)}m ago" if elapsed < 3600 else f"{int(elapsed // 3600)}h ago")
    embed = discord.Embed(
        description=data["content"] if data["content"] else "*[no text content]*",
        color=discord.Color.red(),
        timestamp=data["deleted_at"]
    )
    embed.set_author(name=data["author_name"], icon_url=data["author_avatar"])
    embed.set_footer(text=f"🗑️ Deleted {time_ago} · sniped by {ctx.author.display_name}")
    if data.get("attachment_url"):
        embed.set_image(url=data["attachment_url"])
    await ctx.send(embed=embed)


# ==================== AVATAR ====================

def _avatar_view(target: discord.Member) -> discord.ui.View:
    view = discord.ui.View()
    view.add_item(discord.ui.Button(label="PNG", url=target.display_avatar.replace(format='png', size=1024).url, style=discord.ButtonStyle.link))
    view.add_item(discord.ui.Button(label="WEBP", url=target.display_avatar.replace(format='webp', size=1024).url, style=discord.ButtonStyle.link))
    if target.display_avatar.is_animated():
        view.add_item(discord.ui.Button(label="GIF", url=target.display_avatar.replace(format='gif', size=1024).url, style=discord.ButtonStyle.link))
    return view


@bot.tree.command(name="avatar", description="View a user's full-size avatar")
@app_commands.describe(user="The user whose avatar you want to see (leave empty for yourself)")
@require_feature(FEATURE)
async def avatar_command(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    embed = discord.Embed(
        title=f"🖼️ {target.display_name}'s Avatar",
        color=target.color if target.color.value != 0 else discord.Color.blurple()
    )
    embed.set_image(url=target.display_avatar.url)
    embed.set_footer(text=f"Requested by {interaction.user.display_name}")
    await interaction.response.send_message(embed=embed, view=_avatar_view(target))


async def _resolve_member_for_avatar(ctx, query: str | None) -> discord.Member | None:
    if query is None:
        return ctx.author
    if ctx.message.mentions:
        return ctx.message.mentions[0]
    ql = query.strip().lower()
    m = (
        discord.utils.find(lambda m: m.display_name.lower() == ql, ctx.guild.members)
        or discord.utils.find(lambda m: m.name.lower() == ql, ctx.guild.members)
        or discord.utils.find(lambda m: ql in m.display_name.lower(), ctx.guild.members)
        or discord.utils.find(lambda m: ql in m.name.lower(), ctx.guild.members)
    )
    if m is None:
        await ctx.send(f"❌ Couldn't find a member matching **{neutralize_mentions(query)}**.")
    return m


@bot.command(name="av")
@require_feature(FEATURE)
async def av_prefix(ctx, *, query: str = None):
    """Show a user's avatar. Usage: .av | .av @user | .av username"""
    target = await _resolve_member_for_avatar(ctx, query)
    if target is None:
        return
    embed = discord.Embed(
        title=f"🖼️ {target.display_name}'s Avatar",
        color=target.color if target.color.value != 0 else discord.Color.blurple()
    )
    embed.set_image(url=target.display_avatar.url)
    embed.set_footer(text=f"Requested by {ctx.author.display_name}")
    await ctx.send(embed=embed, view=_avatar_view(target))


# ==================== USER INFO ====================

def _build_userinfo_embed(target: discord.Member, requested_by: str) -> discord.Embed:
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
    embed = discord.Embed(title=f"👤 {target.display_name}", color=target.color if target.color.value != 0 else discord.Color.blurple())
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
    embed.set_footer(text=f"Requested by {requested_by}")
    return embed


@bot.tree.command(name="userinfo", description="View info about a user")
@app_commands.describe(user="The user to look up (leave empty for yourself)")
@require_feature(FEATURE)
async def userinfo_command(interaction: discord.Interaction, user: discord.Member = None):
    target = user or interaction.user
    await interaction.response.send_message(embed=_build_userinfo_embed(target, interaction.user.display_name))


@bot.command(name="userinfo")
@require_feature(FEATURE)
async def userinfo_prefix(ctx, *, query: str = None):
    """View info about a user. Usage: .userinfo [@user]"""
    target = await _resolve_member_for_avatar(ctx, query)
    if target is None:
        return
    await ctx.send(embed=_build_userinfo_embed(target, ctx.author.display_name))


# ==================== SERVER INFO ====================

def _build_serverinfo_embed(guild: discord.Guild, requested_by: str) -> discord.Embed:
    embed = discord.Embed(title=f"🏰 {guild.name}", color=discord.Color.blurple())
    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)
    embed.add_field(name="ID", value=guild.id, inline=True)
    embed.add_field(name="Owner", value=guild.owner.mention if guild.owner else "Unknown", inline=True)
    embed.add_field(name="Created", value=guild.created_at.strftime('%B %d, %Y'), inline=True)
    embed.add_field(name="Members", value=guild.member_count, inline=True)
    embed.add_field(name="Text Channels", value=len(guild.text_channels), inline=True)
    embed.add_field(name="Voice Channels", value=len(guild.voice_channels), inline=True)
    embed.add_field(name="Boost Level", value=guild.premium_tier, inline=True)
    embed.add_field(name="Boosts", value=guild.premium_subscription_count, inline=True)
    embed.set_footer(text=f"Requested by {requested_by}")
    return embed


@bot.tree.command(name="serverinfo", description="Get server information")
@require_feature(FEATURE)
async def serverinfo_command(interaction: discord.Interaction):
    await interaction.response.send_message(embed=_build_serverinfo_embed(interaction.guild, interaction.user.display_name))


@bot.command(name="serverinfo")
@require_feature(FEATURE)
async def serverinfo_prefix(ctx):
    """Get server information."""
    await ctx.send(embed=_build_serverinfo_embed(ctx.guild, ctx.author.display_name))


# ==================== ROLE INFO ====================

@bot.tree.command(name="roleinfo", description="View info about a role")
@app_commands.describe(role="The role to look up")
@require_feature(FEATURE)
async def roleinfo_command(interaction: discord.Interaction, role: discord.Role):
    now = discord.utils.utcnow()
    age = (now - role.created_at).days
    member_count = len(role.members)

    key_perms = []
    perms = role.permissions
    if perms.administrator: key_perms.append("Administrator")
    if perms.manage_guild: key_perms.append("Manage Server")
    if perms.manage_channels: key_perms.append("Manage Channels")
    if perms.manage_roles: key_perms.append("Manage Roles")
    if perms.manage_messages: key_perms.append("Manage Messages")
    if perms.kick_members: key_perms.append("Kick Members")
    if perms.ban_members: key_perms.append("Ban Members")
    if perms.mention_everyone: key_perms.append("Mention Everyone")
    if perms.moderate_members: key_perms.append("Timeout Members")

    color = role.color if role.color.value != 0 else discord.Color.light_grey()
    hex_color = str(role.color) if role.color.value != 0 else "#000000"

    embed = discord.Embed(title=f"🏷️ Role: {role.name}", color=color)
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


# ==================== DATE / PING ====================

def _build_date_string() -> str:
    nepal_tz = pytz.timezone('Asia/Kathmandu')
    now = datetime.now(nepal_tz)
    english_date = now.strftime("%A, %B %d, %Y")
    english_time = now.strftime("%I:%M %p")
    nepali_date_str = "BS conversion unavailable"
    if NEPALI_DATETIME_AVAILABLE and nepali_datetime is not None:
        try:
            nepali_dt = nepali_datetime.datetime.from_datetime_datetime(now)
            nepali_date_str = nepali_dt.strftime("%A, %d %B %Y")
        except Exception:
            try:
                nepali_d = nepali_datetime.date.from_datetime_date(now.date())
                nepali_date_str = nepali_d.strftime("%A, %d %B %Y")
            except Exception:
                nepali_date_str = "BS conversion failed"
    return (
        f"📅 **Current Date & Time:**\n\n"
        f"🇬🇧 **English (AD):** {english_date}\n"
        f"🇳🇵 **Nepali (BS):** {nepali_date_str}\n\n"
        f"🕐 **Time:** {english_time} (Nepal Time)\n"
        f"🌍 **Timezone:** Asia/Kathmandu (NPT)"
    )


@bot.tree.command(name="date", description="Get current date and time in both English and Nepali (Bikram Sambat)")
@require_feature(FEATURE)
async def date_command(interaction: discord.Interaction):
    await interaction.response.defer()
    try:
        await interaction.followup.send(_build_date_string())
    except Exception as e:
        await interaction.followup.send(f"❌ Error getting date: {str(e)}")


@bot.command(name="date")
@require_feature(FEATURE)
async def date_prefix(ctx):
    """Get the current date and time in English and Nepali."""
    try:
        await ctx.send(_build_date_string())
    except Exception as e:
        await ctx.send(f"❌ Error getting date: {str(e)}")


@bot.tree.command(name="ping", description="Check bot status")
@require_feature(FEATURE)
async def ping_command(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)
    await interaction.response.send_message(f"🏓 Pong! Latency: {latency}ms")


# ==================== REMINDER ====================

@bot.tree.command(name="remind", description="Set a reminder (e.g. 30m, 2h, 1d)")
@app_commands.describe(
    time="Time until reminder (e.g. 10m, 2h, 1d)",
    reminder="What to remind you about"
)
@require_feature("reminders")
async def remind_command(interaction: discord.Interaction, time: str, reminder: str):
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

    parts = []
    remaining = seconds
    for unit, name in [(86400, "day"), (3600, "hour"), (60, "minute"), (1, "second")]:
        if remaining >= unit:
            val = remaining // unit
            remaining %= unit
            parts.append(f"{val} {name}{'s' if val != 1 else ''}")
    time_str = ", ".join(parts)

    # `reminder` is free text echoed back later, run through neutralize_mentions()
    # as an extra safety layer on top of the bot's global allowed_mentions default.
    safe_reminder = neutralize_mentions(reminder)

    await interaction.response.send_message(
        f"⏰ Got it! I'll remind you about **{safe_reminder}** in **{time_str}**.",
        ephemeral=False
    )

    async def send_reminder():
        await asyncio.sleep(seconds)
        try:
            embed = discord.Embed(title="⏰ Reminder!", description=safe_reminder, color=discord.Color.yellow())
            embed.set_footer(text=f"Set {time_str} ago")
            await interaction.user.send(embed=embed)
        except discord.Forbidden:
            try:
                await interaction.channel.send(f"⏰ {interaction.user.mention} — reminder: **{safe_reminder}**")
            except Exception:
                pass

    task = asyncio.create_task(send_reminder())
    user_reminders = active_reminders.setdefault(interaction.user.id, [])
    user_reminders.append(task)


# ==================== AFK SYSTEM ====================

@bot.tree.command(name="afk", description="Set yourself as AFK")
@app_commands.describe(reason="Reason for being AFK (optional)")
@require_feature("afk_system")
async def afk_command(interaction: discord.Interaction, reason: str = "AFK"):
    safe_reason = neutralize_mentions(reason)
    afk_users[interaction.user.id] = {
        "reason": safe_reason,
        "time": discord.utils.utcnow()
    }
    await interaction.response.send_message(
        f"💤 **{interaction.user.display_name}** is now AFK: *{safe_reason}*"
    )
    try:
        current_nick = interaction.user.display_name
        if not current_nick.startswith("[AFK]"):
            await interaction.user.edit(nick=f"[AFK] {current_nick}"[:32])
    except discord.Forbidden:
        pass


@bot.listen('on_message')
async def afk_listener(message):
    if message.author.bot:
        return

    from config import FEATURES
    if not FEATURES.get("afk_system", True):
        return

    if message.author.id in afk_users:
        afk_data = afk_users.pop(message.author.id)
        elapsed = discord.utils.utcnow() - afk_data["time"]
        minutes = int(elapsed.total_seconds() // 60)
        time_str = f"{minutes} minute{'s' if minutes != 1 else ''}" if minutes else "less than a minute"
        await message.channel.send(
            f"👋 Welcome back, {message.author.mention}! You were AFK for **{time_str}**.",
            delete_after=10
        )
        try:
            if message.author.display_name.startswith("[AFK]"):
                new_nick = message.author.display_name[6:].strip() or None
                await message.author.edit(nick=new_nick)
        except discord.Forbidden:
            pass

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
