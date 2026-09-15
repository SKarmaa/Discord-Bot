"""
cogs/deploy.py — admin-only ops command: `git pull` the latest changes on
the configured branch, then apply them via one of three strategies (see
`deploy_restart_method` in features.json):

  - "reload" — hot-reload the changed Python code into the already-running
    process, no restart at all (see core/hot_reload.py for exactly what
    this can and can't cover).
  - "exit" (default) — close the connection and exit cleanly, relying on a
    supervisor (NSSM, systemd, pm2, Docker) to bring the process back up.
  - "exec" — re-exec the same process in place (only for setups with NO
    supervisor — do NOT use this under NSSM, see _restart_bot() below).

SECURITY NOTE: whoever can run this command can make the bot execute
whatever code lands on the configured git branch, immediately. It's gated
the same way as the rest of the bot's admin commands (`is_admin_user()` —
the special admin ID or server Administrator permission), but if that's
too broad for a command with this much power in your setup, tighten the
check below to only allow the special admin ID.

Disabled by default (`features.json -> "deploy": false`) — turn it on
explicitly once you've reviewed how it's configured (repo path, remote,
branch, restart method) for your deployment.
"""
import asyncio
import os
import subprocess
import sys

import discord

from bot_instance import bot
from config import FEATURES
from core.features import require_feature
from core.hot_reload import hot_reload
from core.permissions import is_admin_user

FEATURE = "deploy"


def _git_pull() -> tuple[bool, str]:
    """Run `git pull --ff-only <remote> <branch>` in the configured repo path.
    --ff-only refuses to auto-create a merge commit or touch anything if the
    branches have diverged — it either fast-forwards cleanly or fails loudly,
    which is what you want for an unattended restart trigger."""
    repo_path = FEATURES.get("deploy_repo_path", ".")
    remote = FEATURES.get("deploy_git_remote", "origin")
    branch = FEATURES.get("deploy_git_branch", "main")

    if not os.path.isdir(os.path.join(repo_path, ".git")):
        return False, f"`{repo_path}` doesn't look like a git repository (no .git folder found there)."

    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", remote, branch],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=60,
        )
        output = ((result.stdout or "") + (result.stderr or "")).strip() or "(no output)"
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, "git pull timed out after 60 seconds."
    except Exception as e:
        return False, str(e)


async def _restart_bot():
    """Restart the bot process. Two strategies, controlled by
    features.json -> "deploy_restart_method":

      - "exit" (default): close the connection and exit cleanly. This is
        the correct choice when something else supervises and restarts the
        process for you — NSSM, systemd (Restart=always), pm2, Docker
        (--restart), etc. NSSM in particular restarts its managed app
        automatically on exit by default (see its "Exit actions" tab), so
        this is the right setting for an NSSM-installed Windows service.

      - "exec": re-exec the same process in place via os.execv, for setups
        with NO supervisor (e.g. just `python main.py` in a terminal/tmux
        session). Do NOT use this under NSSM (or any supervisor): Windows
        has no true exec() — os.execv there spawns a brand-new process
        with a new PID and exits the old one. NSSM is watching the old PID,
        so it would ALSO restart the service on top of the execv'd copy,
        leaving two bot processes running at once on the same token.
    """
    method = FEATURES.get("deploy_restart_method", "exit")
    try:
        await bot.close()
    except Exception:
        pass
    if method == "exec":
        os.execv(sys.executable, [sys.executable] + sys.argv)
    else:
        os._exit(0)


async def _run_update(send):
    """Shared logic for both the slash and prefix command. `send` is an
    async callable that takes either `content=` or `embed=` kwargs and
    posts a message. Returns "restart", "reloaded", or None (git pull
    failed, nothing was applied)."""
    await send(content="🔄 Pulling latest changes...")

    ok, output = await asyncio.get_event_loop().run_in_executor(None, _git_pull)
    output_display = output[:1500]

    if not ok:
        embed = discord.Embed(
            title="❌ Git pull failed — nothing applied",
            description=f"```\n{output_display}\n```",
            color=discord.Color.red(),
        )
        await send(embed=embed)
        return None

    method = FEATURES.get("deploy_restart_method", "exit")

    if method == "reload":
        await send(content=f"```\n{output_display}\n```\n🔁 Hot-reloading code (no restart)...")
        success, log = await hot_reload(bot)
        embed = discord.Embed(
            title="✅ Hot reload complete" if success else "❌ Hot reload failed — process NOT restarted",
            description=f"```\n{log[:3800]}\n```",
            color=discord.Color.green() if success else discord.Color.red(),
        )
        if not success:
            embed.add_field(
                name="What now?",
                value="The running code may be in a mixed old/new state. Recommend switching "
                      "`deploy_restart_method` to `exit` and running `/update` again for a clean restart.",
                inline=False,
            )
        await send(embed=embed)
        return "reloaded" if success else None

    embed = discord.Embed(
        title="✅ Git pull succeeded — restarting now",
        description=f"```\n{output_display}\n```",
        color=discord.Color.green(),
    )
    await send(embed=embed)
    return "restart"


@bot.tree.command(name="update", description="Pull latest changes from git and restart the bot (Admin only)")
@require_feature(FEATURE)
async def update_command(interaction: discord.Interaction):
    if not is_admin_user(interaction.user):
        await interaction.response.send_message("❌ You are not authorized to use this command!", ephemeral=True)
        return

    await interaction.response.defer()

    async def send(content=None, embed=None):
        kwargs = {}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        await interaction.followup.send(**kwargs)

    should_restart = await _run_update(send)
    if should_restart == "restart":
        await asyncio.sleep(1.5)  # give Discord a moment to actually deliver the message
        await _restart_bot()


@bot.command(name="update")
@require_feature(FEATURE)
async def update_prefix(ctx):
    """Pull latest changes from git and apply them (restart or hot-reload,
    per deploy_restart_method). Usage: .update (Admin only)"""
    if not is_admin_user(ctx.author):
        await ctx.send("❌ You are not authorized to use this command!")
        return

    async def send(content=None, embed=None):
        kwargs = {}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        await ctx.send(**kwargs)

    should_restart = await _run_update(send)
    if should_restart == "restart":
        await asyncio.sleep(1.5)
        await _restart_bot()
