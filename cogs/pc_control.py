"""
cogs/pc_control.py — admin-only PC remote-control commands (AutoHotkey HTTP
bridge + Windows-specific automation for running/streaming a physical PC via
Discord commands).

DISABLED BY DEFAULT: gated behind the "pc_control" toggle in features.json
(default: false) since it requires a Windows host, an AutoHotkey script
running locally, and grants a lot of power to whoever can run these
commands. Every command here is additionally restricted to
`is_admin_user()` (special admin ID or server Administrator), same as the
original.

NOTE: `_run_ps()` is referenced by several commands below (debugwindows,
testinput, debugdiscord, debugedge) but was used-without-being-defined in
the original single-file bot. A straightforward PowerShell-runner
implementation has been added here so those commands don't crash with a
NameError — if you had a different implementation in mind, swap this out.
"""
import asyncio
import ctypes
import ctypes.wintypes
import subprocess

import discord
from aiohttp import web as _web
from discord.ext import commands

from bot_instance import bot
from core.features import require_feature
from core.mention_safety import neutralize_mentions
from core.permissions import is_admin_user

FEATURE = "pc_control"


def _run_ps(script: str) -> tuple[bool, str]:
    """Run a PowerShell script synchronously and return (success, output).
    (Reconstructed — see module docstring.)"""
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=30
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip()
        return result.returncode == 0, output
    except Exception as e:
        return False, str(e)


# ==================== PC CONTROL (ADMINS ONLY) ====================
# Works even when the target window is not focused or is behind other windows.
# Uses ctypes (built-in) to post key messages directly to window handles.
# Install dependencies: pip install pygetwindow pywin32
#
# Access: SPECIAL_ADMIN_ID user + any server member with Administrator permission.

import ctypes
import ctypes.wintypes

# Windows API constants
WM_KEYDOWN   = 0x0100
WM_KEYUP     = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP   = 0x0105

# Virtual key codes
VK = {
    "f5":     0x74,
    "f11":    0x7A,
    "ctrl":   0x11,
    "shift":  0x10,
    "alt":    0x12,
    "w":      0x57,
    "s":      0x53,
    "t":      0x54,
    "enter":  0x0D,
}

user32 = None
if hasattr(ctypes, "windll"):
    user32 = ctypes.windll.user32
else:
    print("⚠️  pc_control: not running on Windows — ctypes.windll unavailable. "
          "PC-control commands will report an error if invoked; this is expected "
          "on non-Windows hosts and harmless when features.json -> pc_control=false.")


def _find_hwnd(title_fragment: str) -> int | None:
    """
    Return the HWND of the first top-level window whose title contains
    `title_fragment` (case-insensitive), or None if not found.
    Does NOT require the window to be focused or visible.
    """
    if user32 is None:
        raise RuntimeError("PC control is only supported on Windows (ctypes.windll unavailable).")
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
    def enum_cb(hwnd, _):
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if title_fragment.lower() in buf.value.lower():
                found.append(hwnd)
        return True

    user32.EnumWindows(enum_cb, 0)
    return found[0] if found else None


def _post_key(hwnd: int, vk: int) -> None:
    """Post a WM_KEYDOWN + WM_KEYUP pair to a window handle."""
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, 0)
    user32.PostMessageW(hwnd, WM_KEYUP,   vk, 0)


def _post_hotkey(hwnd: int, *keys: str) -> None:
    """
    Post a combination of keys to a window handle without needing focus.
    Modifier keys (ctrl, shift, alt) are held via WM_KEYDOWN, the final
    key is sent, then modifiers are released via WM_KEYUP.
    """
    modifiers = [k for k in keys if k in ("ctrl", "shift", "alt")]
    main_keys  = [k for k in keys if k not in ("ctrl", "shift", "alt")]

    for mod in modifiers:
        user32.PostMessageW(hwnd, WM_KEYDOWN, VK[mod], 0)
    for key in main_keys:
        user32.PostMessageW(hwnd, WM_KEYDOWN, VK[key], 0)
        user32.PostMessageW(hwnd, WM_KEYUP,   VK[key], 0)
    for mod in reversed(modifiers):
        user32.PostMessageW(hwnd, WM_KEYUP, VK[mod], 0)


def _pc_admin_check(ctx: commands.Context) -> bool:
    """Return True if the invoking user is an admin (special ID or server Administrator)."""
    return is_admin_user(ctx.author)


async def _get_hwnd_or_fail(ctx: commands.Context, title: str) -> int | None:
    """Find a window handle by partial title; reply and return None if not found."""
    hwnd = await asyncio.get_event_loop().run_in_executor(None, _find_hwnd, title)
    if not hwnd:
        await ctx.reply(f"⚠️ Could not find a **{neutralize_mentions(title)}** window. Is it open?")
    return hwnd


# ── Discord stream control via AHK HTTP bridge ──────────────────────────────
# The bot runs a tiny HTTP server on localhost:9876.
# An AutoHotkey script on your laptop polls it and clicks the Screen button.
# Setup: run the .ahk file (see instructions) alongside the bot.
#
# IMPORTANT: AHK only confirms "done" once it has ACTUALLY FINISHED running the
# command (not just when it picked it up). Each command gets a unique id so the
# bot knows precisely which command's completion it's waiting for, even if a
# slow command (like clickplay, which can take up to ~30s) is still running
# when the next command is queued.

from aiohttp import web as _web

_ahk_command: str = ""          # current pending command for AHK to pick up (format: "id|cmd")
_ahk_command_id: int = 0        # increments per command sent
_ahk_done_id: str = ""          # id of the most recently completed command, set by AHK
_ahk_app: _web.Application | None = None
_ahk_runner: _web.AppRunner | None = None

AHK_PORT = 9876


async def _start_ahk_server():
    """Start the local HTTP server the AHK script polls."""
    global _ahk_app, _ahk_runner
    _ahk_app = _web.Application()
    _ahk_app.router.add_get("/command", _ahk_get_command)
    _ahk_app.router.add_post("/done", _ahk_done)
    _ahk_runner = _web.AppRunner(_ahk_app)
    await _ahk_runner.setup()
    site = _web.TCPSite(_ahk_runner, "127.0.0.1", AHK_PORT)
    await site.start()
    print(f"✅ AHK bridge listening on http://127.0.0.1:{AHK_PORT}")


async def _ahk_get_command(request: _web.Request) -> _web.Response:
    """AHK polls this — returns the pending command (with its id) and clears it.
    NOTE: clearing here only means AHK has *picked up* the command, not that
    it has finished running it. Completion is signaled separately via /done.
    """
    global _ahk_command
    cmd = _ahk_command
    _ahk_command = ""
    return _web.Response(text=cmd)


async def _ahk_done(request: _web.Request) -> _web.Response:
    """AHK calls this AFTER it has finished executing a command, passing the
    command's id back as the request body (or ?id= query param) so the bot
    knows exactly which in-flight command just completed.
    """
    global _ahk_done_id
    try:
        body = (await request.text()).strip()
    except Exception:
        body = ""
    done_id = request.query.get("id", "") or body
    if done_id:
        _ahk_done_id = done_id
    return _web.Response(text="ok")


async def _send_ahk_command(cmd: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Queue a command for AHK and wait until AHK reports it has FINISHED
    executing that exact command (matched by id) — not merely picked it up.
    `timeout` should be long enough to cover the slowest possible runtime of
    `cmd` (e.g. clickplay needs ~35s, simple keystrokes need only a few s).
    """
    global _ahk_command, _ahk_command_id, _ahk_done_id
    _ahk_command_id += 1
    this_id = str(_ahk_command_id)
    _ahk_command = f"{this_id}|{cmd}"

    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.2)
        if _ahk_done_id == this_id:
            return True, "OK"
    # Timed out — clear the pending command so a stale entry doesn't leak
    # into a future poll, but leave _ahk_done_id alone in case AHK is just
    # about to report completion late.
    if _ahk_command == f"{this_id}|{cmd}":
        _ahk_command = ""
    return False, "AHK script did not respond in time. Is the .ahk file running?"


@require_feature(FEATURE)
@bot.command(name="streamstart")
async def stream_start(ctx: commands.Context):
    """Turn the Discord camera ON via AutoHotkey bridge (Alt+S). (Admins only)
    NOTE: this used to toggle screen-share for the watchdgo/Edge setup
    (worldcup.py). For the capture-card setup (epl.py) Alt+S is bound in
    Discord to "Toggle Camera" instead, so this now turns the camera on.
    Both cogs reuse this same command name/AHK hotkey — don't run WC and
    EPL sequences in the same session unless Alt+S means the same thing
    in your Discord keybinds for both."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("streamstart")
    if ok:
        await ctx.reply("📡 **Stream/camera started!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="streamstop")
async def stream_stop(ctx: commands.Context):
    """Turn the Discord camera OFF via AutoHotkey bridge (Alt+S). (Admins only)
    See stream_start's note above — same hotkey, now means camera toggle."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("streamstop")
    if ok:
        await ctx.reply("🛑 **Stream/camera stopped!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="micmute")
async def toggle_mic_mute(ctx: commands.Context):
    """Toggle Discord mute via AutoHotkey bridge (Alt+H). (Admins only)
    Named "micmute" (not "mute") to avoid colliding with moderation.py's
    existing .mute command, which times out a member — unrelated feature.
    Not used by the auto-scheduler (the stream stays unmuted by default) —
    this is just here so you can flip it manually via a "." command
    instead of touching the PC."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("togglemute")
    if ok:
        await ctx.reply("🎤 **Mute toggled!**")
    else:
        await ctx.reply(f"❌ {msg}")


# ── Edge browser commands (via AHK bridge) ──────────────────────────────────

@require_feature(FEATURE)
@bot.command(name="refresh")
async def refresh_edge(ctx: commands.Context):
    """Refresh the active Edge tab. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("refresh")
    if ok:
        await ctx.reply("🔄 **Edge refreshed!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="openlink")
async def open_link(ctx: commands.Context, *, url: str = ""):
    """Open a URL in Edge. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    if not url:
        await ctx.reply("❌ Provide a URL. Example: `.openlink https://youtube.com`")
        return
    if not (url.startswith("http://") or url.startswith("https://")):
        await ctx.reply("❌ URL must start with `http://` or `https://`.")
        return
    ok, msg = await _send_ahk_command(f"openlink {url}")
    if ok:
        await ctx.reply(f"🌐 **Opening in Edge:** `{url}`")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="closelink")
async def close_link(ctx: commands.Context):
    """Close all other Edge tabs except the active one. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("closelink")
    if ok:
        await ctx.reply("❎ **Closed all other Edge tabs!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="fullscreen")
async def fullscreen_edge(ctx: commands.Context):
    """Toggle Edge fullscreen. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("fullscreen")
    if ok:
        await ctx.reply("🔲 **Edge fullscreen toggled!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="focusedge")
async def focus_edge(ctx: commands.Context):
    """Bring the Edge window to the foreground. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("focusedge")
    if ok:
        await ctx.reply("🪟 **Edge is now focused!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="debugwindows")
async def debug_windows(ctx: commands.Context):
    """List all msedge processes and their window titles. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    script = """
Get-Process msedge -ErrorAction SilentlyContinue |
    Where-Object { $_.MainWindowTitle -ne '' } |
    Select-Object Id, MainWindowTitle |
    ForEach-Object { "$($_.Id) | $($_.MainWindowTitle)" }
"""
    ok, out = await asyncio.get_event_loop().run_in_executor(None, _run_ps, script)
    if not out:
        await ctx.reply("⚠️ No Edge windows found with titles. Is Edge open?")
    else:
        await ctx.reply(f"🪟 **Edge windows:**\n```\n{out[:1800]}\n```")


@require_feature(FEATURE)
@bot.command(name="testinput")
async def test_input(ctx: commands.Context):
    """
    Open Notepad and type 'hello' into it to verify SendInput works.
    Watch your screen for 5 seconds after running this. (Admins only)
    """
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return

    script = """
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class TestInput {
    [StructLayout(LayoutKind.Sequential)]
    public struct KEYBDINPUT {
        public ushort wVk;
        public ushort wScan;
        public uint dwFlags;
        public uint time;
        public IntPtr dwExtraInfo;
    }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT {
        public uint type;
        public KEYBDINPUT ki;
        public long padding;
    }
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll", SetLastError=true)]
    public static extern uint SendInput(uint n, INPUT[] inputs, int size);
    public static void PressKey(ushort vk) {
        var inp = new INPUT[2];
        inp[0].type = 1; inp[0].ki.wVk = vk;
        inp[1].type = 1; inp[1].ki.wVk = vk; inp[1].ki.dwFlags = 2;
        SendInput(2, inp, System.Runtime.InteropServices.Marshal.SizeOf(typeof(INPUT)));
    }
}
"@

# Open Notepad
$np = Start-Process notepad -PassThru
Start-Sleep -Milliseconds 1500
[TestInput]::ShowWindow($np.MainWindowHandle, 9) | Out-Null
[TestInput]::SetForegroundWindow($np.MainWindowHandle) | Out-Null
Start-Sleep -Milliseconds 500

# Type H E L L O
foreach ($vk in @(0x48, 0x45, 0x4C, 0x4C, 0x4F)) {
    [TestInput]::PressKey($vk)
    Start-Sleep -Milliseconds 50
}
Write-Output "OK - check Notepad for 'hello'"
"""
    await ctx.reply("🧪 Opening Notepad and typing 'hello' — watch your screen for 5 seconds...")
    ok, out = await asyncio.get_event_loop().run_in_executor(None, _run_ps, script)
    await ctx.reply(f"Result: `{out[:300]}`")


@require_feature(FEATURE)
@bot.command(name="debugdiscord")
async def debug_discord(ctx: commands.Context):
    """Step-by-step diagnostic for Discord stream commands. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return

    script = """
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System;
using System.Runtime.InteropServices;
public class DbgFocus {
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
}
"@
Write-Output "=== Discord Process Search ==="
$procs = Get-Process discord -ErrorAction SilentlyContinue
if ($null -eq $procs) { Write-Output "NO discord process found" }
else { $procs | ForEach-Object { Write-Output "PID=$($_.Id) Handle=$($_.MainWindowHandle) Title=$($_.MainWindowTitle)" } }
$proc = $procs | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if ($null -eq $proc) { Write-Output "NO usable handle"; exit 0 }
Write-Output "=== Focusing PID $($proc.Id) ==="
$s = [DbgFocus]::ShowWindow($proc.MainWindowHandle, 9)
Write-Output "ShowWindow: $s"
Start-Sleep -Milliseconds 300
$f = [DbgFocus]::SetForegroundWindow($proc.MainWindowHandle)
Write-Output "SetForeground: $f"
Start-Sleep -Milliseconds 500
$fg = [DbgFocus]::GetForegroundWindow()
Write-Output "FG handle: $fg expected: $($proc.MainWindowHandle) match: $($fg -eq $proc.MainWindowHandle)"
Write-Output "=== Sending Alt+S ==="
[System.Windows.Forms.SendKeys]::SendWait("%(s)")
Write-Output "SendKeys done"
"""
    ok, out = await asyncio.get_event_loop().run_in_executor(None, _run_ps, script)
    out = (out or "(no output)")[:1800]
    await ctx.reply("\U0001f50d **Discord debug:**\n```\n" + out + "\n```")




@require_feature(FEATURE)
@bot.command(name="join")
async def join_vc(ctx: commands.Context):
    """Join a voice channel using your Discord keybind (Alt+J). (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("join")
    if ok:
        await ctx.reply("🎙️ **Joining VC!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="disconnect")
async def disconnect_vc(ctx: commands.Context):
    """Disconnect from voice channel using your Discord keybind (Alt+D). (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("disconnect")
    if ok:
        await ctx.reply("🔇 **Disconnected from VC!**")
    else:
        await ctx.reply(f"❌ {msg}")

@require_feature(FEATURE)
@bot.command(name="clickplay")
async def click_play(ctx: commands.Context):
    """Open watchdgo.com/en and click the /en/live_events/ Play button. (Admins only)
    Polls for up to ~30s since the site's button appears intermittently.
    Note: this force-closes and reopens Edge to guarantee a single clean
    tab, which drops any active Discord screen share — so a streamstart
    is re-fired afterward to re-share the new window, followed by
    fullscreen to maximize the view."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    await ctx.reply("🔎 **Looking for the Play button on watchdgo…** (up to 30s)")
    ok, msg = await _send_ahk_command("clickplay", timeout=55.0)
    if ok:
        await ctx.reply("✅ **Play button clicked!**")
        await asyncio.sleep(2)
        ok2, msg2 = await _send_ahk_command("streamstart", timeout=8.0)
        if ok2:
            await ctx.reply("🔁 **Re-shared the window!**")
            await asyncio.sleep(2)
            ok3, msg3 = await _send_ahk_command("fullscreen", timeout=8.0)
            if ok3:
                await ctx.reply("🔲 **Fullscreen toggled!**")
            else:
                await ctx.reply(f"❌ Failed to fullscreen: {msg3}")
        else:
            await ctx.reply(f"❌ Failed to re-share: {msg2}")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="closeedge")
async def close_edge(ctx: commands.Context):
    """Force-close the Edge browser window, then reopen a fresh blank
    window. (Admins only)
    Used at the end of a stream to clean up the old tab/state. Edge is
    reopened (without watchdgo) so it stays open between matches —
    leaving it closed too long makes Discord stop recognizing it as an
    active "game", forcing a manual re-add. This also drops any active
    Discord screen share."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("closeedge", timeout=15.0)
    if ok:
        await ctx.reply("🗑️ **Edge closed!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="resume")
async def resume_dgo(ctx: commands.Context):
    """Click the Resume button on the DGO page in Edge. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ahk_command("resume")
    if ok:
        await ctx.reply("▶️ **Resumed!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE)
@bot.command(name="debugedge")
async def debug_edge(ctx: commands.Context):
    if not _pc_admin_check(ctx):
        await ctx.reply("No permission.")
        return
    script = "Get-Process | Where-Object { $_.MainWindowTitle -ne \"\" } | Select-Object Name,Id,MainWindowTitle | ForEach-Object { $_.Name + \"|\" + $_.Id + \"|\" + $_.MainWindowTitle }"
    ok, out = await asyncio.get_event_loop().run_in_executor(None, _run_ps, script)
    out = (out or "no output")[:1800]
    reply = "Windows:" + chr(10) + "```" + chr(10) + out + chr(10) + "```"
    await ctx.reply(reply)




# Alias used by cogs/events.py
start_ahk_server = _start_ahk_server