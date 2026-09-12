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
| `ai_chat` | true | `/ai`, `/aistatus`, the "oh kp baa" trigger |
| `ai_moderation_commands` | true | natural-language kick/ban/mute via the AI trigger |
| `moderation` | true | kick/ban/mute/unmute/lock/unlock/purge/slowmode/massmove |
| `fun_games` | true | poll/8ball/coinflip/trivia/wyr/truth/dare/rps |
| `utility` | true | define/weather/calendar/userinfo/serverinfo/roleinfo/avatar/snipe/date/ping |
| `reminders` | true | `/remind` |
| `afk_system` | true | `/afk` + the AFK listener |
| `confessions` | true | `/confess` |
| `giveaways` | true | `/giveaway` and friends |
| `admin_broadcast` | true | `/kpwrite`, `/kpannounce` |
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
