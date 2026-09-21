"""
config.py — all configuration loading lives here.

Two files on disk drive behavior:

  bot_data.json   — content: witty_responses, welcome_messages, channel IDs
                    etc. (unchanged format from before).
  features.json   — NEW: on/off switches for whole feature areas, plus a
                    handful of tunable numbers (AI cooldown, trigger phrase,
                    special admin id, etc.) so they don't have to be edited
                    in Python code anymore.

Other modules should `from config import CONFIG, FEATURES, ...` and read the
dicts directly — reload_all() mutates these dicts *in place* (clear + update)
rather than rebinding them, so every module that imported a reference keeps
seeing live data after a `/reload`.
"""
import json
import os

from dotenv import load_dotenv

load_dotenv()

BOT_DATA_FILE = "bot_data.json"
FEATURES_FILE = "features.json"
GIVEAWAYS_FILE = "giveaways.json"

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
if not GEMINI_API_KEY:
    print("⚠️  WARNING: GEMINI_API_KEY not found! AI features disabled.")
else:
    print("✅ Gemini API key loaded")

DISCORD_TOKEN = os.getenv("TOKEN") or os.getenv("DISCORD_TOKEN")

# ── Live, mutable config dicts (import these, don't rebind them) ───────────
BOT_DATA: dict = {}
WITTY_RESPONSES: dict = {}
WELCOME_MESSAGES: list = []
CONFIG: dict = {}          # bot_data.json -> "bot_config" (channel ids etc.)
TRIGGER_WORDS: list = []
FEATURES: dict = {}        # features.json — toggles + tunables

DEFAULT_FEATURES = {
    # ── feature area toggles ──
    "ai_chat": True,                    # /ai, /aistatus, "oh kp baa" trigger
    "ai_provider": "gemini",            # "gemini" or "agentai" — toggleable AI backend
    "agentai_model": "gemini-2.5-flash-lite",  # model used when ai_provider="agentai"
    "ai_moderation_commands": True,     # natural-language kick/ban/mute via AI trigger
    "moderation": True,                 # kick/ban/mute/unmute/lock/unlock/purge/slowmode/massmove
    "fun_games": True,                  # 8ball/coinflip/trivia/wyr/truth/dare/rps/poll
    "utility": True,                    # define/weather/calendar/userinfo/serverinfo/roleinfo/avatar/snipe/date/ping
    "reminders": True,
    "afk_system": True,
    "confessions": True,
    "giveaways": True,
    "admin_broadcast": True,            # kpwrite/kpannounce/reload/words
    "pc_control": False,                # AutoHotkey/PC remote-control commands — OFF by default
    "worldcup_tracker": False,          # World Cup auto-stream scheduler + live scores — OFF by default
    "epl_tracker": False,               # EPL auto-stream scheduler + live scores — OFF by default
    "epl_target_channel_id": 0,         # 0/unset = falls back to target_channel_id
    "welcome_messages": True,
    "trigger_word_responses": True,
    "random_reactions": True,

    # ── mention / ping safety ──
    # Whether /giveaway is allowed to actually ping @everyone when it starts.
    # Even when True, this ONLY affects that one specific message — the bot's
    # global default (see bot_instance.py) still blocks @everyone/@here
    # everywhere else regardless of this setting.
    "giveaway_everyone_ping": True,
    # Whether admin-only /kpwrite and /kpannounce are allowed to actually
    # ping @everyone/@here if the admin types it. Default False: even admins
    # get the safe default unless explicitly turned on here.
    "admin_broadcast_everyone_ping": False,

    # ── tunables ──
    "ai_trigger_phrase": "oh kp baa",
    "ai_cooldown_minutes": 15,
    "special_admin_id": 783619741289414676,
    "target_channel_id": 762775973816696863,
    "command_prefix": ".",
    
    # ── /update, .update (git pull + restart) ──
    "deploy": True,  # whether the bot allows /update/.update commands at all
    "deploy_repo_path": ".",
    "deploy_git_remote": "origin",
    "deploy_git_branch": "main",
    # "exit" (default) = clean exit, let a supervisor (NSSM, systemd, pm2,
    # Docker) restart it. "exec" = self re-exec in place — only for setups
    # with NO supervisor (do NOT use under NSSM, see cogs/deploy.py).
    # "reload" = hot-reload the code into the running process, no restart
    # at all (see core/hot_reload.py for exactly what it can/can't cover).
    "deploy_restart_method": "exit",
}


def _load_features():
    global FEATURES
    if not os.path.exists(FEATURES_FILE):
        FEATURES.clear()
        FEATURES.update(DEFAULT_FEATURES)
        with open(FEATURES_FILE, "w", encoding="utf-8") as f:
            json.dump(DEFAULT_FEATURES, f, indent=2)
        print(f"✅ Created default {FEATURES_FILE}")
        return
    try:
        with open(FEATURES_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"⚠️  Error reading {FEATURES_FILE}: {e}. Using defaults.")
        loaded = {}
    merged = dict(DEFAULT_FEATURES)
    merged.update(loaded)  # user's file wins for any keys it sets
    FEATURES.clear()
    FEATURES.update(merged)


def create_default_bot_data():
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
            "confession_channel_id": 0,
            "samu_tag_reactions": ["👋", "😊", "🎉"],
            "general_reactions": ["😂", "👍", "🤔", "😎", "🔥", "✨"],
            "write_command_user_id": 0,
            "write_command_channel_id": 0,
            "general_channel_id": 0
        }
    }
    BOT_DATA = default_data
    WITTY_RESPONSES = default_data["witty_responses"]
    WELCOME_MESSAGES = default_data["welcome_messages"]
    CONFIG = default_data["bot_config"]
    TRIGGER_WORDS = list(WITTY_RESPONSES.keys())
    with open(BOT_DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(default_data, f, indent=2, ensure_ascii=False)
    print(f"✅ Created default {BOT_DATA_FILE}")


def load_all():
    """Initial load at startup — call once before the bot logs in."""
    global BOT_DATA, WITTY_RESPONSES, WELCOME_MESSAGES, CONFIG, TRIGGER_WORDS

    _load_features()

    try:
        with open(BOT_DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        BOT_DATA.clear()
        BOT_DATA.update(data)
        WITTY_RESPONSES.clear()
        WITTY_RESPONSES.update(BOT_DATA.get("witty_responses", {}))
        WELCOME_MESSAGES[:] = BOT_DATA.get("welcome_messages", [])
        CONFIG.clear()
        CONFIG.update(BOT_DATA.get("bot_config", {}))
        TRIGGER_WORDS[:] = list(WITTY_RESPONSES.keys())
        print(f"Loaded {len(WITTY_RESPONSES)} trigger categories")
        print(f"Loaded {len(WELCOME_MESSAGES)} welcome messages")
    except FileNotFoundError:
        print(f"{BOT_DATA_FILE} not found! Creating default configuration...")
        create_default_bot_data()
    except json.JSONDecodeError as e:
        print(f"Error reading {BOT_DATA_FILE}: {e}")
        create_default_bot_data()


def reload_all():
    """Reload both bot_data.json and features.json without restarting the bot.
    Mutates the existing dict/list objects in place so every module that did
    `from config import CONFIG` (etc.) sees the updated data immediately."""
    global BOT_DATA, WITTY_RESPONSES, WELCOME_MESSAGES, CONFIG, TRIGGER_WORDS
    with open(BOT_DATA_FILE, 'r', encoding='utf-8') as f:
        data = json.load(f)
    BOT_DATA.clear()
    BOT_DATA.update(data)
    WITTY_RESPONSES.clear()
    WITTY_RESPONSES.update(BOT_DATA.get("witty_responses", {}))
    WELCOME_MESSAGES[:] = BOT_DATA.get("welcome_messages", [])
    CONFIG.clear()
    CONFIG.update(BOT_DATA.get("bot_config", {}))
    TRIGGER_WORDS[:] = list(WITTY_RESPONSES.keys())
    _load_features()
    # Reload JSON data files (data/*.json) so edits take effect
    from core.data_loader import reload_data
    reload_data()


# Backwards-compatible aliases (old code called these names)
load_bot_data = load_all
reload_bot_data = reload_all

# Load immediately on import so every other module sees populated dicts.
load_all()