"""
core/hot_reload.py — reload changed Python code into the already-running
bot process, without exiting/restarting it at all.

WHAT THIS CAN RELOAD:
  - Every file under cogs/ — commands and listeners it registered are
    cleanly removed from the live `bot` object first, then the module is
    freshly re-imported, which re-registers them from the new code.
  - Everything under core/ EXCEPT core/state.py (state.py holds the bot's
    live in-memory data — confession_store, snipe_store, afk_users,
    active_reminders, active_giveaways. Reloading it would reset all of
    that to empty and orphan any asyncio Task still holding a reference to
    the old dict objects, e.g. an in-flight giveaway timer or reminder).

WHAT THIS CANNOT RELOAD (needs a real process restart instead):
  - bot_instance.py — the bot object itself, its intents, its command
    prefix. There's only ever one bot instance; nothing reloads it.
  - config.py's own Python logic. Its DATA (bot_data.json / features.json)
    IS covered here via config.reload_all() — only actual code edits to
    config.py itself need a restart.
  - main.py.
  - Anything requiring a new `pip install` — the interpreter already has
    its import machinery warmed up; a brand-new dependency needs a fresh
    Python process to become importable at all.

Minor side effect: reloading core/ai_client.py recreates the AI rate
limiter object, so every user's AI cooldown resets to zero on each hot
reload.

Also note: if a change ADDS, REMOVES, or changes the parameters of a slash
command, this calls bot.tree.sync() to push that to Discord — like any
slash command sync, that can take a little while to show up for users
(existing command *behavior* changes are instant either way, since Discord
just calls back into your code).
"""
import importlib
import sys

import config

CORE_MODULES = [
    "core.mention_safety",
    "core.permissions",
    "core.ai_client",
    "core.nepali_calendar",
    "core.features",
    # core.state is deliberately excluded — see module docstring.
]

COG_MODULES = [
    "cogs.events",
    "cogs.ai_commands",
    "cogs.moderation",
    "cogs.fun_games",
    "cogs.utility",
    "cogs.confession",
    "cogs.giveaway",
    "cogs.admin_broadcast",
    "cogs.pc_control",
    "cogs.worldcup",
    # cogs.deploy is deliberately excluded: it's the module currently
    # running this reload. A change to deploy.py itself needs a restart.
]


def _strip_module_registrations(bot, module_name: str) -> int:
    """Remove every command/listener `module_name` registered on `bot`, so
    re-importing it doesn't collide with (or duplicate) what's already
    there. Returns how many things were removed."""
    removed = 0

    for cmd in list(bot.tree.get_commands()):
        if getattr(cmd.callback, "__module__", None) == module_name:
            bot.tree.remove_command(cmd.name)
            removed += 1

    for cmd in list(bot.commands):
        if getattr(cmd.callback, "__module__", None) == module_name:
            bot.remove_command(cmd.name)
            removed += 1

    for event_name, funcs in list(bot.extra_events.items()):
        for func in list(funcs):
            if getattr(func, "__module__", None) == module_name:
                bot.remove_listener(func, name=event_name)
                removed += 1

    return removed


async def hot_reload(bot) -> tuple[bool, str]:
    """Reload core/* and cogs/* modules in place, re-registering commands
    on the live bot object. Returns (success, log_text). Stops at the
    first failure so you're not left with half-old/half-new code silently —
    if this returns False, fall back to a full restart (/update with
    deploy_restart_method set to "exit" or "exec")."""
    log_lines = []

    try:
        config.reload_all()
        log_lines.append("✅ config data (bot_data.json, features.json) reloaded")
    except Exception as e:
        return False, f"❌ Failed reloading config data: {e}\n\n" + "\n".join(log_lines)

    for mod_name in CORE_MODULES:
        try:
            mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
            importlib.reload(mod)
            log_lines.append(f"✅ {mod_name}")
        except Exception as e:
            log_lines.append(f"❌ {mod_name}: {e}")
            return False, "\n".join(log_lines)

    for mod_name in COG_MODULES:
        try:
            removed = _strip_module_registrations(bot, mod_name)
            mod = sys.modules.get(mod_name) or importlib.import_module(mod_name)
            importlib.reload(mod)
            log_lines.append(f"✅ {mod_name} ({removed} registration(s) replaced)")
        except Exception as e:
            log_lines.append(f"❌ {mod_name}: {e}")
            return False, "\n".join(log_lines)

    try:
        synced = await bot.tree.sync()
        log_lines.append(f"✅ Synced {len(synced)} slash commands with Discord")
    except Exception as e:
        log_lines.append(
            f"⚠️ Command sync failed (code was still reloaded fine — "
            f"only matters if a command's name/parameters changed): {e}"
        )

    return True, "\n".join(log_lines)
