"""
cogs/worldcup.py — World Cup 2026 auto-stream scheduler + live score tracker.

DISABLED BY DEFAULT: gated behind the "worldcup_tracker" toggle in
features.json (default: false). Requires a FOOTBALL_DATA_API_KEY in .env
and depends on cogs/pc_control.py's AHK bridge (_send_ahk_command,
_pc_admin_check) to actually trigger stream start/stop sequences, so it
only really does anything useful when "pc_control" is also enabled.

Commands (.wcenable / .wcdisable / .wcstatus / .wctest / .wcreset /
.wcscores / .wcscore / .streamgo / .streamend) are all still individually
gated by @require_feature("worldcup_tracker") below.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone

import aiohttp
import discord
from discord.ext import commands

from bot_instance import bot
from config import FEATURES
from core.features import require_feature
from core.mention_safety import neutralize_mentions
from cogs.pc_control import _send_ahk_command, _pc_admin_check

FEATURE = "worldcup_tracker"

# ==================== WORLD CUP 2026 AUTO-STREAM SCHEDULER ====================
#
# Uses football-data.org free API (sign up at football-data.org for a free key).
# Add FOOTBALL_DATA_API_KEY=your_key to your .env file.
#
# HOW IT WORKS:
#   - Polls football-data.org every 30 s (60 s during live matches)
#   - PRE-MATCH: fires the stream-start sequence exactly 15 min before kickoff
#     (window: 14:30–15:30 before kickoff so the 30s loop never misses it)
#   - POST-MATCH: fires the stream-stop sequence 5 min after the API reports
#     status = FINISHED (real game-end, not a time guess).
#     Fallback: if API never updates, fires 130 min after kickoff.
#
# Match statuses from football-data.org:
#   TIMED      – scheduled, not started yet
#   IN_PLAY    – currently playing (1st half)
#   PAUSED     – half-time
#   FINISHED   – full-time (or AET/penalties done)
#   POSTPONED / CANCELLED / SUSPENDED
#
# Commands:
#   .wcenable / .wcdisable  – toggle scheduler
#   .wcstatus               – show upcoming matches + scheduler state
#   .wctest                 – live API diagnostic
#   .wcreset                – clear fired-match memory (re-arm all)

WC_SCHEDULER_ENABLED = True           # toggle with .wcenable / .wcdisable
_wc_scheduled: dict[str, str] = {}    # "{match_id}_pre" | "{match_id}_post" → "fired"
_wc_matches_cache: list[dict] = []
_wc_cache_time: float = 0.0
_wc_finished_at: dict[str, datetime] = {}  # match_id → UTC time we first saw FINISHED
WC_CACHE_TTL_IDLE = 600               # seconds between refreshes when no match is near
WC_CACHE_TTL_LIVE = 60               # seconds between refreshes when a match is live/close
WC_PRE_WINDOW_LOW  = 14.5            # minutes before kickoff — start of firing window
WC_PRE_WINDOW_HIGH = 15.5            # minutes before kickoff — end of firing window
WC_POST_DELAY      = 5               # minutes after FINISHED before firing post-sequence
WC_FALLBACK_MINUTES = 130            # fallback: fire post-match N min after kickoff if API stale

# Pre-match command sequence (15 s gaps between each)
WC_PRE_COMMANDS  = ["join", "clickplay", "streamstart", "fullscreen"]
# Post-match command sequence
WC_POST_COMMANDS = ["resume", "closeedge", "disconnect"]

# football-data.org — free tier, 10 req/min, no cost
# World Cup 2026 competition code: WC   (id: 2000)
_FD_BASE = "https://api.football-data.org/v4"
_FD_WC_COMPETITION = "WC"   # or use numeric id 2000


def _fd_headers() -> dict:
    """Return auth headers for football-data.org. Key is optional on free tier but required for WC."""
    key = os.getenv("FOOTBALL_DATA_API_KEY", "")
    h = {"Accept": "application/json"}
    if key:
        h["X-Auth-Token"] = key
    return h


def _fd_ssl() -> "ssl.SSLContext":
    """Disabled-verification SSL context — fixes Windows CA bundle issues."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _wc_fetch_matches() -> list[dict]:
    """
    Fetch World Cup 2026 matches from football-data.org.
    Returns list of dicts with keys: id, name, kickoff (UTC datetime), status.
    Status values: TIMED | IN_PLAY | PAUSED | FINISHED | POSTPONED | CANCELLED
    Falls back to cached data on error.
    """
    global _wc_matches_cache, _wc_cache_time

    # Decide cache TTL: shorter when a match is live or starting soon
    now_loop = asyncio.get_event_loop().time()
    now_utc  = datetime.now(timezone.utc)
    is_close = any(
        abs((m["kickoff"] - now_utc).total_seconds()) < 7200   # within 2 hours of kickoff
        or m["status"] in ("IN_PLAY", "PAUSED")
        for m in _wc_matches_cache
    )
    ttl = WC_CACHE_TTL_LIVE if is_close else WC_CACHE_TTL_IDLE

    if _wc_matches_cache and (now_loop - _wc_cache_time) < ttl:
        return _wc_matches_cache

    url = f"{_FD_BASE}/competitions/{_FD_WC_COMPETITION}/matches"
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_fd_ssl())) as session:
            async with session.get(url, headers=_fd_headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 403:
                    print("[WC Scheduler] football-data.org: 403 — add FOOTBALL_DATA_API_KEY to .env")
                    return _wc_matches_cache
                if resp.status != 200:
                    print(f"[WC Scheduler] football-data.org HTTP {resp.status}")
                    return _wc_matches_cache
                data = await resp.json()

        matches = []
        for m in data.get("matches", []):
            mid     = str(m.get("id", ""))
            status  = m.get("status", "TIMED")           # TIMED | IN_PLAY | PAUSED | FINISHED …
            utc_str = m.get("utcDate", "")               # "2026-06-11T16:00:00Z"
            home    = m.get("homeTeam", {}).get("shortName") or m.get("homeTeam", {}).get("name", "TBD")
            away    = m.get("awayTeam", {}).get("shortName") or m.get("awayTeam", {}).get("name", "TBD")
            name    = f"{home} vs {away}"

            try:
                ko = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
            except Exception:
                continue

            matches.append({
                "id":      mid,
                "name":    name,
                "kickoff": ko,
                "status":  status,
            })

        _wc_matches_cache = matches
        _wc_cache_time    = now_loop
        live_count = sum(1 for m in matches if m["status"] in ("IN_PLAY", "PAUSED"))
        print(f"[WC Scheduler] Loaded {len(matches)} WC matches ({live_count} live) from football-data.org")
        return matches

    except Exception as e:
        print(f"[WC Scheduler] Fetch error: {e}")
        return _wc_matches_cache


async def _wc_run_sequence(commands: list[str], label: str, channel: discord.TextChannel):
    """Send a sequence of AHK commands with 15-second gaps, posting status in channel.
    Most commands are quick keystrokes (8s timeout is plenty), but clickplay
    opens a browser + polls the page for up to ~30s, so it needs a much
    longer timeout or it will be wrongly reported as failed mid-run.
    """
    AHK_CMD_TIMEOUTS = {
        "clickplay": 55.0,   # worst case: Edge kill+wait (~5.5s) + relaunch wait
                              # (~5s) + window activate (~5.5s) + JS setup (~1.3s)
                              # + 30s poll loop + buffer ≈ 50s actual, +5s margin
        "closeedge": 15.0,   # kill + wait-for-exit (~5.5s) + relaunch wait (~3s)
                              # + window activate (~5s) + buffer
    }
    await channel.send(f"⚽ **World Cup Auto-Scheduler** › {label} — starting sequence…")
    for i, cmd in enumerate(commands):
        cmd_timeout = AHK_CMD_TIMEOUTS.get(cmd, 8.0)
        ok, msg = await _send_ahk_command(cmd, timeout=cmd_timeout)
        emoji = "✅" if ok else "❌"
        await channel.send(f"{emoji} `.{cmd}` {'done' if ok else f'failed: {msg}'}")
        if i < len(commands) - 1:
            await asyncio.sleep(15)
    await channel.send("✅ **Sequence complete!**")


async def _wc_scheduler_loop():
    """Background task — polls every 30 s and fires commands at the right times."""
    await bot.wait_until_ready()
    print("⚽ World Cup scheduler started (football-data.org live status).")

    # ── Startup guard: pre-mark all already-FINISHED/past matches so we never
    #    fire post-match commands for games that ended before this session. ──
    try:
        startup_matches = await _wc_fetch_matches()
        now_utc_boot = datetime.now(timezone.utc)
        skipped = 0
        for m in startup_matches:
            post_key = f"{m['id']}_post"
            pre_key  = f"{m['id']}_pre"
            # Already finished — mark both pre and post as done
            if m["status"] == "FINISHED":
                _wc_scheduled.setdefault(post_key, "fired")
                _wc_scheduled.setdefault(pre_key,  "fired")
                _wc_finished_at.setdefault(m["id"], now_utc_boot)
                skipped += 1
            # Kicked off already but not finished — pre-match is moot
            elif m["kickoff"] <= now_utc_boot:
                _wc_scheduled.setdefault(pre_key, "fired")
        print(f"⚽ Startup: pre-armed {skipped} already-finished matches (won't fire post-match).")
    except Exception as e:
        print(f"⚽ Startup pre-arm error: {e}")

    while not bot.is_closed():
        if not WC_SCHEDULER_ENABLED:
            await asyncio.sleep(30)
            continue

        channel = bot.get_channel(FEATURES.get("target_channel_id", 0))
        if channel is None:
            await asyncio.sleep(30)
            continue

        try:
            matches  = await _wc_fetch_matches()
            now_utc  = datetime.now(timezone.utc)

            for match in matches:
                mid     = match["id"]
                kickoff = match["kickoff"]      # UTC datetime
                name    = match["name"]
                status  = match["status"]       # live status from API
                minutes_to_start   = (kickoff - now_utc).total_seconds() / 60
                minutes_since_start = (now_utc - kickoff).total_seconds() / 60

                # ── PRE-MATCH: fire once, exactly in the 14.5–15.5 min window before kickoff ──
                pre_key = f"{mid}_pre"
                if pre_key not in _wc_scheduled and WC_PRE_WINDOW_LOW <= minutes_to_start <= WC_PRE_WINDOW_HIGH:
                    _wc_scheduled[pre_key] = "fired"
                    print(f"[WC Scheduler] PRE-MATCH → {name} (kicks off in {minutes_to_start:.1f} min)")
                    asyncio.create_task(
                        _wc_run_sequence(
                            WC_PRE_COMMANDS,
                            f"Pre-match — **{name}** kicks off in ~15 min!",
                            channel,
                        )
                    )

                # ── POST-MATCH detection ──────────────────────────────────────────────────────
                post_key = f"{mid}_post"
                if post_key in _wc_scheduled:
                    continue  # already fired

                # 1) API reports FINISHED → start 5-min countdown
                if status == "FINISHED":
                    if mid not in _wc_finished_at:
                        _wc_finished_at[mid] = now_utc
                        print(f"[WC Scheduler] API says FINISHED for {name}, waiting {WC_POST_DELAY} min…")
                    elif (now_utc - _wc_finished_at[mid]).total_seconds() / 60 >= WC_POST_DELAY:
                        _wc_scheduled[post_key] = "fired"
                        print(f"[WC Scheduler] POST-MATCH (API FINISHED+{WC_POST_DELAY}m) → {name}")
                        asyncio.create_task(
                            _wc_run_sequence(
                                WC_POST_COMMANDS,
                                f"Post-match — **{name}** has ended!",
                                channel,
                            )
                        )

                # 2) Fallback: if API never updated, fire WC_FALLBACK_MINUTES after kickoff
                elif minutes_since_start >= WC_FALLBACK_MINUTES:
                    _wc_scheduled[post_key] = "fired"
                    print(f"[WC Scheduler] POST-MATCH (fallback {WC_FALLBACK_MINUTES}m) → {name}")
                    asyncio.create_task(
                        _wc_run_sequence(
                            WC_POST_COMMANDS,
                            f"Post-match — **{name}** (fallback timer, API may be stale)",
                            channel,
                        )
                    )

        except Exception as e:
            print(f"[WC Scheduler] Loop error: {e}")

        await asyncio.sleep(30)


# ==================== WORLD CUP 2026 LIVE SCORE TRACKER ====================
#
# When a match goes IN_PLAY the bot posts a score embed in TARGET_CHANNEL_ID.
# That message is then edited every 60 s with the latest score + minute.
# When the match reaches FINISHED the embed is given a final ✅ update.
#
# New commands:
#   .wcscore [team]   – fetch current/latest score for a team (or all live)
#   .wcscores         – list every match live right now

# match_id → discord.Message (the live-score embed we posted)
_wc_score_messages: dict[str, discord.Message] = {}
# match_id → last raw score dict we rendered (to skip redundant edits)
_wc_last_score_render: dict[str, str] = {}

SCORE_UPDATE_INTERVAL = 60   # seconds between live score edits


async def _wc_fetch_live_match(match_id: str) -> dict | None:
    """
    Fetch a single match's detailed live data from football-data.org.
    Returns the raw match dict or None on error.
    """
    url = f"{_FD_BASE}/matches/{match_id}"
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_fd_ssl())) as session:
            async with session.get(url, headers=_fd_headers(), timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json()
                return data
    except Exception:
        return None


def _wc_score_embed(match: dict, raw: dict | None = None) -> discord.Embed:
    """
    Build a Discord Embed for a live/finished match.
    `match` is our cached dict (id, name, kickoff, status).
    `raw`   is the full API response for the single match (has score, minute, etc.).
    """
    status  = raw["status"] if raw else match["status"]
    home    = raw["homeTeam"]["shortName"] if raw else match["name"].split(" vs ")[0]
    away    = raw["awayTeam"]["shortName"] if raw else match["name"].split(" vs ")[-1]

    # Score
    score_home = score_away = "-"
    minute_str = ""
    if raw:
        sc = raw.get("score", {})
        ft = sc.get("fullTime", {})
        ht = sc.get("halfTime", {})
        if ft.get("home") is not None:
            score_home = str(ft["home"])
            score_away = str(ft["away"])
        elif ht.get("home") is not None:
            score_home = str(ht["home"])
            score_away = str(ht["away"])
        minute_str = str(raw.get("minute", ""))

    # Color & title by status
    if status == "FINISHED":
        color = discord.Color.dark_green()
        title = "⚽ Full Time"
    elif status == "PAUSED":
        color = discord.Color.orange()
        title = "⚽ Half Time"
    elif status == "IN_PLAY":
        color = discord.Color.green()
        title = f"⚽ LIVE{f' — {minute_str}′' if minute_str else ''}"
    else:
        color = discord.Color.greyple()
        title = f"⚽ {status.title()}"

    embed = discord.Embed(title=title, color=color)
    embed.add_field(
        name=f"🏟️ {home}  vs  {away}",
        value=f"## {score_home}  –  {score_away}",
        inline=False,
    )

    # Goals detail
    if raw:
        goals1 = raw.get("goals", []) if False else []   # API v4 doesn't give goal list on match endpoint
        # Show scorer info if available via bookings/events (not always present on free tier)
        pass

    nepal_tz   = pytz.timezone("Asia/Kathmandu")
    kickoff_npt = match["kickoff"].astimezone(nepal_tz).strftime("%b %d, %H:%M NPT")
    embed.set_footer(text=f"Kickoff: {kickoff_npt} • Updates every {SCORE_UPDATE_INTERVAL}s")
    return embed


async def _wc_live_score_loop():
    """
    Background task — runs alongside _wc_scheduler_loop.
    Posts + keeps editing live-score embeds for every IN_PLAY / PAUSED match.
    """
    await bot.wait_until_ready()
    print("📊 World Cup live-score tracker started.")

    # Wait briefly for the scheduler loop to finish its startup pre-arm first
    await asyncio.sleep(5)

    while not bot.is_closed():
        if not WC_SCHEDULER_ENABLED:
            await asyncio.sleep(SCORE_UPDATE_INTERVAL)
            continue

        channel = bot.get_channel(FEATURES.get("target_channel_id", 0))
        if channel is None:
            await asyncio.sleep(SCORE_UPDATE_INTERVAL)
            continue

        try:
            matches = await _wc_fetch_matches()   # uses shared cache

            for match in matches:
                mid    = match["id"]
                status = match["status"]

                # Only care about live, half-time, or just-finished matches
                if status not in ("IN_PLAY", "PAUSED", "FINISHED"):
                    continue

                # Skip matches that were already finished before this bot session
                post_key = f"{mid}_post"
                if _wc_scheduled.get(post_key) == "fired" and mid not in _wc_score_messages:
                    # pre-armed at startup — never went live during this session
                    continue

                # Don't update finished matches more than once after they end
                score_done_key = f"{mid}_score_done"
                if score_done_key in _wc_scheduled and status == "FINISHED":
                    continue

                # Fetch fresh single-match detail (real score + minute)
                raw = await _wc_fetch_live_match(mid)
                if raw is None:
                    continue

                embed = _wc_score_embed(match, raw)

                # Build a short fingerprint to avoid spamming identical edits
                ft    = raw.get("score", {}).get("fullTime", {})
                ht    = raw.get("score", {}).get("halfTime", {})
                fingerprint = f"{status}-{ft}-{ht}-{raw.get('minute','')}"

                if mid in _wc_score_messages:
                    # Edit existing message only if something changed
                    if _wc_last_score_render.get(mid) != fingerprint:
                        try:
                            await _wc_score_messages[mid].edit(embed=embed)
                            _wc_last_score_render[mid] = fingerprint
                        except discord.NotFound:
                            del _wc_score_messages[mid]  # message was deleted, re-post next loop
                        except Exception:
                            pass
                else:
                    # Post a new score message
                    home = raw.get("homeTeam", {}).get("shortName", "?")
                    away = raw.get("awayTeam", {}).get("shortName", "?")
                    msg  = await channel.send(
                        content=f"📊 **Live Score — {home} vs {away}**",
                        embed=embed,
                    )
                    _wc_score_messages[mid]       = msg
                    _wc_last_score_render[mid]    = fingerprint
                    print(f"[WC Scores] Posted live score embed for {match['name']}")

                # Mark finished matches so we stop updating them
                if status == "FINISHED":
                    _wc_scheduled[score_done_key] = "done"
                    print(f"[WC Scores] Final score posted for {match['name']}")

        except Exception as e:
            print(f"[WC Scores] Loop error: {e}")

        await asyncio.sleep(SCORE_UPDATE_INTERVAL)


# ── Score commands ─────────────────────────────────────────────────────────────

@require_feature(FEATURE)
@bot.command(name="wcscores")
async def wc_scores_cmd(ctx: commands.Context):
    """Show all currently live World Cup matches and their scores."""
    global _wc_cache_time
    _wc_cache_time = 0.0
    matches = await _wc_fetch_matches()
    live    = [m for m in matches if m["status"] in ("IN_PLAY", "PAUSED")]

    if not live:
        # Show the next upcoming match instead
        now_utc  = datetime.now(timezone.utc)
        upcoming = sorted([m for m in matches if m["kickoff"] > now_utc], key=lambda m: m["kickoff"])
        if upcoming:
            nxt = upcoming[0]
            nepal_tz = pytz.timezone("Asia/Kathmandu")
            npt = nxt["kickoff"].astimezone(nepal_tz).strftime("%b %d, %H:%M NPT")
            mins = int((nxt["kickoff"] - now_utc).total_seconds() / 60)
            await ctx.reply(f"⚽ No matches live right now.\nNext: **{nxt['name']}** at {npt} (in {mins} min)")
        else:
            await ctx.reply("⚽ No live or upcoming World Cup matches found.")
        return

    embeds = []
    for match in live:
        raw = await _wc_fetch_live_match(match["id"])
        embeds.append(_wc_score_embed(match, raw))

    await ctx.reply(f"🔴 **{len(live)} match(es) live right now:**", embeds=embeds[:10])


@require_feature(FEATURE)
@bot.command(name="wcscore")
async def wc_score_cmd(ctx: commands.Context, *, team: str = ""):
    """Show the live/latest score for a specific team, or next upcoming match if nothing is live."""
    global _wc_cache_time
    _wc_cache_time = 0.0
    matches  = await _wc_fetch_matches()
    now_utc  = datetime.now(timezone.utc)
    nepal_tz = pytz.timezone("Asia/Kathmandu")

    if team:
        team_lower = team.lower()
        # Prefer live/finished matches for this team first
        candidates = sorted(
            [m for m in matches if team_lower in m["name"].lower()],
            key=lambda m: m["kickoff"], reverse=True
        )
        if not candidates:
            await ctx.reply(f"❌ No matches found for **{neutralize_mentions(team)}**. Check the spelling or use `.wcstatus` to see teams.")
            return
        match = candidates[0]
        raw   = await _wc_fetch_live_match(match["id"])
        embed = _wc_score_embed(match, raw)

        # If it's a future match, add kickoff info
        if match["status"] == "TIMED":
            npt  = match["kickoff"].astimezone(nepal_tz).strftime("%b %d, %H:%M NPT")
            mins = int((match["kickoff"] - now_utc).total_seconds() / 60)
            await ctx.reply(f"⏳ **{match['name']}** hasn't kicked off yet.\n🕐 Kickoff: **{npt}** (in {mins} min)", embed=embed)
        else:
            await ctx.reply(embed=embed)
        return

    # No team specified — live > most recent finished > next upcoming
    live = [m for m in matches if m["status"] in ("IN_PLAY", "PAUSED")]
    if live:
        raw   = await _wc_fetch_live_match(live[0]["id"])
        embed = _wc_score_embed(live[0], raw)
        await ctx.reply(embed=embed)
        return

    recent = sorted(
        [m for m in matches if m["status"] == "FINISHED"],
        key=lambda m: m["kickoff"], reverse=True
    )
    if recent:
        raw   = await _wc_fetch_live_match(recent[0]["id"])
        embed = _wc_score_embed(recent[0], raw)
        await ctx.reply(f"✅ Most recent result:", embed=embed)
        return

    # Nothing live or finished — show next upcoming
    upcoming = sorted(
        [m for m in matches if m["kickoff"] > now_utc],
        key=lambda m: m["kickoff"]
    )
    if upcoming:
        nxt  = upcoming[0]
        npt  = nxt["kickoff"].astimezone(nepal_tz).strftime("%b %d, %H:%M NPT")
        mins = int((nxt["kickoff"] - now_utc).total_seconds() / 60)
        await ctx.reply(
            f"⚽ No matches live or finished yet.\n"
            f"Next: **{nxt['name']}** — {npt} (in {mins} min)\n"
            f"Use `.wcstatus` for the full schedule."
        )
    else:
        await ctx.reply("⚽ No World Cup matches found. Try `.wctest` to check the API.")


@require_feature(FEATURE)
@bot.command(name="streamgo")
async def stream_go(ctx: commands.Context):
    """Manually run the full stream-start sequence. (Admins only)
    Sequence: join → clickplay → streamstart → fullscreen
    Each step has a 15-second gap. The clickplay step force-closes and
    reopens Edge to guarantee a single clean tab on watchdgo.com/en, then
    polls for the /en/live_events/ button for up to 30 seconds and clicks
    it. streamstart then shares the freshly opened window, and fullscreen
    maximizes the view.
    """
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    await _wc_run_sequence(WC_PRE_COMMANDS, "Manual Stream Start", ctx.channel)


@require_feature(FEATURE)
@bot.command(name="streamend")
async def stream_end(ctx: commands.Context):
    """Manually run the full stream-end sequence. (Admins only)
    Sequence: resume → closeedge → disconnect
    Each step has a 15-second gap. The closeedge step force-closes Edge
    (dropping any active Discord screen share) then reopens a fresh blank
    Edge window — Edge staying closed too long makes Discord stop
    recognizing it as an active "game". disconnect then leaves the VC.
    """
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    await _wc_run_sequence(WC_POST_COMMANDS, "Manual Stream End", ctx.channel)


# ── Admin commands ────────────────────────────────────────────────────────────

@require_feature(FEATURE)
@bot.command(name="wcenable")
async def wc_enable(ctx: commands.Context):
    """Enable the World Cup auto-stream scheduler. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    global WC_SCHEDULER_ENABLED
    WC_SCHEDULER_ENABLED = True
    await ctx.reply("✅ ⚽ World Cup auto-stream scheduler **enabled**!")


@require_feature(FEATURE)
@bot.command(name="wcdisable")
async def wc_disable(ctx: commands.Context):
    """Disable the World Cup auto-stream scheduler. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    global WC_SCHEDULER_ENABLED
    WC_SCHEDULER_ENABLED = False
    await ctx.reply("🛑 World Cup auto-stream scheduler **disabled**.")


@require_feature(FEATURE)
@bot.command(name="wcstatus")
async def wc_status(ctx: commands.Context):
    """Show upcoming World Cup matches and scheduler status."""
    status_str = "✅ Enabled" if WC_SCHEDULER_ENABLED else "🛑 Disabled"

    # Force a fresh fetch every time
    global _wc_matches_cache, _wc_cache_time
    _wc_cache_time = 0.0

    matches  = await _wc_fetch_matches()
    now_utc  = datetime.now(timezone.utc)
    nepal_tz = pytz.timezone("Asia/Kathmandu")

    # Show live matches first, then next 4 upcoming
    live     = [m for m in matches if m["status"] in ("IN_PLAY", "PAUSED")]
    upcoming = sorted(
        [m for m in matches if m["kickoff"] > now_utc and m["status"] not in ("IN_PLAY", "PAUSED", "FINISHED")],
        key=lambda m: m["kickoff"]
    )[:4]
    display  = live + upcoming

    embed = discord.Embed(
        title="⚽ World Cup 2026 Auto-Stream Scheduler",
        color=discord.Color.green() if WC_SCHEDULER_ENABLED else discord.Color.red(),
    )
    embed.add_field(name="Status", value=status_str, inline=True)
    embed.add_field(name="Data Source", value="football-data.org (live)", inline=True)
    embed.add_field(
        name="Matches",
        value=f"{len(matches)} total • {len(live)} live • {len(upcoming)} upcoming",
        inline=False,
    )

    if display:
        lines = []
        for m in display:
            local_kick  = m["kickoff"].astimezone(nepal_tz)
            mins_away   = int((m["kickoff"] - now_utc).total_seconds() / 60)
            pre_fired   = "✅" if f"{m['id']}_pre"  in _wc_scheduled else "⏳"
            post_fired  = "✅" if f"{m['id']}_post" in _wc_scheduled else "⏳"
            live_badge  = f"🔴 **{m['status']}**" if m["status"] in ("IN_PLAY", "PAUSED") else ""
            fin_at      = _wc_finished_at.get(m["id"])
            fin_str     = f" (ended {int((now_utc - fin_at).total_seconds()/60)}m ago)" if fin_at else ""
            lines.append(
                f"**{m['name']}** {live_badge}\n"
                f"🕐 {local_kick.strftime('%b %d, %H:%M')} NPT"
                + (f" (in {mins_away} min)" if mins_away > 0 else fin_str)
                + f"\nPre: {pre_fired}  Post: {post_fired}"
            )
        embed.add_field(name="Live / Upcoming", value="\n\n".join(lines), inline=False)
    else:
        embed.add_field(name="Upcoming Matches", value="No matches found. Run `.wctest` to diagnose.", inline=False)

    embed.set_footer(
        text=f"Pre fires {WC_PRE_WINDOW_LOW}–{WC_PRE_WINDOW_HIGH} min before kickoff • "
             f"Post fires {WC_POST_DELAY} min after FINISHED (fallback: {WC_FALLBACK_MINUTES} min)"
    )
    await ctx.reply(embed=embed)


@require_feature(FEATURE)
@bot.command(name="wctest")
async def wc_test(ctx: commands.Context):
    """Diagnose the World Cup API fetch — shows live status. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return

    key_set = bool(os.getenv("FOOTBALL_DATA_API_KEY", ""))
    await ctx.reply(f"🔍 Querying football-data.org… (API key: {'✅ set' if key_set else '❌ missing — add FOOTBALL_DATA_API_KEY to .env'})")

    url = f"{_FD_BASE}/competitions/{_FD_WC_COMPETITION}/matches"
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_fd_ssl())) as session:
            async with session.get(url, headers=_fd_headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                status_code = resp.status
                data        = await resp.json()

        if status_code == 403:
            await ctx.reply("❌ **403 Forbidden** — your API key is missing or wrong.\nGet a free key at https://www.football-data.org/client/register")
            return
        if status_code != 200:
            await ctx.reply(f"❌ HTTP {status_code}: {str(data)[:300]}")
            return

        all_matches = data.get("matches", [])
        now_utc     = datetime.now(timezone.utc)
        nepal_tz    = pytz.timezone("Asia/Kathmandu")

        parsed = []
        for m in all_matches:
            try:
                ko      = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                home    = m.get("homeTeam", {}).get("shortName") or m.get("homeTeam", {}).get("name", "TBD")
                away    = m.get("awayTeam", {}).get("shortName") or m.get("awayTeam", {}).get("name", "TBD")
                status  = m.get("status", "?")
                parsed.append((ko, home, away, status))
            except Exception:
                continue

        live     = [(ko, h, a, s) for ko, h, a, s in parsed if s in ("IN_PLAY", "PAUSED")]
        upcoming = sorted([(ko, h, a, s) for ko, h, a, s in parsed if ko > now_utc and s == "TIMED"])
        lines    = [f"✅ HTTP {status_code} — {len(all_matches)} matches ({len(live)} live, {len(upcoming)} upcoming)\n"]

        if live:
            lines.append("**🔴 LIVE NOW:**")
            for ko, h, a, s in live:
                lines.append(f"  `{s}` — **{h} vs {a}**")
            lines.append("")

        lines.append("**⏳ Next 8 upcoming:**")
        for ko, h, a, s in upcoming[:8]:
            npt  = ko.astimezone(nepal_tz)
            mins = int((ko - now_utc).total_seconds() / 60)
            lines.append(f"`{npt.strftime('%b %d %H:%M')} NPT` (+{mins}m) — {h} vs {a}")

        await ctx.reply("\n".join(lines))

    except Exception as e:
        await ctx.reply(f"❌ Exception: `{type(e).__name__}: {e}`")


@require_feature(FEATURE)
@bot.command(name="wcreset")
async def wc_reset(ctx: commands.Context):
    """Clear the scheduler's fired-match memory (re-arm all matches). (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    _wc_scheduled.clear()
    _wc_matches_cache.clear()
    _wc_finished_at.clear()
    _wc_score_messages.clear()
    _wc_last_score_render.clear()
    _wc_cache_time = 0.0
    await ctx.reply("🔄 World Cup scheduler + live scores reset — all matches re-armed!")




# Aliases used by cogs/events.py
wc_scheduler_loop = _wc_scheduler_loop
wc_live_score_loop = _wc_live_score_loop
