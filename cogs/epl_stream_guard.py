"""
cogs/epl_stream_guard.py — keeps the NetTV -> Discord EPL stream alive, and
lets the people watching vote to switch between the two Star Sports Select HD
channels.

Gated behind features.json -> "epl_stream_guard" (needs "epl_tracker",
"stb_adb_control" and "pc_control" too — it reuses their ADB + AHK plumbing).

1) WATCHDOG
   While cogs/epl.py says the EPL stream is live (_epl_streaming_active), every
   `epl_guard_interval_seconds` (default 45 s) this checks, in order, and
   repairs whatever is missing:

     a. NetTV box awake?            (adb dumpsys power)
          -> KEYCODE_WAKEUP, then KEYCODE_POWER if still asleep
     b. Live TV app in front?       (adb dumpsys window / activity)
          -> relaunch the Live TV app (stb_livetv_package, learned with
             `.stbfocus save`) or KEYCODE_TV if it isn't set
     c. On the expected channel?    (504 by default, or 505 after a vote)
          -> re-types the channel number. The channel can only be *verified*
             if the NetTV app publishes the channel name (Star Sports Select HD 1/2)
             via Android's media session; if it doesn't, the bot trusts the
             last channel it tuned and always re-tunes after fixing a/b.
     d. Stream account (epl_stream_account_id) in the stream VC
        (epl_stream_voice_channel_id)?   (Discord voice state — ground truth)
          -> AHK "disconnect" (if it's in the wrong VC) + "join"
     e. That account's camera on?   (voice state self_video)
          -> AHK "streamstart" (camera toggle). Because it's a TOGGLE, it is
             only sent after the voice state says video is OFF twice in a row.

   Each repair has a cooldown, and after repeated failures it posts one
   warning and backs off instead of spamming the channel.

2) CHANNEL VOTE — `.chvote [1|2|504|505]`
   Anyone in the stream VC can request a switch between
     504 = Star Sports Select HD 1   and   505 = Star Sports Select HD 2.
   Everyone currently in that VC (humans only, not the stream account) can
   react ✅ / ❌. When a strict majority of the people in the VC have ✅'d
   (the requester counts as ✅), the bot switches channel. The vote fails on
   timeout or once a majority is no longer reachable. People who leave the VC
   stop counting; people who join can vote.
"""
import asyncio
import json
import re
import time

import discord
from discord.ext import commands

from bot_instance import bot
from config import FEATURES, FEATURES_FILE
from core.features import require_feature
from cogs.pc_control import (
    STB_STATE, _pc_admin_check, _send_adb_app, _send_adb_keyevent,
    _send_ahk_command, adb_shell, stb_default_channel, stb_power_state,
    stb_read_banner, stb_tune_channel,
)

FEATURE = "epl_stream_guard"

DEFAULT_CHANNEL_NAMES = {"504": "Star Sports Select HD 1", "505": "Star Sports Select HD 2"}

START_GRACE_SECONDS = 240       # don't "repair" while the start sequence (boot + Live TV wait) is still running
REPAIR_COOLDOWN = 60            # min seconds between two attempts at the same repair
MAX_FAILS_BEFORE_ALERT = 3      # consecutive failed attempts before posting a warning
BACKOFF_SECONDS = 300           # after alerting, retry this repair only every 5 min
VOTE_COOLDOWN_SECONDS = 30      # gap between the end of one vote and the next request

YES, NO = "✅", "❌"

# ── config helpers (read live so features.json edits + .reload apply) ───────


def _vc_id() -> int:
    return int(FEATURES.get("epl_stream_voice_channel_id") or 1514648474799575162)


def _account_id() -> int:
    return int(FEATURES.get("epl_stream_account_id") or 765577654438330429)


def _channel_names() -> dict[str, str]:
    names = FEATURES.get("stb_epl_channels") or DEFAULT_CHANNEL_NAMES
    return {str(k): str(v) for k, v in names.items()}


def _label(ch: str | None) -> str:
    if not ch:
        return "unknown"
    name = _channel_names().get(str(ch))
    return f"{name} ({ch})" if name else f"channel {ch}"


def _expected_channel() -> str:
    return STB_STATE.get("channel") or stb_default_channel()


def _report_channel():
    return bot.get_channel(FEATURES.get("epl_target_channel_id") or FEATURES.get("target_channel_id", 0))


def _is_streaming() -> bool:
    """EPL stream is on according to the scheduler (also restored after a
    mid-match restart). This is what the watchdog runs on."""
    try:
        import cogs.epl as epl
        return bool(epl._epl_streaming_active)
    except Exception:
        return False


def _stream_live() -> bool:
    """For .chvote: the scheduler says we're streaming, OR the stream account
    is actually in the stream VC with video on (e.g. started with .ststart)."""
    if _is_streaming():
        return True
    st = discord_state()
    return bool(st["in_vc"] and st["video"])


def _save_feature(key: str, value) -> None:
    """Persist one key into features.json (and the live FEATURES dict)."""
    try:
        with open(FEATURES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        data = {}
    data[key] = value
    with open(FEATURES_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    FEATURES[key] = value


# ── shared state ────────────────────────────────────────────────────────────

_box_lock = asyncio.Lock()          # serialises every action on the box/Discord client
_guard_enabled = True               # runtime on/off (.eplguard on/off)
_stream_started_at = 0.0            # monotonic time of the last stream start
_loop_started = False
_repair_last: dict[str, float] = {}  # repair name -> monotonic time of last attempt
_repair_fails: dict[str, int] = {}   # repair name -> consecutive failures
_alerted: set[str] = set()           # repair names we've already warned about


def on_stream_start() -> str:
    """Called by cogs/epl.py right before every stream start sequence. Resets
    the channel to the default (504) and gives the start sequence a grace
    period before the watchdog starts second-guessing it. Returns the
    channel the start sequence should tune to."""
    global _stream_started_at
    _stream_started_at = time.monotonic()
    _repair_last.clear()
    _repair_fails.clear()
    _alerted.clear()
    STB_STATE["channel"] = None
    return stb_default_channel()


# ── checks (read-only) ──────────────────────────────────────────────────────

_FOCUS_PATTERNS = [
    r"mCurrentFocus=Window\{[^}]*?\s([\w.]+)/([\w.$]+)",
    r"mFocusedApp=[^\n]*?\s([\w.]+)/([\w.$]+)",
    r"(?:topResumedActivity|mResumedActivity|ResumedActivity)[=:][^\n]*?\s([\w.]+)/([\w.$]+)",
]
_LAUNCHER_HINTS = ("launcher", "leanback", "tvhome", "home.tv", "settings")


async def foreground_app() -> tuple[str, str] | None:
    """(package, activity) of the app in front on the box, or None."""
    for args in (("dumpsys", "window"), ("dumpsys", "activity", "activities")):
        ok, out = await adb_shell(*args)
        if not ok or not out:
            continue
        for pat in _FOCUS_PATTERNS:
            m = re.search(pat, out)
            if m:
                return m.group(1), m.group(2)
    return None


def _on_live_tv(fg: tuple[str, str] | None) -> bool | None:
    """True/False if we can decide, None if we can't."""
    if fg is None:
        return None
    pkg = fg[0].lower()
    configured = (FEATURES.get("stb_livetv_package") or "").strip().lower()
    if configured:
        return pkg == configured
    if any(h in pkg for h in _LAUNCHER_HINTS):
        return False       # sitting on the home screen / settings -> definitely not live TV
    return None            # some other app — can't be sure without stb_livetv_package


_last_channel_check = 0.0


async def detect_channel(force: bool = False) -> str | None:
    """Which channel the box is on, read from NetTV's channel banner.
    The banner is only on screen briefly, so this presses
    `stb_channel_check_key` (a key that shows the banner WITHOUT changing
    channel, e.g. KEYCODE_INFO / KEYCODE_DPAD_CENTER on some boxes) first.
    If that key isn't configured it returns None (can't check), and the
    guard trusts the last channel it typed — which is verified on screen
    every time it tunes."""
    global _last_channel_check
    key = (FEATURES.get("stb_channel_check_key") or "").strip()
    if not key:
        return None
    # The overlay is visible to viewers, so only check every few minutes.
    every = int(FEATURES.get("stb_channel_check_interval_seconds", 300) or 300)
    if not force and time.monotonic() - _last_channel_check < every:
        return None
    _last_channel_check = time.monotonic()
    shown = await stb_read_banner(press=key)
    dismiss = (FEATURES.get("stb_channel_check_dismiss_key") or "").strip()
    if dismiss:
        await _send_adb_keyevent_raw(dismiss)
    return next(iter(shown)) if len(shown) == 1 else None


async def _send_adb_keyevent_raw(code: str):
    await adb_shell("input", "keyevent", code)


def discord_state() -> dict:
    """Where is the stream account, and is its camera on?"""
    vc = bot.get_channel(_vc_id())
    acc = _account_id()
    if vc is None:
        return {"vc_found": False, "in_vc": None, "video": None, "other_vc": None}
    vs = vc.voice_states.get(acc)
    other = None
    if vs is None:
        member = vc.guild.get_member(acc)
        if member and member.voice and member.voice.channel:
            other = member.voice.channel
    return {
        "vc_found": True,
        "in_vc": vs is not None,
        "video": bool(vs.self_video) if vs else False,
        "other_vc": other,
    }


async def collect_status() -> dict:
    awake = await stb_power_state()
    fg = await foreground_app() if awake else None
    return {
        "awake": awake,
        "foreground": fg,
        "live_tv": _on_live_tv(fg) if awake else False,
        "channel_detected": await detect_channel(force=True) if awake else None,
        "channel_expected": _expected_channel(),
        **discord_state(),
    }


def _status_lines(st: dict) -> list[str]:
    def yn(v, yes="✅", no="❌", unk="❔"):
        return unk if v is None else (yes if v else no)
    fg = f"`{st['foreground'][0]}`" if st.get("foreground") else "unknown"
    det = st.get("channel_detected")
    if det is None:
        ch_line = (f"❔ expecting **{_label(st['channel_expected'])}** (not re-checked — set stb_channel_check_key; "
                   f"every channel change is checked on screen)")
    else:
        ch_line = (f"{yn(det == st['channel_expected'])} on **{_label(det)}**"
                   f" (expected {_label(st['channel_expected'])})")
    vc = f"<#{_vc_id()}>"
    if not st["vc_found"]:
        acc_line = f"❔ stream VC {vc} not found (check epl_stream_voice_channel_id)"
    elif st["in_vc"]:
        acc_line = f"✅ <@{_account_id()}> is in {vc}"
    elif st["other_vc"]:
        acc_line = f"❌ <@{_account_id()}> is in the wrong VC ({st['other_vc'].mention})"
    else:
        acc_line = f"❌ <@{_account_id()}> is not in {vc}"
    return [
        f"{yn(st['awake'])} NetTV box awake",
        f"{yn(st['live_tv'])} Live TV in front (foreground: {fg})",
        ch_line,
        acc_line,
        f"{yn(st['video'] if st['in_vc'] else False)} camera/video on",
    ]


# ── repairs ─────────────────────────────────────────────────────────────────

def _may_try(name: str) -> bool:
    last = _repair_last.get(name, 0.0)
    wait = BACKOFF_SECONDS if name in _alerted else REPAIR_COOLDOWN
    return time.monotonic() - last >= wait


async def _note(channel, text: str):
    print(f"[EPL Guard] {text}")
    if channel is not None:
        try:
            await channel.send(f"🛡️ **Stream guard** › {text}",
                               allowed_mentions=discord.AllowedMentions.none())
        except Exception:
            pass


async def _record(name: str, fixed: bool, channel, what: str):
    if fixed:
        if _repair_fails.get(name) or name in _alerted:
            await _note(channel, f"✅ {what} — recovered.")
        _repair_fails[name] = 0
        _alerted.discard(name)
        return
    _repair_fails[name] = _repair_fails.get(name, 0) + 1
    if _repair_fails[name] >= MAX_FAILS_BEFORE_ALERT and name not in _alerted:
        _alerted.add(name)
        await _note(channel, f"⚠️ {what} — still failing after {_repair_fails[name]} tries. "
                             f"Retrying every {BACKOFF_SECONDS // 60} min; someone may need to check it by hand.")


async def _launch_live_tv() -> tuple[bool, str]:
    pkg = (FEATURES.get("stb_livetv_package") or "").strip()
    act = (FEATURES.get("stb_livetv_activity") or "").strip()
    if pkg and act:
        ok, out = await adb_shell("am", "start", "-n", f"{pkg}/{act}")
        if ok and "error" not in out.lower() and "exception" not in out.lower():
            return ok, out
        # that activity isn't directly launchable — fall back to the app's launcher entry
    if pkg:
        return await _send_adb_app(pkg)
    return await _send_adb_keyevent("tv")


async def run_guard_pass(channel, force: bool = False) -> list[str]:
    """One check-and-repair pass. Returns a list of actions taken."""
    actions: list[str] = []
    async with _box_lock:
        if not force and not _is_streaming():
            return actions
        retune = False

        # a) power -------------------------------------------------------
        awake = await stb_power_state()
        if awake is None:
            if _may_try("adb"):
                _repair_last["adb"] = time.monotonic()
                await _record("adb", False, channel, "Can't reach the NetTV box over ADB")
        else:
            if _repair_fails.get("adb") or "adb" in _alerted:
                await _record("adb", True, channel, "ADB connection to the NetTV box")
            if not awake and _may_try("power"):
                _repair_last["power"] = time.monotonic()
                await _note(channel, "📺 NetTV box is off/asleep — waking it…")
                await _send_adb_keyevent("wake")
                await asyncio.sleep(4)
                if await stb_power_state() is False:
                    await _send_adb_keyevent("power")
                    await asyncio.sleep(6)
                awake = await stb_power_state()
                actions.append("woke box")
                retune = True
                await _record("power", bool(awake), channel, "Waking the NetTV box")
                if awake:
                    await asyncio.sleep(6)   # let it boot back into its last app

        # b) live TV app ---------------------------------------------------
        if awake:
            live = _on_live_tv(await foreground_app())
            if live is False and _may_try("livetv"):
                _repair_last["livetv"] = time.monotonic()
                await _note(channel, "📺 NetTV isn't on Live TV — reopening it…")
                await _launch_live_tv()
                await asyncio.sleep(8)
                live = _on_live_tv(await foreground_app())
                actions.append("reopened live TV")
                retune = True
                await _record("livetv", live is not False, channel, "Reopening Live TV")

            # c) channel ---------------------------------------------------
            expected = _expected_channel()
            detected = await detect_channel()
            if detected is not None and detected != expected:
                retune = True
                await _note(channel, f"📺 Box is on {_label(detected)} but should be on "
                                     f"{_label(expected)} — switching back…")
            if retune and _may_try("channel"):
                _repair_last["channel"] = time.monotonic()
                ok, _ = await stb_tune_channel(expected)
                actions.append(f"tuned {expected}")
                await asyncio.sleep(3)
                await _record("channel", ok, channel, f"Tuning {_label(expected)}")

        # d) account in VC -------------------------------------------------
        if not force and not _is_streaming():   # match ended while we were busy
            return actions
        st = discord_state()
        if st["vc_found"] and not st["in_vc"] and _may_try("join"):
            _repair_last["join"] = time.monotonic()
            if st["other_vc"]:
                await _note(channel, f"🔌 Stream account is in the wrong VC — moving it to <#{_vc_id()}>…")
                await _send_ahk_command("disconnect", timeout=8.0)
                await asyncio.sleep(3)
            else:
                await _note(channel, f"🔌 Stream account isn't in <#{_vc_id()}> — rejoining…")
            await _send_ahk_command("join", timeout=8.0)
            await asyncio.sleep(8)
            st = discord_state()
            actions.append("rejoined VC")
            await _record("join", bool(st["in_vc"]), channel, "Rejoining the stream VC")

        # e) camera --------------------------------------------------------
        if st["vc_found"] and st["in_vc"] and not st["video"] and _may_try("camera"):
            await asyncio.sleep(3)              # double-check: the camera hotkey is a toggle
            st = discord_state()
            if st["in_vc"] and not st["video"]:
                _repair_last["camera"] = time.monotonic()
                await _note(channel, "🎥 Stream account's camera is off — turning it on…")
                await _send_ahk_command("streamstart", timeout=8.0)
                await asyncio.sleep(8)
                st = discord_state()
                actions.append("camera on")
                await _record("camera", bool(st["video"]), channel, "Turning the camera on")
        elif st["in_vc"] and st["video"] and (_repair_fails.get("camera") or "camera" in _alerted):
            await _record("camera", True, channel, "Camera")
    return actions


async def _guard_loop():
    await bot.wait_until_ready()
    print("🛡️ EPL stream guard started.")
    while not bot.is_closed():
        interval = max(20, int(FEATURES.get("epl_guard_interval_seconds", 45) or 45))
        try:
            if (FEATURES.get(FEATURE, True) and _guard_enabled and _is_streaming()
                    and time.monotonic() - _stream_started_at >= START_GRACE_SECONDS):
                await run_guard_pass(_report_channel())
        except Exception as e:
            print(f"[EPL Guard] loop error: {e}")
        await asyncio.sleep(interval)


def start_guard_loop():
    """Start the watchdog once (on_ready can fire again on reconnects)."""
    global _loop_started
    if _loop_started:
        return
    _loop_started = True
    bot.loop.create_task(_guard_loop())


# ── admin commands ──────────────────────────────────────────────────────────

@require_feature(FEATURE)
@bot.command(name="eplguard")
async def epl_guard_cmd(ctx: commands.Context, action: str = "status"):
    """Stream watchdog. (Admins only)
    `.eplguard status` — check box + Discord now (no repairs)
    `.eplguard fix`    — run a check-and-repair pass right now
    `.eplguard on|off` — turn the automatic watchdog on/off"""
    global _guard_enabled
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    action = action.lower()
    if action in ("on", "enable"):
        _guard_enabled = True
        await ctx.reply("🛡️ Stream guard **on** — it runs while the EPL stream is live.")
    elif action in ("off", "disable"):
        _guard_enabled = False
        await ctx.reply("🛡️ Stream guard **off** (until `.eplguard on` or a bot restart).")
    elif action in ("fix", "repair"):
        await ctx.reply("🛡️ Running a check-and-repair pass…")
        actions = await run_guard_pass(ctx.channel, force=True)
        await ctx.reply("✅ Nothing needed fixing." if not actions else "🛠️ Did: " + ", ".join(actions))
    else:
        async with ctx.typing():
            st = await collect_status()
        streaming = "🔴 EPL stream live" if _is_streaming() else "⚪ no EPL stream right now"
        embed = discord.Embed(title="🛡️ EPL stream guard", color=discord.Color.blurple(),
                              description="\n".join(_status_lines(st)))
        embed.add_field(name="State", value=f"{streaming} • guard {'on' if _guard_enabled else 'off'}")
        if not FEATURES.get("stb_livetv_package"):
            embed.set_footer(text="Tip: while the box is on Live TV, run .stbfocus save so the guard "
                                  "knows exactly which app is Live TV.")
        await ctx.reply(embed=embed, allowed_mentions=discord.AllowedMentions.none())


@require_feature(FEATURE)
@bot.command(name="stbfocus")
async def stb_focus_cmd(ctx: commands.Context, action: str = ""):
    """Show which app is in front on the NetTV box. `.stbfocus save` (while
    Live TV is playing) remembers that app as Live TV for the guard. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    fg = await foreground_app()
    if fg is None:
        await ctx.reply("❌ Couldn't read the foreground app (is the box awake and ADB connected?).")
        return
    pkg, act = fg
    if action.lower() == "save":
        _save_feature("stb_livetv_package", pkg)
        _save_feature("stb_livetv_activity", act)
        await ctx.reply(f"💾 Saved `{pkg}` as the Live TV app — the guard will reopen it if the box leaves Live TV.")
    else:
        saved = FEATURES.get("stb_livetv_package") or "(not set)"
        await ctx.reply(f"📺 Foreground: `{pkg}/{act}`\nSaved Live TV app: `{saved}`\n"
                        f"Put the box on Live TV and run `.stbfocus save` to set it.")


# ── channel vote ────────────────────────────────────────────────────────────

class _Vote:
    def __init__(self, guild_id, channel_id, message_id, requester_id, target, ends_at):
        self.guild_id = guild_id
        self.channel_id = channel_id
        self.message_id = message_id
        self.requester_id = requester_id
        self.target = target
        self.ends_at = ends_at
        self.done = False


_active_vote: _Vote | None = None
_vote_lock = asyncio.Lock()
_last_vote_end = 0.0


def _eligible_voters(guild: discord.Guild) -> set[int]:
    vc = guild.get_channel(_vc_id())
    if vc is None:
        return set()
    ids = set()
    for uid in vc.voice_states:
        if uid in (_account_id(), bot.user.id):
            continue
        member = guild.get_member(uid)
        if member is not None and member.bot:
            continue
        ids.add(uid)
    return ids


def _resolve_target(arg: str, current: str) -> str | None:
    nums = list(_channel_names().keys())          # ["504", "505"]
    arg = (arg or "").strip().lower()
    if not arg:
        others = [n for n in nums if n != current]
        return others[0] if others else None
    if arg in nums:
        return arg
    if arg.isdigit() and 1 <= int(arg) <= len(nums):   # "1" -> HD 1 (504), "2" -> HD 2 (505)
        return nums[int(arg) - 1]
    for num, name in _channel_names().items():
        if arg in name.lower():
            return num
    return None


def _vote_embed(vote: _Vote, yes: int, no: int, n: int, need: int, result: str | None = None,
                color=discord.Color.gold()) -> discord.Embed:
    cur = _expected_channel()
    e = discord.Embed(title="📺 Channel switch vote", color=color)
    e.description = (f"<@{vote.requester_id}> wants to switch **{_label(cur)}** → **{_label(vote.target)}**.\n"
                     f"Everyone in <#{_vc_id()}> can vote: react {YES} to accept, {NO} to reject.")
    e.add_field(name="Votes", value=f"{YES} {yes}  •  {NO} {no}  •  in VC: {n}")
    e.add_field(name="Needed", value=f"{need} {YES} (majority)")
    if result:
        e.add_field(name="Result", value=result, inline=False)
    else:
        e.add_field(name="Ends", value=f"<t:{int(vote.ends_at)}:R>", inline=False)
    return e


async def _evaluate_vote(final: bool = False):
    global _active_vote, _last_vote_end
    async with _vote_lock:
        vote = _active_vote
        if vote is None or vote.done:
            return
        guild = bot.get_guild(vote.guild_id)
        chan = bot.get_channel(vote.channel_id)
        if guild is None or chan is None:
            return
        try:
            msg = await chan.fetch_message(vote.message_id)
        except discord.HTTPException:
            vote.done, _active_vote, _last_vote_end = True, None, time.monotonic()
            return

        yes_ids, no_ids = set(), set()
        for r in msg.reactions:
            if str(r.emoji) == YES:
                yes_ids = {u.id async for u in r.users()}
            elif str(r.emoji) == NO:
                no_ids = {u.id async for u in r.users()}
        eligible = _eligible_voters(guild)
        if vote.requester_id not in no_ids:
            yes_ids.add(vote.requester_id)
        yes_ids -= no_ids                     # reacted both -> counts as no
        yes_ids &= eligible
        no_ids &= eligible
        n = len(eligible)
        need = n // 2 + 1
        y, nn = len(yes_ids), len(no_ids)

        passed = n > 0 and y >= need
        impossible = n == 0 or y + (n - y - nn) < need     # even every undecided voter can't make it
        if not (passed or impossible or final):
            await msg.edit(embed=_vote_embed(vote, y, nn, n, need),
                           allowed_mentions=discord.AllowedMentions.none())
            return

        vote.done = True
        _active_vote = None
        _last_vote_end = time.monotonic()

    if not passed:
        why = "nobody left in the VC" if n == 0 else ("time's up" if final and not impossible else "majority said no")
        await msg.edit(embed=_vote_embed(vote, y, nn, n, need, f"❌ Not switching — {why}.",
                                         discord.Color.red()),
                       allowed_mentions=discord.AllowedMentions.none())
        return

    await msg.edit(embed=_vote_embed(vote, y, nn, n, need, f"✅ Passed — switching to **{_label(vote.target)}**…",
                                     discord.Color.green()),
                   allowed_mentions=discord.AllowedMentions.none())
    async with _box_lock:
        ok, err = await stb_tune_channel(vote.target)
    result = (f"✅ Passed — now on **{_label(vote.target)}**." if ok
              else f"⚠️ Passed, but the switch failed: `{err[:150]}`")
    await msg.edit(embed=_vote_embed(vote, y, nn, n, need, result,
                                     discord.Color.green() if ok else discord.Color.orange()),
                   allowed_mentions=discord.AllowedMentions.none())


async def _vote_timer(vote: _Vote):
    await asyncio.sleep(max(0, vote.ends_at - time.time()))
    if _active_vote is vote and not vote.done:
        await _evaluate_vote(final=True)


@require_feature(FEATURE)
@bot.command(name="chvote", aliases=["channelvote", "switchchannel", "switchch"])
async def channel_vote_cmd(ctx: commands.Context, *, target: str = ""):
    """Ask the people in the stream VC to vote on switching the NetTV channel
    between Star Sports Select HD 1 (504) and Star Sports Select HD 2 (505).
    `.chvote` switches to the other one; `.chvote 1` / `.chvote 2` / `.chvote 505` pick one."""
    global _active_vote
    if ctx.guild is None:
        return
    if not (ctx.author.voice and ctx.author.voice.channel and ctx.author.voice.channel.id == _vc_id()):
        await ctx.reply(f"❌ You need to be in <#{_vc_id()}> to request a channel switch.")
        return
    if not _stream_live():
        await ctx.reply(f"❌ The stream isn't running right now (<@{_account_id()}> isn't in <#{_vc_id()}> with video on).",
                        allowed_mentions=discord.AllowedMentions.none())
        return
    if _active_vote is not None and not _active_vote.done:
        await ctx.reply("⏳ There's already a channel vote going — vote on that one.")
        return
    wait = VOTE_COOLDOWN_SECONDS - (time.monotonic() - _last_vote_end)
    if wait > 0:
        await ctx.reply(f"⏳ Give it {int(wait) + 1}s before starting another vote.")
        return
    current = _expected_channel()
    tgt = _resolve_target(target, current)
    if tgt is None:
        opts = ", ".join(f"`{i + 1}`/`{n}` = {name}" for i, (n, name) in enumerate(_channel_names().items()))
        await ctx.reply(f"❌ Unknown channel. Options: {opts}")
        return
    if tgt == current:
        await ctx.reply(f"📺 Already on **{_label(current)}**.")
        return

    duration = int(FEATURES.get("epl_channel_vote_seconds", 120) or 120)
    vote = _Vote(ctx.guild.id, ctx.channel.id, 0, ctx.author.id, tgt, time.time() + duration)
    n = len(_eligible_voters(ctx.guild))
    msg = await ctx.send(embed=_vote_embed(vote, 1, 0, n, n // 2 + 1),
                         allowed_mentions=discord.AllowedMentions.none())
    vote.message_id = msg.id
    _active_vote = vote
    try:
        await msg.add_reaction(YES)
        await msg.add_reaction(NO)
    except discord.HTTPException:
        pass
    asyncio.create_task(_vote_timer(vote))
    await _evaluate_vote()       # passes straight away if the requester is the majority (e.g. alone)


@require_feature(FEATURE)
@bot.command(name="chcurrent", aliases=["whichchannel"])
async def channel_current_cmd(ctx: commands.Context):
    """Show which NetTV channel the stream is on."""
    detected = await detect_channel(force=True)
    extra = "" if detected is None else f" (box reports {_label(detected)})"
    await ctx.reply(f"📺 Stream channel: **{_label(_expected_channel())}**{extra}")


@bot.listen("on_raw_reaction_add")
async def _vote_reaction_add(payload: discord.RawReactionActionEvent):
    vote = _active_vote
    if vote is None or payload.message_id != vote.message_id or payload.user_id == bot.user.id:
        return
    emoji = str(payload.emoji)
    if emoji not in (YES, NO):
        return
    # Keep one vote per person: drop their opposite reaction (needs Manage Messages; best effort).
    other = NO if emoji == YES else YES
    try:
        chan = bot.get_channel(payload.channel_id)
        msg = chan.get_partial_message(payload.message_id)
        await msg.remove_reaction(other, discord.Object(id=payload.user_id))
    except Exception:
        pass
    await _evaluate_vote()


@bot.listen("on_raw_reaction_remove")
async def _vote_reaction_remove(payload: discord.RawReactionActionEvent):
    vote = _active_vote
    if vote is None or payload.message_id != vote.message_id:
        return
    if str(payload.emoji) in (YES, NO):
        await _evaluate_vote()


@bot.listen("on_voice_state_update")
async def _vote_voice_update(member, before, after):
    vote = _active_vote
    if vote is None or vote.done:
        return
    vc_id = _vc_id()
    if (before.channel and before.channel.id == vc_id) != (after.channel and after.channel.id == vc_id):
        await _evaluate_vote()
