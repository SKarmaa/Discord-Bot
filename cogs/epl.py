"""
cogs/epl.py — English Premier League auto-stream scheduler + live score tracker.

DISABLED BY DEFAULT: gated behind the "epl_tracker" toggle in features.json
(default: false). Requires a FOOTBALL_DATA_API_KEY in .env and depends on
cogs/pc_control.py's AHK bridge (_send_ahk_command, _pc_admin_check) to
actually join the voice channel and turn the camera on/off, so it only
really does anything useful when "pc_control" is also enabled.

HOW THIS DIFFERS FROM worldcup.py:
worldcup.py drives a browser (Edge + watchdgo.com) that gets screen-shared.
This setup instead feeds a physical NetTV set-top box's HDMI output through
a USB capture card that Discord sees as a normal webcam — so there is no
browser to click through. The whole pre-match sequence is just:
    join the voice channel  ->  turn the camera on
and post-match is just:
    turn the camera off  ->  disconnect
That's what _epl_pre_commands() / _epl_post_commands() send below, reusing
the same "join" / "disconnect" / "streamstart" / "streamstop" AHK commands
pc_control.py already exposes (streamstart/streamstop now toggle the
camera — see ahk bridge script — not a browser screen-share). If the
"epl_control_stb_power" toggle is on, those two functions also add a
"stbon"/"stboff" step routed to the Arduino IR bridge instead of AHK —
see cogs/pc_control.py's stb_ir_control section.

Commands (.eplenable / .epldisable / .eplstatus / .epltest / .eplreset /
.eplscores / .eplscore / .eplgo / .eplend) are all individually gated by
@require_feature("epl_tracker") below.
"""
import asyncio
import os
from datetime import datetime, timezone

import aiohttp
import discord
import pytz
from discord.ext import commands

from bot_instance import bot
from config import FEATURES
from core.features import require_feature
from core.mention_safety import neutralize_mentions
from cogs.pc_control import (
    _send_ahk_command, _send_ir_command, _send_adb_keyevent, _pc_admin_check,
    stb_full_start_sequence, stb_full_end_sequence,
)

FEATURE = "epl_tracker"

# ==================== EPL AUTO-STREAM SCHEDULER ====================
#
# Uses football-data.org free API (same account/key as worldcup.py).
# Add FOOTBALL_DATA_API_KEY=your_key to your .env file (shared with WC).
#
# HOW IT WORKS:
#   - Polls football-data.org every 30 s (60 s during live matches)
#   - PRE-MATCH: fires join + camera-on exactly 5 min before kickoff
#     (window: 4.5-5.5 min before kickoff so the 30s loop never misses it)
#   - POST-MATCH: fires camera-off + disconnect 5 min after the API
#     reports status = FINISHED (real game-end, not a time guess).
#     Fallback: if API never updates, fires 130 min after kickoff.
#
# Match statuses from football-data.org:
#   TIMED      - scheduled, not started yet
#   IN_PLAY    - currently playing (1st half)
#   PAUSED     - half-time
#   FINISHED   - full-time (or AET/penalties done)
#   POSTPONED / CANCELLED / SUSPENDED
#
# Commands:
#   .eplenable / .epldisable  - toggle scheduler
#   .eplstatus                - show upcoming matches + scheduler state
#   .epltest                  - live API diagnostic
#   .eplreset                 - clear fired-match memory (re-arm all)

EPL_SCHEDULER_ENABLED = True           # toggle with .eplenable / .epldisable
_epl_scheduled: dict[str, str] = {}    # "{match_id}_pre" | "{match_id}_post" -> "fired"
_epl_matches_cache: list[dict] = []
_epl_cache_time: float = 0.0
_epl_finished_at: dict[str, datetime] = {}  # match_id -> UTC time we first saw FINISHED

# ── Concurrent-match stream tracking ────────────────────────────────────────
# Multiple EPL matches can be live at once. The box/camera/VC must only be
# started ONCE even if a second match's pre-window fires while we're
# already streaming, and must only be torn down once EVERY match that
# triggered a start has also finished (not just the first one to end).
_epl_streaming_active = False      # True from the first successful start until the last active match ends
_epl_active_matches: set[str] = set()   # match ids currently "holding the stream open"
EPL_CACHE_TTL_IDLE = 600               # seconds between refreshes when no match is near
EPL_CACHE_TTL_LIVE = 60                # seconds between refreshes when a match is live/close
EPL_PRE_WINDOW_LOW  = 4.5              # minutes before kickoff - start of firing window
EPL_PRE_WINDOW_HIGH = 5.5              # minutes before kickoff - end of firing window
EPL_POST_DELAY      = 5                # minutes after FINISHED before firing post-sequence
EPL_FALLBACK_MINUTES = 130             # fallback: fire post-match N min after kickoff if API stale

# Pre-match command sequence: optionally power the STB on first (if
# "epl_control_stb_power" is enabled in features.json), then join VC, then
# turn the camera on. Built as a function (not a fixed list) so flipping
# these toggles takes effect on the next fire without a restart.
#
# Two power-control methods can be configured (cogs/pc_control.py has the
# setup for each): ADB (stb_adb_control — preferred when available, since
# KEYCODE_WAKEUP/KEYCODE_SLEEP are real distinct commands, not a toggle)
# and Arduino+IR (stb_ir_control — the fallback for boxes with no
# Developer Options, where the remote may only have a single toggle
# button). If both happen to be enabled, ADB wins.
def _epl_pre_commands() -> list[str]:
    cmds = []
    if FEATURES.get("epl_control_stb_power", False):
        if FEATURES.get("stb_adb_control", False):
            cmds.append("stbadbon")
        elif FEATURES.get("stb_ir_control", False):
            cmds.append("stbon")
    cmds += ["join", "streamstart"]
    return cmds


# Post-match: camera off, leave the VC, then optionally power the STB off.
def _epl_post_commands() -> list[str]:
    cmds = ["streamstop", "disconnect"]
    if FEATURES.get("epl_control_stb_power", False):
        if FEATURES.get("stb_adb_control", False):
            cmds.append("stbadboff")
        elif FEATURES.get("stb_ir_control", False):
            cmds.append("stboff")
    return cmds


# "stbon"/"stboff" don't go through the AHK bridge like everything else —
# they're a direct serial command to the Arduino IR bridge instead. This
# maps the EPL-sequence command name to the exact string ir_bridge.ino
# expects (see cogs/pc_control.py's stbon/stboff commands for the same
# on/off-with-toggle-fallback logic; this sequence path calls the same
# underlying names for consistency but does not do the toggle fallback
# itself — if you haven't configured POWER_ON/POWER_OFF in ir_bridge.ino,
# use .stbtest to check before relying on the auto-scheduler for this).
_STB_IR_COMMAND_MAP = {"stbon": "POWER_ON", "stboff": "POWER_OFF"}

# "stbadbon"/"stbadboff" similarly bypass the AHK bridge, going straight to
# pc_control.py's ADB keyevent sender instead — see _epl_run_sequence below.
_STB_ADB_COMMAND_MAP = {"stbadbon": "wake", "stbadboff": "sleep"}

# football-data.org - free tier, 10 req/min, no cost
# Premier League competition code: PL   (id: 2021)
_FD_BASE = "https://api.football-data.org/v4"
_FD_EPL_COMPETITION = "PL"   # or use numeric id 2021


def _fd_headers() -> dict:
    """Return auth headers for football-data.org. Key is optional on free tier but required for PL."""
    key = os.getenv("FOOTBALL_DATA_API_KEY", "")
    h = {"Accept": "application/json"}
    if key:
        h["X-Auth-Token"] = key
    return h


def _fd_ssl() -> "ssl.SSLContext":
    """Disabled-verification SSL context - fixes Windows CA bundle issues."""
    import ssl
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def _epl_fetch_matches() -> list[dict]:
    """
    Fetch EPL matches from football-data.org.
    Returns list of dicts with keys: id, name, kickoff (UTC datetime), status.
    Status values: TIMED | IN_PLAY | PAUSED | FINISHED | POSTPONED | CANCELLED
    Falls back to cached data on error.
    """
    global _epl_matches_cache, _epl_cache_time

    now_loop = asyncio.get_event_loop().time()
    now_utc  = datetime.now(timezone.utc)
    is_close = any(
        abs((m["kickoff"] - now_utc).total_seconds()) < 7200   # within 2 hours of kickoff
        or m["status"] in ("IN_PLAY", "PAUSED")
        for m in _epl_matches_cache
    )
    ttl = EPL_CACHE_TTL_LIVE if is_close else EPL_CACHE_TTL_IDLE

    if _epl_matches_cache and (now_loop - _epl_cache_time) < ttl:
        return _epl_matches_cache

    url = f"{_FD_BASE}/competitions/{_FD_EPL_COMPETITION}/matches"
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_fd_ssl())) as session:
            async with session.get(url, headers=_fd_headers(), timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status == 403:
                    print("[EPL Scheduler] football-data.org: 403 - add FOOTBALL_DATA_API_KEY to .env")
                    return _epl_matches_cache
                if resp.status != 200:
                    print(f"[EPL Scheduler] football-data.org HTTP {resp.status}")
                    return _epl_matches_cache
                data = await resp.json()

        matches = []
        for m in data.get("matches", []):
            mid     = str(m.get("id", ""))
            status  = m.get("status", "TIMED")
            utc_str = m.get("utcDate", "")
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

        _epl_matches_cache = matches
        _epl_cache_time    = now_loop
        live_count = sum(1 for m in matches if m["status"] in ("IN_PLAY", "PAUSED"))
        print(f"[EPL Scheduler] Loaded {len(matches)} EPL matches ({live_count} live) from football-data.org")
        return matches

    except Exception as e:
        print(f"[EPL Scheduler] Fetch error: {e}")
        return _epl_matches_cache


async def _epl_run_sequence(commands: list[str], label: str, channel: discord.TextChannel):
    """Send a sequence of commands with a short gap, posting status in channel.
    Most steps go through the AHK bridge (join/streamstart/streamstop/
    disconnect); stbon/stboff go straight to the Arduino IR bridge (see
    _STB_IR_COMMAND_MAP), and stbadbon/stbadboff go straight to the ADB
    keyevent sender (see _STB_ADB_COMMAND_MAP) instead. No browser/page to
    wait on here (unlike WC's clickplay), so a 5s gap is plenty between
    steps."""
    await channel.send(f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 **EPL Auto-Scheduler** › {label} — starting sequence…")
    for i, cmd in enumerate(commands):
        if cmd in _STB_IR_COMMAND_MAP:
            ok, msg = await _send_ir_command(_STB_IR_COMMAND_MAP[cmd])
        elif cmd in _STB_ADB_COMMAND_MAP:
            ok, msg = await _send_adb_keyevent(_STB_ADB_COMMAND_MAP[cmd])
        else:
            ok, msg = await _send_ahk_command(cmd, timeout=8.0)
        emoji = "✅" if ok else "❌"
        await channel.send(f"{emoji} `.{cmd}` {'done' if ok else f'failed: {msg}'}")
        if i < len(commands) - 1:
            await asyncio.sleep(5)
    await channel.send("✅ **Sequence complete!**")


async def _epl_run_full_start(label: str, channel: discord.TextChannel):
    """Dispatch the start sequence: the full power+channel+join+camera
    sequence over ADB when available (preferred — real distinct power
    keyevent, and can switch to the right channel), otherwise fall back to
    the AHK-only join+camera sequence (optionally with Arduino IR power)."""
    if FEATURES.get("epl_control_stb_power", False) and FEATURES.get("stb_adb_control", False):
        await channel.send(f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 **EPL Auto-Scheduler** › {label} — starting full sequence…")
        results = await stb_full_start_sequence(os.getenv("STB_DEFAULT_CHANNEL") or None)
        for cmd, ok, msg in results:
            emoji = "✅" if ok else "❌"
            await channel.send(f"{emoji} `{cmd}` {'done' if ok else f'failed: {msg}'}")
        await channel.send("✅ **Sequence complete!**")
    else:
        await _epl_run_sequence(_epl_pre_commands(), label, channel)


async def _epl_run_full_end(label: str, channel: discord.TextChannel):
    """Dispatch the end sequence: sleep (ADB) + disconnect when ADB power
    control is on, otherwise the old AHK/IR camera-off + disconnect (+
    optional IR power-off) sequence."""
    if FEATURES.get("epl_control_stb_power", False) and FEATURES.get("stb_adb_control", False):
        await channel.send(f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 **EPL Auto-Scheduler** › {label} — starting full sequence…")
        results = await stb_full_end_sequence()
        for cmd, ok, msg in results:
            emoji = "✅" if ok else "❌"
            await channel.send(f"{emoji} `{cmd}` {'done' if ok else f'failed: {msg}'}")
        await channel.send("✅ **Sequence complete!**")
    else:
        await _epl_run_sequence(_epl_post_commands(), label, channel)


async def _epl_scheduler_loop():
    """Background task - polls every 30 s and fires commands at the right times."""
    global _epl_streaming_active
    await bot.wait_until_ready()
    print("🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL scheduler started (football-data.org live status).")

    # Startup guard: pre-mark all already-FINISHED/past matches so we never
    # fire post-match commands for games that ended before this session.
    try:
        startup_matches = await _epl_fetch_matches()
        now_utc_boot = datetime.now(timezone.utc)
        skipped = 0
        for m in startup_matches:
            post_key = f"{m['id']}_post"
            pre_key  = f"{m['id']}_pre"
            if m["status"] == "FINISHED":
                _epl_scheduled.setdefault(post_key, "fired")
                _epl_scheduled.setdefault(pre_key,  "fired")
                _epl_finished_at.setdefault(m["id"], now_utc_boot)
                skipped += 1
            elif m["kickoff"] <= now_utc_boot:
                _epl_scheduled.setdefault(pre_key, "fired")
        print(f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 Startup: pre-armed {skipped} already-finished matches (won't fire post-match).")
    except Exception as e:
        print(f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 Startup pre-arm error: {e}")

    while not bot.is_closed():
        if not EPL_SCHEDULER_ENABLED:
            await asyncio.sleep(30)
            continue

        channel = bot.get_channel(FEATURES.get("epl_target_channel_id") or FEATURES.get("target_channel_id", 0))
        if channel is None:
            await asyncio.sleep(30)
            continue

        try:
            matches  = await _epl_fetch_matches()
            now_utc  = datetime.now(timezone.utc)

            for match in matches:
                mid     = match["id"]
                kickoff = match["kickoff"]
                name    = match["name"]
                status  = match["status"]
                minutes_to_start    = (kickoff - now_utc).total_seconds() / 60
                minutes_since_start = (now_utc - kickoff).total_seconds() / 60

                # PRE-MATCH: fire once, exactly in the pre-window before kickoff.
                # If we're already streaming (another match is live/holding
                # the stream open), just register this match as another
                # active one WITHOUT re-running the start sequence.
                pre_key = f"{mid}_pre"
                if pre_key not in _epl_scheduled and EPL_PRE_WINDOW_LOW <= minutes_to_start <= EPL_PRE_WINDOW_HIGH:
                    _epl_scheduled[pre_key] = "fired"
                    _epl_active_matches.add(mid)
                    if not _epl_streaming_active:
                        _epl_streaming_active = True
                        print(f"[EPL Scheduler] PRE-MATCH -> {name} (starting stream, kicks off in {minutes_to_start:.1f} min)")
                        asyncio.create_task(
                            _epl_run_full_start(f"Pre-match — **{name}** kicks off in ~5 min!", channel)
                        )
                    else:
                        print(f"[EPL Scheduler] PRE-MATCH -> {name} (already streaming — tracking as concurrent match, no restart)")
                        asyncio.create_task(
                            channel.send(
                                f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 **{name}** kicks off in ~5 min — already streaming for another match, "
                                f"not restarting the sequence. ({len(_epl_active_matches)} match(es) now holding the stream open.)"
                            )
                        )

                # POST-MATCH detection
                post_key = f"{mid}_post"
                if post_key in _epl_scheduled:
                    continue

                if status == "FINISHED":
                    if mid not in _epl_finished_at:
                        _epl_finished_at[mid] = now_utc
                        print(f"[EPL Scheduler] API says FINISHED for {name}, waiting {EPL_POST_DELAY} min…")
                    elif (now_utc - _epl_finished_at[mid]).total_seconds() / 60 >= EPL_POST_DELAY:
                        _epl_scheduled[post_key] = "fired"
                        _epl_active_matches.discard(mid)
                        print(f"[EPL Scheduler] POST-MATCH (API FINISHED+{EPL_POST_DELAY}m) -> {name}")
                        if not _epl_active_matches:
                            _epl_streaming_active = False
                            asyncio.create_task(
                                _epl_run_full_end(f"Post-match — **{name}** has ended!", channel)
                            )
                        else:
                            asyncio.create_task(
                                channel.send(
                                    f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 **{name}** has ended, but {len(_epl_active_matches)} other match(es) "
                                    f"are still live — keeping the stream running."
                                )
                            )
                elif minutes_since_start >= EPL_FALLBACK_MINUTES:
                    _epl_scheduled[post_key] = "fired"
                    _epl_active_matches.discard(mid)
                    print(f"[EPL Scheduler] POST-MATCH (fallback {EPL_FALLBACK_MINUTES}m) -> {name}")
                    if not _epl_active_matches:
                        _epl_streaming_active = False
                        asyncio.create_task(
                            _epl_run_full_end(f"Post-match — **{name}** (fallback timer, API may be stale)", channel)
                        )
                    else:
                        asyncio.create_task(
                            channel.send(
                                f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 **{name}** hit its fallback timer, but {len(_epl_active_matches)} other "
                                f"match(es) are still live — keeping the stream running."
                            )
                        )

        except Exception as e:
            print(f"[EPL Scheduler] Loop error: {e}")

        await asyncio.sleep(30)


# ==================== EPL LIVE SCORE TRACKER ====================
#
# When a match goes IN_PLAY the bot posts a score embed in the target
# channel. That message is then edited every 60 s with the latest score +
# minute. When the match reaches FINISHED the embed gets a final update.
#
# New commands:
#   .eplscore [team]   - fetch current/latest score for a team (or all live)
#   .eplscores         - list every match live right now

_epl_score_messages: dict[str, discord.Message] = {}
_epl_last_score_render: dict[str, str] = {}

SCORE_UPDATE_INTERVAL = 60   # seconds between live score edits


async def _epl_fetch_live_match(match_id: str) -> dict | None:
    """Fetch a single match's detailed live data from football-data.org."""
    url = f"{_FD_BASE}/matches/{match_id}"
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=_fd_ssl())) as session:
            async with session.get(url, headers=_fd_headers(), timeout=aiohttp.ClientTimeout(total=8)) as resp:
                if resp.status != 200:
                    return None
                return await resp.json()
    except Exception:
        return None


def _epl_score_embed(match: dict, raw: dict | None = None) -> discord.Embed:
    """Build a Discord Embed for a live/finished EPL match."""
    status  = raw["status"] if raw else match["status"]
    home    = raw["homeTeam"]["shortName"] if raw else match["name"].split(" vs ")[0]
    away    = raw["awayTeam"]["shortName"] if raw else match["name"].split(" vs ")[-1]

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

    if status == "FINISHED":
        color = discord.Color.dark_green()
        title = "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Full Time"
    elif status == "PAUSED":
        color = discord.Color.orange()
        title = "🏴󠁧󠁢󠁥󠁮󠁧󠁿 Half Time"
    elif status == "IN_PLAY":
        color = discord.Color.green()
        title = f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 LIVE{f' — {minute_str}′' if minute_str else ''}"
    else:
        color = discord.Color.greyple()
        title = f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 {status.title()}"

    embed = discord.Embed(title=title, color=color)
    embed.add_field(
        name=f"🏟️ {home}  vs  {away}",
        value=f"## {score_home}  –  {score_away}",
        inline=False,
    )

    nepal_tz    = pytz.timezone("Asia/Kathmandu")
    kickoff_npt = match["kickoff"].astimezone(nepal_tz).strftime("%b %d, %H:%M NPT")
    embed.set_footer(text=f"Kickoff: {kickoff_npt} • Updates every {SCORE_UPDATE_INTERVAL}s")
    return embed


async def _epl_live_score_loop():
    """Background task - runs alongside _epl_scheduler_loop.
    Posts + keeps editing live-score embeds for every IN_PLAY / PAUSED match."""
    await bot.wait_until_ready()
    print("📊 EPL live-score tracker started.")
    await asyncio.sleep(5)

    while not bot.is_closed():
        if not EPL_SCHEDULER_ENABLED:
            await asyncio.sleep(SCORE_UPDATE_INTERVAL)
            continue

        channel = bot.get_channel(FEATURES.get("epl_target_channel_id") or FEATURES.get("target_channel_id", 0))
        if channel is None:
            await asyncio.sleep(SCORE_UPDATE_INTERVAL)
            continue

        try:
            matches = await _epl_fetch_matches()

            for match in matches:
                mid    = match["id"]
                status = match["status"]

                if status not in ("IN_PLAY", "PAUSED", "FINISHED"):
                    continue

                post_key = f"{mid}_post"
                if _epl_scheduled.get(post_key) == "fired" and mid not in _epl_score_messages:
                    continue  # pre-armed at startup - never went live this session

                score_done_key = f"{mid}_score_done"
                if score_done_key in _epl_scheduled and status == "FINISHED":
                    continue

                raw = await _epl_fetch_live_match(mid)
                if raw is None:
                    continue

                embed = _epl_score_embed(match, raw)

                ft = raw.get("score", {}).get("fullTime", {})
                ht = raw.get("score", {}).get("halfTime", {})
                fingerprint = f"{status}-{ft}-{ht}-{raw.get('minute', '')}"

                if mid in _epl_score_messages:
                    if _epl_last_score_render.get(mid) != fingerprint:
                        try:
                            await _epl_score_messages[mid].edit(embed=embed)
                            _epl_last_score_render[mid] = fingerprint
                        except discord.NotFound:
                            del _epl_score_messages[mid]
                        except Exception:
                            pass
                else:
                    home = raw.get("homeTeam", {}).get("shortName", "?")
                    away = raw.get("awayTeam", {}).get("shortName", "?")
                    msg = await channel.send(
                        content=f"📊 **Live Score — {home} vs {away}**",
                        embed=embed,
                    )
                    _epl_score_messages[mid]    = msg
                    _epl_last_score_render[mid] = fingerprint
                    print(f"[EPL Scores] Posted live score embed for {match['name']}")

                if status == "FINISHED":
                    _epl_scheduled[score_done_key] = "done"
                    print(f"[EPL Scores] Final score posted for {match['name']}")

        except Exception as e:
            print(f"[EPL Scores] Loop error: {e}")

        await asyncio.sleep(SCORE_UPDATE_INTERVAL)


# ── Score commands ─────────────────────────────────────────────────────────

@require_feature(FEATURE)
@bot.command(name="eplscores")
async def epl_scores_cmd(ctx: commands.Context):
    """Show all currently live EPL matches and their scores."""
    global _epl_cache_time
    _epl_cache_time = 0.0
    matches = await _epl_fetch_matches()
    live    = [m for m in matches if m["status"] in ("IN_PLAY", "PAUSED")]

    if not live:
        now_utc  = datetime.now(timezone.utc)
        upcoming = sorted([m for m in matches if m["kickoff"] > now_utc], key=lambda m: m["kickoff"])
        if upcoming:
            nxt = upcoming[0]
            nepal_tz = pytz.timezone("Asia/Kathmandu")
            npt = nxt["kickoff"].astimezone(nepal_tz).strftime("%b %d, %H:%M NPT")
            mins = int((nxt["kickoff"] - now_utc).total_seconds() / 60)
            await ctx.reply(f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 No matches live right now.\nNext: **{nxt['name']}** at {npt} (in {mins} min)")
        else:
            await ctx.reply("🏴󠁧󠁢󠁥󠁮󠁧󠁿 No live or upcoming EPL matches found.")
        return

    embeds = []
    for match in live:
        raw = await _epl_fetch_live_match(match["id"])
        embeds.append(_epl_score_embed(match, raw))

    await ctx.reply(f"🔴 **{len(live)} match(es) live right now:**", embeds=embeds[:10])


@require_feature(FEATURE)
@bot.command(name="eplscore")
async def epl_score_cmd(ctx: commands.Context, *, team: str = ""):
    """Show the live/latest score for a specific team, or next upcoming match if nothing is live."""
    global _epl_cache_time
    _epl_cache_time = 0.0
    matches  = await _epl_fetch_matches()
    now_utc  = datetime.now(timezone.utc)
    nepal_tz = pytz.timezone("Asia/Kathmandu")

    if team:
        team_lower = team.lower()
        candidates = sorted(
            [m for m in matches if team_lower in m["name"].lower()],
            key=lambda m: m["kickoff"], reverse=True
        )
        if not candidates:
            await ctx.reply(f"❌ No matches found for **{neutralize_mentions(team)}**. Check the spelling or use `.eplstatus` to see teams.")
            return
        match = candidates[0]
        raw   = await _epl_fetch_live_match(match["id"])
        embed = _epl_score_embed(match, raw)

        if match["status"] == "TIMED":
            npt  = match["kickoff"].astimezone(nepal_tz).strftime("%b %d, %H:%M NPT")
            mins = int((match["kickoff"] - now_utc).total_seconds() / 60)
            await ctx.reply(f"⏳ **{match['name']}** hasn't kicked off yet.\n🕐 Kickoff: **{npt}** (in {mins} min)", embed=embed)
        else:
            await ctx.reply(embed=embed)
        return

    live = [m for m in matches if m["status"] in ("IN_PLAY", "PAUSED")]
    if live:
        raw   = await _epl_fetch_live_match(live[0]["id"])
        embed = _epl_score_embed(live[0], raw)
        await ctx.reply(embed=embed)
        return

    recent = sorted(
        [m for m in matches if m["status"] == "FINISHED"],
        key=lambda m: m["kickoff"], reverse=True
    )
    if recent:
        raw   = await _epl_fetch_live_match(recent[0]["id"])
        embed = _epl_score_embed(recent[0], raw)
        await ctx.reply("✅ Most recent result:", embed=embed)
        return

    upcoming = sorted([m for m in matches if m["kickoff"] > now_utc], key=lambda m: m["kickoff"])
    if upcoming:
        nxt  = upcoming[0]
        npt  = nxt["kickoff"].astimezone(nepal_tz).strftime("%b %d, %H:%M NPT")
        mins = int((nxt["kickoff"] - now_utc).total_seconds() / 60)
        await ctx.reply(
            f"🏴󠁧󠁢󠁥󠁮󠁧󠁿 No matches live or finished yet.\n"
            f"Next: **{nxt['name']}** — {npt} (in {mins} min)\n"
            f"Use `.eplstatus` for the full schedule."
        )
    else:
        await ctx.reply("🏴󠁧󠁢󠁥󠁮󠁧󠁿 No EPL matches found. Try `.epltest` to check the API.")


@require_feature(FEATURE)
@bot.command(name="eplgo")
async def epl_go(ctx: commands.Context):
    """Manually run the full stream-start sequence (same one the scheduler
    uses): power on -> channel -> join -> camera on (ADB mode), or
    join -> camera on (AHK/IR-only mode). (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    global _epl_streaming_active
    _epl_streaming_active = True
    await _epl_run_full_start("Manual Stream Start", ctx.channel)


@require_feature(FEATURE)
@bot.command(name="eplend")
async def epl_end(ctx: commands.Context):
    """Manually run the full stream-end sequence (same one the scheduler
    uses): sleep -> disconnect (ADB mode), or camera off -> disconnect
    (AHK/IR-only mode). (Admins only)
    NOTE: this ends the stream regardless of whether another match is still
    tracked as active — use this to force-end if the auto-scheduler is
    stuck thinking a match is still live."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    global _epl_streaming_active
    _epl_streaming_active = False
    _epl_active_matches.clear()
    await _epl_run_full_end("Manual Stream End", ctx.channel)


# ── Admin commands ────────────────────────────────────────────────────────

@require_feature(FEATURE)
@bot.command(name="eplenable")
async def epl_enable(ctx: commands.Context):
    """Enable the EPL auto-stream scheduler. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    global EPL_SCHEDULER_ENABLED
    EPL_SCHEDULER_ENABLED = True
    await ctx.reply("✅ 🏴󠁧󠁢󠁥󠁮󠁧󠁿 EPL auto-stream scheduler **enabled**!")


@require_feature(FEATURE)
@bot.command(name="epldisable")
async def epl_disable(ctx: commands.Context):
    """Disable the EPL auto-stream scheduler. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    global EPL_SCHEDULER_ENABLED
    EPL_SCHEDULER_ENABLED = False
    await ctx.reply("🛑 EPL auto-stream scheduler **disabled**.")


@require_feature(FEATURE)
@bot.command(name="eplstatus")
async def epl_status(ctx: commands.Context):
    """Show upcoming EPL matches and scheduler status."""
    status_str = "✅ Enabled" if EPL_SCHEDULER_ENABLED else "🛑 Disabled"

    global _epl_cache_time
    _epl_cache_time = 0.0

    matches  = await _epl_fetch_matches()
    now_utc  = datetime.now(timezone.utc)
    nepal_tz = pytz.timezone("Asia/Kathmandu")

    live     = [m for m in matches if m["status"] in ("IN_PLAY", "PAUSED")]
    upcoming = sorted(
        [m for m in matches if m["kickoff"] > now_utc and m["status"] not in ("IN_PLAY", "PAUSED", "FINISHED")],
        key=lambda m: m["kickoff"]
    )[:4]
    display  = live + upcoming

    embed = discord.Embed(
        title="🏴󠁧󠁢󠁥󠁮󠁧󠁿 Premier League Auto-Stream Scheduler",
        color=discord.Color.green() if EPL_SCHEDULER_ENABLED else discord.Color.red(),
    )
    embed.add_field(name="Status", value=status_str, inline=True)
    embed.add_field(name="Data Source", value="football-data.org (live)", inline=True)
    streaming_str = (
        f"🔴 Live — {len(_epl_active_matches)} match(es) holding it open"
        if _epl_streaming_active else "⚪ Not streaming"
    )
    embed.add_field(name="Stream State", value=streaming_str, inline=True)
    embed.add_field(
        name="Matches",
        value=f"{len(matches)} total • {len(live)} live • {len(upcoming)} upcoming",
        inline=False,
    )

    if display:
        lines = []
        for m in display:
            local_kick = m["kickoff"].astimezone(nepal_tz)
            mins_away  = int((m["kickoff"] - now_utc).total_seconds() / 60)
            pre_fired  = "✅" if f"{m['id']}_pre"  in _epl_scheduled else "⏳"
            post_fired = "✅" if f"{m['id']}_post" in _epl_scheduled else "⏳"
            live_badge = f"🔴 **{m['status']}**" if m["status"] in ("IN_PLAY", "PAUSED") else ""
            fin_at     = _epl_finished_at.get(m["id"])
            fin_str    = f" (ended {int((now_utc - fin_at).total_seconds()/60)}m ago)" if fin_at else ""
            lines.append(
                f"**{m['name']}** {live_badge}\n"
                f"🕐 {local_kick.strftime('%b %d, %H:%M')} NPT"
                + (f" (in {mins_away} min)" if mins_away > 0 else fin_str)
                + f"\nPre: {pre_fired}  Post: {post_fired}"
            )
        embed.add_field(name="Live / Upcoming", value="\n\n".join(lines), inline=False)
    else:
        embed.add_field(name="Upcoming Matches", value="No matches found. Run `.epltest` to diagnose.", inline=False)

    embed.set_footer(
        text=f"Pre fires {EPL_PRE_WINDOW_LOW}–{EPL_PRE_WINDOW_HIGH} min before kickoff • "
             f"Post fires {EPL_POST_DELAY} min after FINISHED (fallback: {EPL_FALLBACK_MINUTES} min)"
    )
    await ctx.reply(embed=embed)


@require_feature(FEATURE)
@bot.command(name="epltest")
async def epl_test(ctx: commands.Context):
    """Diagnose the EPL API fetch - shows live status. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return

    key_set = bool(os.getenv("FOOTBALL_DATA_API_KEY", ""))
    await ctx.reply(f"🔍 Querying football-data.org… (API key: {'✅ set' if key_set else '❌ missing — add FOOTBALL_DATA_API_KEY to .env'})")

    url = f"{_FD_BASE}/competitions/{_FD_EPL_COMPETITION}/matches"
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
                ko     = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00"))
                home   = m.get("homeTeam", {}).get("shortName") or m.get("homeTeam", {}).get("name", "TBD")
                away   = m.get("awayTeam", {}).get("shortName") or m.get("awayTeam", {}).get("name", "TBD")
                status = m.get("status", "?")
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
@bot.command(name="eplreset")
async def epl_reset(ctx: commands.Context):
    """Clear the scheduler's fired-match memory (re-arm all matches). (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    _epl_scheduled.clear()
    _epl_matches_cache.clear()
    _epl_finished_at.clear()
    _epl_score_messages.clear()
    _epl_last_score_render.clear()
    _epl_active_matches.clear()
    global _epl_cache_time, _epl_streaming_active
    _epl_cache_time = 0.0
    _epl_streaming_active = False
    await ctx.reply("🔄 EPL scheduler + live scores reset — all matches re-armed!")


# Aliases used by cogs/events.py
epl_scheduler_loop = _epl_scheduler_loop
epl_live_score_loop = _epl_live_score_loop