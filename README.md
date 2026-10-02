# KP Oli Bot — modular structure

This is your original single-file `main.py` split into modules by task, plus
a `features.json` toggle system and a hardened @everyone/@here safety net.
No functionality was removed — every slash command and prefix command from
the original file is still here (verified 1:1 against the original command
list).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in your TOKEN and GEMINI_API_KEY
python main.py
```

`bot_data.json` (witty responses, welcome messages, channel IDs) and
`features.json` (toggles) are both auto-created with sensible defaults on
first run if they don't already exist, same as before.

## `.env` reference

```env
# Discord bot token (either name works — config.py checks TOKEN first, then DISCORD_TOKEN)
TOKEN=your_discord_bot_token

# AI chat (Gemini)
GEMINI_API_KEY=your_gemini_api_key

# World Cup / EPL live match data (football-data.org — free tier, same key for both)
FOOTBALL_DATA_API_KEY=your_footballdata_api_key

# Arduino Uno + IR bridge (stb_ir_control) — only needed if you're using that
# box/hardware. Check Device Manager > Ports (COM & LPT) for the right COM port.
ARDUINO_IR_PORT=COM5

# ADB control for an Android-TV set-top box, e.g. a Streamz/NetTV box with
# Developer Options (stb_adb_control) — the box's IP on your wifi.
STB_ADB_HOST=192.168.x.x
STB_ADB_PORT=5555

# Default channel number the .ststart full sequence / EPL auto-scheduler
# switches to when powering on. Optional — defaults to "48" in code even if
# left out of .env entirely.
STB_DEFAULT_CHANNEL=48
```

Required: `TOKEN`, `GEMINI_API_KEY`. Everything else is only needed if the
matching feature toggle (below) is turned on.

`adb` (Android platform-tools) and AutoHotkey both need to be installed and
reachable on whatever machine actually runs the bot — see the STB/EPL
section below for details.

## Folder layout

```
main.py                 entry point — imports every cog, then bot.run()
bot_instance.py          creates the shared `bot` object (see "Ping safety" below)
config.py                loads .env, bot_data.json, features.json
features.json            NEW — turn whole feature areas on/off
bot_data.json            unchanged format: witty_responses, welcome_messages, bot_config

core/
  permissions.py          is_admin_user()
  mention_safety.py        the @everyone/@here safety net (read this first)
  ai_client.py              Gemini API client + per-user rate limiter
  nepali_calendar.py         Bikram Sambat festival data/helpers
  state.py                    shared in-memory state (snipe cache, AFK, giveaways, confessions)
  features.py                  @require_feature("name") decorator

cogs/
  events.py                on_ready / on_member_join / on_message / AI trigger / AI-driven mod commands
  ai_commands.py             /ai, /aistatus
  moderation.py               kick/ban/unban/mute/unmute/lock/unlock/purge/slowmode/massmove
  fun_games.py                  poll, 8ball, coinflip, trivia, wyr, truth/dare, rps
  utility.py                    define, weather, calendar, userinfo, serverinfo, roleinfo,
                                 avatar, snipe, date, ping, remind, afk
  confession.py                  /confess
  giveaway.py                     /giveaway and friends
  admin_broadcast.py               /kpwrite, /kpannounce, /reload, .words, .reload-data
  pc_control.py                     AutoHotkey bridge + NetTV/STB remote control (IR + ADB)
  worldcup.py                        World Cup 2026 auto-stream scheduler + live scores (browser/screen-share setup)
  epl.py                              Premier League auto-stream scheduler + live scores (capture-card/webcam setup)
```

## Feature toggles (`features.json`)

Every major area can be switched off without touching code:

| Key | Default | What it gates |
|---|---|---|
| `ai_chat` | true | `/ai`, `/aistatus`, the "oh kp baa" trigger, and @mentioning the bot |
| `ai_moderation_commands` | true | natural-language kick/ban/mute via the AI trigger |
| `moderation` | true | kick/ban/mute/unmute/lock/unlock/purge/slowmode/massmove |
| `fun_games` | true | poll/8ball/coinflip/trivia/wyr/truth/dare/rps |
| `utility` | true | define/weather/calendar/userinfo/serverinfo/roleinfo/avatar/snipe/date/ping |
| `reminders` | true | `/remind` |
| `afk_system` | true | `/afk` + the AFK listener |
| `confessions` | true | `/confess` |
| `giveaways` | true | `/giveaway` and friends |
| `admin_broadcast` | true | `/kpwrite`, `/kpannounce` |
| `deploy` | **false** | `/update`, `.update` — `git pull` + self-restart (see below) |
| `pc_control` | **false** | AHK bridge / Discord join-mute-disconnect / Edge-browser commands (Windows-only, needs a local AHK script) |
| `worldcup_tracker` | **false** | World Cup auto-stream scheduler + live scores — browser/screen-share setup (needs `FOOTBALL_DATA_API_KEY`) |
| `epl_tracker` | **false** | Premier League auto-stream scheduler + live scores — capture-card/webcam setup (needs `FOOTBALL_DATA_API_KEY`) |
| `epl_target_channel_id` | 0 | channel the EPL scheduler posts to; `0`/unset falls back to `target_channel_id` |
| `stb_ir_control` | **false** | Arduino Uno + IR bridge for NetTV box power (`.stbon`/`.stboff`/`.stbtoggle`/`.stbtest`) |
| `stb_adb_control` | **false** | ADB-over-wifi control for an Android-TV STB (`.stb*` ADB commands below) — preferred over IR when the box supports it |
| `epl_control_stb_power` | **false** | if true, the EPL auto-scheduler's start/end sequences also power the STB on/off (ADB preferred over IR if both are enabled) |
| `welcome_messages` | true | posting a welcome message on member join |
| `trigger_word_responses` | true | the 30%-chance witty-word replies |
| `random_reactions` | true | the 1%-chance random emoji reactions |

Plus a few tunables: `ai_trigger_phrase`, `ai_cooldown_minutes`,
`special_admin_id`, `target_channel_id`, `command_prefix`.

Edit `features.json` and either restart the bot or run `/reload` /
`.reload-data` (admin only) — both now reload `features.json` as well as
`bot_data.json`.

If a toggled-off command is used anyway, the bot replies with a short
"this feature is currently turned off" message instead of silently failing.

## NetTV / set-top-box control (`cogs/pc_control.py`)

Two independent ways to control the physical NetTV/Streamz box, plus the AHK
bridge that drives Discord itself (join VC, camera, mute, disconnect). Pick
whichever matches your box — **ADB is strictly better where available**
(real distinct keyevents, no extra hardware, works over wifi), so use IR only
on an older box with no Developer Options menu.

### ADB control (`stb_adb_control`) — preferred

One-time setup on the box: enable Developer Options (tap the build number
~7 times under Settings → Device Preferences → About) → enable USB/Network
debugging. From any machine on the same wifi, `adb connect <box-ip>:5555`
once and accept the "Allow debugging?" prompt on the TV with the remote —
after that it reconnects automatically. `adb` (Android platform-tools) must
be installed and on **PATH** on the machine actually running the bot (it
shells out to the real `adb.exe`/`adb` binary) — see "Installing adb" below
if you hit `'adb' is not recognized`.

| Command | Does |
|---|---|
| `.stbconnect` | Re-run `adb connect` to the box (use after a reboot/wifi drop) |
| `.stbadbtest` | Diagnose the ADB connection |
| `.stbadbon` / `.stbadboff` | `KEYCODE_WAKEUP` / `KEYCODE_SLEEP` — real distinct commands, not a toggle |
| `.stbpower` | `KEYCODE_POWER` |
| `.stbsleep` | `KEYCODE_SLEEP` |
| `.stbtv` | `KEYCODE_TV` — switch to Live TV |
| `.stbchannel <number>` | Types the digits like the remote, e.g. `.stbchannel 49` → presses 4 then 9 |
| `.stbhome` / `.stbback` / `.stbok` | Home / Back / D-pad select |
| `.stbup` / `.stbdown` / `.stbleft` / `.stbright` | D-pad navigation |
| `.stbvolup` / `.stbvoldown` / `.stbmute` | Volume |
| `.stbplay` | Play/pause |
| `.stbraw <keycodes>` | Sends exactly what you type to `adb shell input keyevent`, e.g. `.stbraw KEYCODE_HOME KEYCODE_BACK` |
| `.stbapp <package>` | Launch an app by package name |
| `.stbapps` | List installed app package names |

**Full combined sequences** (power + channel + Discord, all in one):

| Command | Does |
|---|---|
| `.ststart [channel]` | Power on → switch channel (default `STB_DEFAULT_CHANNEL`, `48`) → join VC → camera on |
| `.stend` | Sleep the box → disconnect from VC |

Both reply once at the start ("Running full start/end sequence…") and once
at the end ("done!") rather than narrating every step — a step's failure is
still called out individually if one happens.

#### Installing `adb` (if you get `'adb' is not recognized`)

1. Download "SDK Platform-Tools for Windows" from
   https://developer.android.com/tools/releases/platform-tools (just the
   zip — no need for full Android Studio).
2. Extract to a permanent folder, e.g. `C:\platform-tools`.
3. Add that folder to your **PATH** (Win → search "Environment Variables" →
   Edit the system environment variables → Environment Variables → select
   `Path` → Edit → New → paste the folder → OK everywhere).
4. Open a **new** terminal (PATH changes don't apply to already-open ones)
   and confirm with `adb version`.
5. Restart the bot so it picks up the same PATH.

### IR control (`stb_ir_control`) — fallback for boxes with no Developer Options

An Arduino Uno + IR transmitter/receiver module, flashed with
`ir_bridge.ino`, talks to the bot over USB serial (`ARDUINO_IR_PORT`,
115200 baud). `ir_capture.ino` is a one-time-use sketch to learn your
remote's IR codes — flash it, capture the codes, paste them into
`ir_bridge.ino`, then flash that permanently.

| Command | Does |
|---|---|
| `.stbtest` | Diagnose the Arduino serial connection (`PING`) |
| `.stbon` / `.stboff` | Power on/off (falls back to `.stbtoggle` if no dedicated code is configured) |
| `.stbtoggle` | Toggle power directly |

### AHK bridge (`pc_control`) — Discord-side control

Runs a tiny local HTTP server (`localhost:9876`) that `bot_desktop_bridge.ahk`
polls, so Discord's own UI (join VC, camera, mute, disconnect, plus the
World-Cup browser/Edge commands) can be driven by `.` commands. Set it to
run on Windows startup so it's always there after a reboot.

| Command | Does |
|---|---|
| `.join` / `.disconnect` | Join/leave the voice channel (Alt+J / Alt+G) |
| `.streamstart` / `.streamstop` | Camera on/off for the capture-card setup (Alt+S) — toggles screen-share instead for the World Cup/browser setup |
| `.micmute` | Toggle Discord mute (Alt+H) — named "micmute", not "mute", to avoid colliding with moderation's `.mute` (timeout) command |
| `.refresh` / `.openlink` / `.closelink` / `.fullscreen` / `.focusedge` / `.clickplay` / `.closeedge` / `.resume` | World Cup/Edge-browser helper commands |
| `.debugwindows` / `.testinput` / `.debugdiscord` / `.debugedge` | Diagnostics |

## Auto-stream schedulers (`worldcup.py` / `epl.py`)

Both poll football-data.org (free tier, `FOOTBALL_DATA_API_KEY`, shared
between the two) and automatically run a start sequence ~5 min before
kickoff and an end sequence after the match finishes. `worldcup.py` drives
a browser (Edge + watchdgo.com) for screen-share; `epl.py` instead assumes a
USB capture card that Discord sees as a webcam, so its sequence is just
"power → channel → join → camera on" / "sleep → disconnect" (via the ADB
full sequences above when `epl_control_stb_power` + `stb_adb_control` are
both on, otherwise falls back to the plain join/camera AHK commands, with
IR power if `stb_ir_control` is on instead).

**Polling, not push** — football-data.org has no webhooks. The scheduler
loop checks every **30 seconds**, but the underlying match-list fetch is
cached: **10 minutes** between real API calls when nothing is close to
kickoff, tightening to **60 seconds** once a match is live or within 2
hours of kickoff. The separate live-score-embed loop updates every **60
seconds**. This is comfortably inside the 4.5–5.5 min pre-kickoff firing
window and the 5-min post-FINISHED delay, so nothing gets missed.

**Concurrent matches are handled correctly** — `epl.py` tracks which match
IDs are currently "holding the stream open" (`_epl_active_matches`). If a
second match's pre-window fires while the first is still live, it's added
to that set WITHOUT re-running the start sequence (join/camera-on don't
fire twice). The end sequence only runs once that set is empty — i.e. once
every match that triggered a start has also finished — so one match ending
early doesn't cut the stream while another is still live.

EPL commands: `.eplenable` / `.epldisable` / `.eplstatus` / `.epltest` /
`.eplreset` / `.eplscores` / `.eplscore [team]` / `.eplgo` (manual start) /
`.eplend` (manual end, force-clears the active-match tracking too).

## Feature toggles quick-reference for the STB/EPL stack

To run the full ADB-powered EPL auto-stream setup:

```json
"epl_tracker": true,
"stb_adb_control": true,
"epl_control_stb_power": true,
"pc_control": true
```

...plus `FOOTBALL_DATA_API_KEY`, `STB_ADB_HOST`, and `adb` + the AHK script
both running on the host machine.

## Feature toggles (`features.json`) — safety & deploy

### The @everyone / @here safety net

This was the other big ask, so it's worth explaining clearly
(`core/mention_safety.py` has the same explanation in code comments).

**The structural fix — `bot_instance.py`:**

```python
bot = commands.Bot(
    ...,
    allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True, replied_user=True),
)
```

This is a bot-wide *default*. Discord itself refuses to resolve
@everyone/@here/role pings on **any** message the bot sends, no matter what
text ends up in that message — a `.mute` reason, a `/giveaway` prize, an AI
reply, a `/remind` body, an `/afk` reason, a `.kpwrite` broadcast, anything.
It doesn't matter whether the text was sanitized or not; Discord just won't
ping. This is much stronger than the original code's approach, which only
scrubbed `@everyone`/`@here` out of **AI responses** — every other command
that echoed free user text back in a plain message (the AI-driven
`kick/ban/mute` reason, `/remind`, `/afk`, giveaway congratulations) could
previously leak a real @everyone ping if someone typed one into a "reason"
or "reminder" field. That's fixed now, everywhere, at once.

Only one place is allowed to opt back in to a real @everyone ping:
`/giveaway`'s start announcement, via:

```python
allowed_mentions=EVERYONE_PING  # discord.AllowedMentions(everyone=True, ...)
```

...and only when `features.json -> giveaway_everyone_ping` is `true` (it's
`true` by default, matching the original behavior). If you flip it off, new
giveaways just won't ping — the giveaway itself still works normally.

`admin_broadcast_everyone_ping` works the same way for `/kpwrite` — off by
default, meaning even an admin typing `@everyone` into `/kpwrite` won't
trigger a real ping unless you turn this on.

**The cosmetic layer — `neutralize_mentions()` / `sanitize_ai_response()`:**
on top of the structural fix, a handful of free-text fields (AI-driven mod
command reasons, `/remind` text, `/afk` reasons, giveaway prize text used in
plain-content congratulation messages) are also passed through
`neutralize_mentions()`, which breaks up `@everyone`/`@here` with a
zero-width space so it doesn't even *look* like a working mention, and
strips raw `<@id>`/`<@&id>` syntax. AI responses get the fuller
`sanitize_ai_response()` treatment (same as before), which also strips
non-whitelisted links and Discord invite links.

Both layers are independent on purpose — if one is ever misconfigured or a
future command forgets to call the text-scrubbing helper, the global
`allowed_mentions` default still holds the line.

### `/update` and `.update` — git pull + apply changes

Disabled by default (`features.json -> "deploy": false`). Once enabled, an
admin can run `/update` or `.update` to run `git pull --ff-only <remote>
<branch>` in `deploy_repo_path` (`--ff-only` fails loudly instead of
creating a merge commit if the branches have diverged — safer for an
unattended trigger), post the output back to the channel, and then apply
the change one of three ways, controlled by `deploy_restart_method`:

| Value | What happens | Use when |
|---|---|---|
| `"exit"` (default) | Closes the Discord connection, exits cleanly | You run under a supervisor — **NSSM**, systemd (`Restart=always`), pm2, Docker (`--restart`). NSSM restarts its managed app on exit by default. |
| `"exec"` | Re-execs the same process in place (`os.execv`) | You run it directly (`python main.py` in a terminal/tmux), with **no** supervisor. **Do not use this under NSSM** — Windows has no true `exec()`, so this spawns a brand-new process with a new PID while the old one exits; NSSM would restart the service *on top of* that, leaving two bot instances running on the same token. |
| `"reload"` | Hot-reloads changed `cogs/*.py` and most of `core/*.py` straight into the running process — **no restart at all** | You want zero-downtime updates for ordinary command/logic changes and are OK with its limits (see below). |

**`"reload"` limits** (full explanation in `core/hot_reload.py`):
- Can't reload `core/state.py` (would wipe live giveaways/AFK/snipe/reminder
  data) — it's skipped on purpose, your live state is safe.
- Can't reload `bot_instance.py`, `main.py`, or code changes to `config.py`
  itself (its *data* — `bot_data.json`/`features.json` — is still reloaded)
  — those need a real restart (`"exit"` or `"exec"`).
- Can't pick up a new `pip install` dependency — needs a fresh interpreter.
- If a reload step fails partway through, it stops immediately and tells
  you so rather than leaving a silent mixed old/new state — switch to
  `"exit"` and run `/update` again to get a clean restart.
- Resets per-user AI cooldowns (the rate limiter object is recreated).

Configurable in `features.json`:

```json
"deploy_repo_path": ".",
"deploy_git_remote": "origin",
"deploy_git_branch": "main",
"deploy_restart_method": "exit"
```

Permission is the same as other admin commands (`is_admin_user()` — the
special admin ID or server Administrator). Since this command can make the
bot run whatever code is on that branch, tighten that check in
`cogs/deploy.py` if you want it restricted to only the special admin ID.

**Before enabling it:** make sure `bot_data.json`, `features.json`,
`giveaways.json`, and `.env` are NOT committed to the git repo (see the
included `.gitignore`) — otherwise `git pull` can conflict with, or
overwrite, your live runtime data.

## Notes / things worth knowing

- **`pc_control`, `worldcup_tracker`, `epl_tracker`, `stb_ir_control`, and
  `stb_adb_control` all default to `false`.** They're Windows-only / require
  locally-running AutoHotkey + (for STB control) either an Arduino+IR rig or
  `adb` on PATH, plus an external API key for the match trackers. The bot
  runs fine on Linux/Mac with these off — previously the whole bot would
  crash on non-Windows hosts because of an unconditional `ctypes.windll`
  reference at import time. This is now guarded and only actually breaks
  (with a clear error, not a crash) if you enable `pc_control` on a
  non-Windows host.
- **`_run_ps()`** (used by `.debugwindows`, `.testinput`, `.debugdiscord`,
  `.debugedge`) was referenced in your original file but never defined
  anywhere in it — it would have raised `NameError` if those specific
  commands were ever invoked. A straightforward PowerShell-runner was added
  in `cogs/pc_control.py` so they work; swap it out if you had a different
  implementation elsewhere.
- Command *names* and *behavior* for the original non-STB/EPL commands are
  unchanged — verified against the original command list, no more, no less.