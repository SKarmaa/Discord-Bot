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
  pc_control.py                     AutoHotkey/PC remote-control commands (Windows only)
  worldcup.py                        World Cup 2026 auto-stream scheduler + live scores
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
| `pc_control` | **false** | AutoHotkey/PC remote-control commands (Windows-only, needs a local AHK script) |
| `worldcup_tracker` | **false** | World Cup auto-stream scheduler + live scores (needs `FOOTBALL_DATA_API_KEY`) |
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

## The @everyone / @here safety net

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

## `/update` and `.update` — git pull + apply changes

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

- **`pc_control` and `worldcup_tracker` default to `false`.** They're
  Windows-only / require a locally-running AutoHotkey script and an
  external API key respectively. The bot now runs fine on Linux/Mac with
  these off — previously the whole bot would crash on non-Windows hosts
  because of an unconditional `ctypes.windll` reference at import time. This
  is now guarded and only actually breaks (with a clear error, not a crash)
  if you enable `pc_control` on a non-Windows host.
- **`_run_ps()`** (used by `.debugwindows`, `.testinput`, `.debugdiscord`,
  `.debugedge`) was referenced in your original file but never defined
  anywhere in it — it would have raised `NameError` if those specific
  commands were ever invoked. A straightforward PowerShell-runner was added
  in `cogs/pc_control.py` so they work; swap it out if you had a different
  implementation elsewhere.
- Command *names* and *behavior* are unchanged — verified the new bot
  registers the exact same 35 slash commands and 45 prefix commands as the
  original file, no more, no less.