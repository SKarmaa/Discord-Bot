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
import os
import subprocess
import time

import discord
from aiohttp import web as _web
from discord.ext import commands

from bot_instance import bot
from core.features import require_feature
from core.mention_safety import neutralize_mentions
from core.permissions import is_admin_user

try:
    import serial as _pyserial  # pip install pyserial
except ImportError:
    _pyserial = None

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


# ── NetTV set-top box power control via Arduino Uno + IR ────────────────────
# The Arduino runs ir_bridge.ino, listens on Serial for text commands, and
# fires the matching learned IR code at the box. This is a direct serial
# link (not the HTTP polling the AHK bridge uses) — the Uno is plugged
# straight into this laptop over USB.
#
# GATED SEPARATELY from "pc_control": controlled by the "stb_ir_control"
# feature toggle (default: false), since it needs its own hardware
# (Arduino Uno + IR receiver/emitter) that not every setup will have.
#
# Setup: flash ir_capture.ino once to learn your remote's codes, paste
# them into ir_bridge.ino, flash that permanently, then set
# ARDUINO_IR_PORT=COM<n> in your .env to match the Uno's COM port
# (Device Manager -> Ports (COM & LPT) will show it).

FEATURE_STB = "stb_ir_control"
ARDUINO_IR_PORT = os.getenv("ARDUINO_IR_PORT", "COM5")
ARDUINO_IR_BAUD = 115200

_arduino_ser = None   # serial.Serial instance, opened once at startup


def start_arduino_bridge():
    """Open the serial connection to the Arduino. Called once from
    cogs/events.py's on_ready if features.json -> stb_ir_control = true.
    Safe to call even if the Arduino isn't plugged in yet — commands will
    just report a clear connection error instead of the bot crashing."""
    global _arduino_ser
    if _pyserial is None:
        print("⚠️  stb_ir_control: pyserial not installed (`pip install pyserial`) — Arduino IR bridge disabled.")
        return
    try:
        _arduino_ser = _pyserial.Serial(ARDUINO_IR_PORT, ARDUINO_IR_BAUD, timeout=3)
        time.sleep(2)  # the Uno resets when the serial port opens — give the sketch time to boot
        print(f"✅ Arduino IR bridge connected on {ARDUINO_IR_PORT}")
    except Exception as e:
        print(f"⚠️  Could not open Arduino IR bridge on {ARDUINO_IR_PORT}: {e}. "
              f"Check the port in Device Manager and set ARDUINO_IR_PORT in .env if it's different.")
        _arduino_ser = None


def _send_ir_command_sync(cmd: str) -> tuple[bool, str]:
    if _arduino_ser is None:
        return False, f"Arduino IR bridge not connected (port {ARDUINO_IR_PORT}). Is the Uno plugged in?"
    try:
        _arduino_ser.reset_input_buffer()
        _arduino_ser.write((cmd.strip() + "\n").encode("utf-8"))
        line = _arduino_ser.readline().decode("utf-8", errors="replace").strip()
        if not line:
            return False, "No response from Arduino (timed out) — check it's running ir_bridge.ino."
        if line.startswith("OK"):
            return True, line
        return False, line
    except Exception as e:
        return False, str(e)


async def _send_ir_command(cmd: str) -> tuple[bool, str]:
    """Send one command (PING / POWER_ON / POWER_OFF / POWER_TOGGLE) to the
    Arduino and wait for its reply. Runs the blocking pyserial call in an
    executor so it doesn't block the bot's event loop."""
    return await asyncio.get_event_loop().run_in_executor(None, _send_ir_command_sync, cmd)


@require_feature(FEATURE_STB)
@bot.command(name="stbon")
async def stb_on(ctx: commands.Context):
    """Turn the NetTV box on via IR (Arduino bridge). (Admins only)
    Falls back to the toggle code if your remote doesn't have a distinct
    Power On button — see ir_bridge.ino."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ir_command("POWER_ON")
    if not ok and "NOT_CONFIGURED" in msg:
        ok, msg = await _send_ir_command("POWER_TOGGLE")
        if ok:
            await ctx.reply("📺 **Power toggled** (no dedicated Power On code configured — used the toggle instead, so double-check it actually turned ON, not off).")
            return
    if ok:
        await ctx.reply("📺 **NetTV box powered on!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE_STB)
@bot.command(name="stboff")
async def stb_off(ctx: commands.Context):
    """Turn the NetTV box off via IR (Arduino bridge). (Admins only)
    Falls back to the toggle code if your remote doesn't have a distinct
    Power Off button — see ir_bridge.ino."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ir_command("POWER_OFF")
    if not ok and "NOT_CONFIGURED" in msg:
        ok, msg = await _send_ir_command("POWER_TOGGLE")
        if ok:
            await ctx.reply("📺 **Power toggled** (no dedicated Power Off code configured — used the toggle instead, so double-check it actually turned OFF, not on).")
            return
    if ok:
        await ctx.reply("📺 **NetTV box powered off!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE_STB)
@bot.command(name="stbtoggle")
async def stb_toggle(ctx: commands.Context):
    """Toggle the NetTV box's power directly via IR (Arduino bridge). (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ir_command("POWER_TOGGLE")
    if ok:
        await ctx.reply("📺 **Power toggled!**")
    else:
        await ctx.reply(f"❌ {msg}")


@require_feature(FEATURE_STB)
@bot.command(name="stbtest")
async def stb_test(ctx: commands.Context):
    """Diagnose the Arduino IR bridge connection. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_ir_command("PING")
    if ok:
        await ctx.reply(f"✅ Arduino IR bridge is alive on `{ARDUINO_IR_PORT}`: `{msg}`\n"
                         f"Try `.stbon` / `.stboff` / `.stbtoggle` to test the actual IR send.")
    else:
        await ctx.reply(f"❌ {msg}")


# ── NetTV/Streamz box control via ADB (wifi) ────────────────────────────────
# Some Android-TV-based set-top boxes (e.g. the newer Streamz+ NetTV units)
# expose a real Developer Options menu with USB/network debugging, unlike
# the earlier NetTV box which had none at all. Where available, this is
# strictly better than the Arduino+IR route above: full remote control over
# wifi, no extra hardware — and unlike an IR remote's single toggle button,
# Android exposes DISTINCT wake/sleep keyevents, so .stbadbon/.stbadboff are
# not ambiguous the way a toggle-only IR power button can be.
#
# GATED SEPARATELY: "stb_adb_control" feature toggle (default: false).
#
# One-time setup on the box: Settings -> Device Preferences -> About ->
# tap the build number ~7 times to unlock Developer Options, then enable
# "USB debugging" (and "Network debugging" / "Wireless debugging" if a
# separate option is shown). From any machine on the same wifi, run
# `adb connect <box-ip>:5555` once and accept the "Allow debugging?"
# prompt on the TV with the remote — after that it reconnects automatically.
# Set STB_ADB_HOST=<box-ip> (and STB_ADB_PORT if not 5555) in your .env.
# `adb` (Android platform-tools) must be installed and on PATH on whatever
# machine actually runs the bot, since this shells out to the real binary
# rather than talking the ADB protocol directly.

FEATURE_ADB = "stb_adb_control"
ADB_HOST = os.getenv("STB_ADB_HOST", "")
ADB_PORT = os.getenv("STB_ADB_PORT", "5555")
ADB_TARGET = f"{ADB_HOST}:{ADB_PORT}" if ADB_HOST else ""

# Keyevent names the simple nav/volume commands below send. Wake/sleep are
# the two that matter most for the auto-scheduler — real distinct Android
# keyevents, not a single ambiguous toggle like the IR remote's power button.
_ADB_KEYEVENTS = {
    "home": "KEYCODE_HOME",
    "back": "KEYCODE_BACK",
    "ok": "KEYCODE_DPAD_CENTER",
    "up": "KEYCODE_DPAD_UP",
    "down": "KEYCODE_DPAD_DOWN",
    "left": "KEYCODE_DPAD_LEFT",
    "right": "KEYCODE_DPAD_RIGHT",
    "volup": "KEYCODE_VOLUME_UP",
    "voldown": "KEYCODE_VOLUME_DOWN",
    "mute": "KEYCODE_VOLUME_MUTE",
    "play": "KEYCODE_MEDIA_PLAY_PAUSE",
    "wake": "KEYCODE_WAKEUP",
    "sleep": "KEYCODE_SLEEP",
    "tv": "KEYCODE_TV",
    "power": "KEYCODE_POWER",
}

# Default channel number for the full start sequence (".ststart" / the EPL
# auto-scheduler). Override with STB_DEFAULT_CHANNEL in .env.
STB_DEFAULT_CHANNEL = os.getenv("STB_DEFAULT_CHANNEL", "48")


def connect_adb() -> tuple[bool, str]:
    """Run `adb connect <host:port>` once. Called from cogs/events.py's
    on_ready if features.json -> stb_adb_control = true. Safe to call even
    if the box is offline or `adb` isn't installed — just reports a clear
    error rather than crashing the bot."""
    if not ADB_HOST:
        return False, "STB_ADB_HOST not set in .env."
    try:
        result = subprocess.run(
            ["adb", "connect", ADB_TARGET],
            capture_output=True, text=True, timeout=10,
        )
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        ok = "connected" in out.lower() and "unable" not in out.lower() and "refused" not in out.lower()
        return ok, out
    except FileNotFoundError:
        return False, "`adb` not found on PATH. Install Android platform-tools on this machine."
    except Exception as e:
        return False, str(e)


def _run_adb_sync(args: list[str]) -> tuple[bool, str]:
    if not ADB_HOST:
        return False, "STB_ADB_HOST not set in .env."
    try:
        def _once():
            return subprocess.run(
                ["adb", "-s", ADB_TARGET] + args,
                capture_output=True, text=True, timeout=10,
            )
        result = _once()
        out = ((result.stdout or "") + (result.stderr or "")).strip()
        stale = result.returncode != 0 or any(
            s in out.lower() for s in ("device offline", "device not found", "no devices")
        )
        if stale:
            # The box may have rebooted or the adb server lost the
            # connection since last use — reconnect once and retry before
            # giving up, rather than surfacing a flaky transient error.
            connect_adb()
            result = _once()
            out = ((result.stdout or "") + (result.stderr or "")).strip()
        return result.returncode == 0, out
    except FileNotFoundError:
        return False, "`adb` not found on PATH. Install Android platform-tools on this machine."
    except Exception as e:
        return False, str(e)


async def _send_adb_keyevent(name: str) -> tuple[bool, str]:
    """Send a named keyevent (see _ADB_KEYEVENTS) to the box. Runs the
    blocking subprocess call in an executor so it doesn't block the bot's
    event loop."""
    code = _ADB_KEYEVENTS.get(name)
    if not code:
        return False, f"Unknown keyevent name: {name}"
    return await asyncio.get_event_loop().run_in_executor(
        None, _run_adb_sync, ["shell", "input", "keyevent", code]
    )


async def _send_adb_app(package: str) -> tuple[bool, str]:
    """Launch an app by package name. Uses `monkey -c LAUNCHER` instead of
    `am start -n`, since monkey only needs the package name — it finds the
    launcher activity itself, so you don't need to know the exact
    Activity class to launch something."""
    return await asyncio.get_event_loop().run_in_executor(
        None, _run_adb_sync,
        ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"],
    )


@require_feature(FEATURE_ADB)
@bot.command(name="stbconnect")
async def stb_connect_cmd(ctx: commands.Context):
    """(Re)connect ADB to the Streamz/NetTV box. Run this if the box dropped
    its ADB connection (reboot, wifi hiccup, etc.) before using any other
    .stb* command. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    if not ADB_HOST:
        await ctx.reply("❌ `STB_ADB_HOST` not set in `.env`. Add `STB_ADB_HOST=<box-ip>` (and restart the bot) and try again.")
        return
    ok, out = await asyncio.get_event_loop().run_in_executor(None, connect_adb)
    emoji = "✅" if ok else "❌"
    await ctx.reply(f"{emoji} `adb connect {ADB_TARGET}` → `{out[:300] or '(no output)'}`")


@require_feature(FEATURE_ADB)
@bot.command(name="stbraw")
async def stb_raw(ctx: commands.Context, *, keys: str = ""):
    """Send raw keyevent code(s) straight to `adb shell input keyevent`,
    exactly as typed. (Admins only)
    Example: `.stbraw KEYCODE_HOME` or `.stbraw KEYCODE_4 KEYCODE_9`"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    keys = keys.strip()
    if not keys:
        await ctx.reply("❌ Provide one or more keyevent codes. Example: `.stbraw KEYCODE_HOME`")
        return
    args = keys.split()
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, _run_adb_sync, ["shell", "input", "keyevent"] + args
    )
    await ctx.reply(f"📺 **Sent:** `adb shell input keyevent {keys}`" if ok else f"❌ {msg}")


@require_feature(FEATURE_ADB)
@bot.command(name="stbadbtest")
async def stb_adb_test(ctx: commands.Context):
    """Diagnose the ADB connection to the Streamz/NetTV box. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    if not ADB_HOST:
        await ctx.reply("❌ `STB_ADB_HOST` not set in `.env`. Add `STB_ADB_HOST=<box-ip>` (and restart the bot) and try again.")
        return
    ok, out = await asyncio.get_event_loop().run_in_executor(None, connect_adb)
    emoji = "✅" if ok else "❌"
    await ctx.reply(f"{emoji} `adb connect {ADB_TARGET}` → `{out[:300] or '(no output)'}`\n"
                     f"Try `.stbadbon` / `.stbhome` to test an actual command.")


@require_feature(FEATURE_ADB)
@bot.command(name="stbadbon")
async def stb_adb_on(ctx: commands.Context):
    """Wake the Streamz/NetTV box via ADB (KEYCODE_WAKEUP) — a real distinct
    command, not a toggle. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_adb_keyevent("wake")
    await ctx.reply("📺 **Box woken up!**" if ok else f"❌ {msg}")


@require_feature(FEATURE_ADB)
@bot.command(name="stbadboff")
async def stb_adb_off(ctx: commands.Context):
    """Put the Streamz/NetTV box to sleep via ADB (KEYCODE_SLEEP) — a real
    distinct command, not a toggle. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, msg = await _send_adb_keyevent("sleep")
    await ctx.reply("📺 **Box put to sleep!**" if ok else f"❌ {msg}")


@require_feature(FEATURE_ADB)
@bot.command(name="stbapp")
async def stb_app(ctx: commands.Context, *, package: str = ""):
    """Launch an app on the box by package name via ADB. (Admins only)
    Example: `.stbapp com.google.android.youtube.tv`
    Run `.stbapps` first to find the right package name."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    package = package.strip()
    if not package:
        await ctx.reply("❌ Provide a package name. Example: `.stbapp com.google.android.youtube.tv`\n"
                         "Use `.stbapps` to list installed apps.")
        return
    ok, msg = await _send_adb_app(package)
    await ctx.reply(f"📺 **Launched `{package}`!**" if ok else f"❌ {msg}")


@require_feature(FEATURE_ADB)
@bot.command(name="stbapps")
async def stb_apps(ctx: commands.Context):
    """List installed app package names on the box via ADB. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    ok, out = await asyncio.get_event_loop().run_in_executor(
        None, _run_adb_sync, ["shell", "pm", "list", "packages"]
    )
    if not ok:
        await ctx.reply(f"❌ {out}")
        return
    packages = out.replace("package:", "").strip()
    if len(packages) > 1800:
        packages = packages[:1800] + "\n… (truncated)"
    await ctx.reply(f"📦 **Installed packages:**\n```\n{packages}\n```")


# Simple one-keyevent nav/volume commands, generated from a table instead of
# eleven near-identical function bodies. Each closure captures its own
# cmd/key/desc via default-arg binding (avoids the late-binding-in-a-loop
# footgun), so this is equivalent to writing eleven separate @bot.command
# functions by hand.
_SIMPLE_ADB_COMMANDS = {
    "stbhome":    ("home",    "Home screen"),
    "stbback":    ("back",    "Back"),
    "stbok":      ("ok",      "OK / select"),
    "stbup":      ("up",      "D-pad up"),
    "stbdown":    ("down",    "D-pad down"),
    "stbleft":    ("left",    "D-pad left"),
    "stbright":   ("right",   "D-pad right"),
    "stbvolup":   ("volup",   "Volume up"),
    "stbvoldown": ("voldown", "Volume down"),
    "stbmute":    ("mute",    "Mute toggle"),
    "stbplay":    ("play",    "Play/pause"),
    "stbtv":      ("tv",      "Live TV"),
    "stbsleep":   ("sleep",   "Sleep"),
    "stbpower":   ("power",   "Power"),
}


def _register_simple_adb_command(cmd_name: str, key_name: str, desc: str):
    @require_feature(FEATURE_ADB)
    @bot.command(name=cmd_name, help=f"{desc} on the Streamz/NetTV box via ADB. (Admins only)")
    async def _cmd(ctx: commands.Context, _key_name=key_name, _desc=desc):
        if not _pc_admin_check(ctx):
            await ctx.reply("❌ You need Administrator permission to use this command.")
            return
        ok, msg = await _send_adb_keyevent(_key_name)
        await ctx.reply(f"📺 **{_desc} sent!**" if ok else f"❌ {msg}")
    return _cmd


for _cmd_name, (_key_name, _desc) in _SIMPLE_ADB_COMMANDS.items():
    _register_simple_adb_command(_cmd_name, _key_name, _desc)


@require_feature(FEATURE_ADB)
@bot.command(name="stbchannel")
async def stb_channel(ctx: commands.Context, *, number: str = ""):
    """Switch to a channel number via ADB, like typing it on the remote —
    sends each digit as its own KEYCODE_<digit> keyevent in order.
    (Admins only)
    Example: `.stbchannel 49` presses 4 then 9."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    number = number.strip()
    if not number.isdigit():
        await ctx.reply("❌ Provide a channel number, digits only. Example: `.stbchannel 49`")
        return
    codes = [f"KEYCODE_{d}" for d in number]
    ok, msg = await asyncio.get_event_loop().run_in_executor(
        None, _run_adb_sync, ["shell", "input", "keyevent"] + codes
    )
    await ctx.reply(f"📺 **Switched to channel {number}!**" if ok else f"❌ {msg}")


# ── Full match start/end sequences ──────────────────────────────────────────
# Combine the ADB (box) and AHK (Discord) steps into the two sequences used
# both by the manual .ststart/.stend commands below and by cogs/epl.py's
# auto-scheduler, so there's exactly one place that defines "what a match
# start/end actually does."
#
# Start: power the box on -> switch to the given channel -> join the VC ->
#        turn the camera on.
# End:   put the box to sleep -> disconnect from the VC. (No camera-off step
#        — by design, per the shutdown sequence requested: sleep + disconnect
#        only.)

async def stb_full_start_sequence(channel_number: str | None = None) -> list[tuple[str, bool, str]]:
    """Run the full match-start sequence. Returns a list of
    (step_label, ok, message) tuples in the order each step ran, so callers
    can report every step (not just the first failure)."""
    results: list[tuple[str, bool, str]] = []

    ok, msg = await _send_adb_keyevent("power")
    results.append(("stbpower", ok, msg))
    await asyncio.sleep(2)

    ch = (channel_number or STB_DEFAULT_CHANNEL).strip()
    if ch.isdigit():
        codes = [f"KEYCODE_{d}" for d in ch]
        ok, msg = await asyncio.get_event_loop().run_in_executor(
            None, _run_adb_sync, ["shell", "input", "keyevent"] + codes
        )
        results.append((f"stbchannel {ch}", ok, msg))
        await asyncio.sleep(2)

    ok, msg = await _send_ahk_command("join", timeout=8.0)
    results.append(("join", ok, msg))
    await asyncio.sleep(3)

    ok, msg = await _send_ahk_command("streamstart", timeout=8.0)
    results.append(("streamstart", ok, msg))

    return results


async def stb_full_end_sequence() -> list[tuple[str, bool, str]]:
    """Run the full match-end sequence: sleep the box, disconnect from the VC."""
    results: list[tuple[str, bool, str]] = []

    ok, msg = await _send_adb_keyevent("sleep")
    results.append(("stbsleep", ok, msg))
    await asyncio.sleep(2)

    ok, msg = await _send_ahk_command("disconnect", timeout=8.0)
    results.append(("disconnect", ok, msg))

    return results


@require_feature(FEATURE_ADB)
@bot.command(name="ststart")
async def st_start(ctx: commands.Context, *, channel_number: str = ""):
    """Run the full match-start sequence manually: power on -> switch
    channel (default from STB_DEFAULT_CHANNEL, currently """ + STB_DEFAULT_CHANNEL + """) -> join VC -> camera on. (Admins only)
    Example: `.ststart` (uses default channel) or `.ststart 48` (specific channel)."""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    await ctx.reply("▶️ **Running full start sequence…**")
    results = await stb_full_start_sequence(channel_number.strip() or None)
    failed = [f"`{label}`: {msg}" for label, ok, msg in results if not ok]
    if failed:
        await ctx.reply("⚠️ **Start sequence finished with issues:**\n" + "\n".join(failed))
    else:
        await ctx.reply("✅ **Start sequence done!**")


@require_feature(FEATURE_ADB)
@bot.command(name="stend")
async def st_end(ctx: commands.Context):
    """Run the full match-end sequence manually: sleep box -> disconnect
    from VC. (Admins only)"""
    if not _pc_admin_check(ctx):
        await ctx.reply("❌ You need Administrator permission to use this command.")
        return
    await ctx.reply("▶️ **Running full end sequence…**")
    results = await stb_full_end_sequence()
    failed = [f"`{label}`: {msg}" for label, ok, msg in results if not ok]
    if failed:
        await ctx.reply("⚠️ **End sequence finished with issues:**\n" + "\n".join(failed))
    else:
        await ctx.reply("✅ **End sequence done!**")