"""
cogs/music.py — YouTube music playback in voice channels (no account needed).

How it works:
  - yt-dlp finds the track (search text, YouTube / YouTube Music link, or a
    playlist link) and gets a direct audio stream URL. No ads are ever
    involved: ads are inserted by YouTube's player, not part of the stream.
  - FFmpeg reads that stream and discord.py plays it in the voice channel.

Commands (prefix shown with "."; the same commands exist as /music ...):
  .play <song name | link>   join your voice channel and queue a track/playlist
  .skip                      skip the current track
  .pause / .unpause            (.resume is taken by the PC-control DGO command)
  .stop                      clear the queue and leave the voice channel
  .queue                     show what's coming up
  .np                        show the current track
  .loop <off|track|queue>
  .shuffle
  .remove <position>         remove one track from the queue
  .volume <0-150>
  .ytupdate                  (admins) update yt-dlp when YouTube breaks playback

features.json keys (all optional, defaults shown):
  "music": true,
  "music_idle_timeout_minutes": 5,     leave after this long with nothing queued
  "music_alone_timeout_seconds": 60,   leave after this long alone in the channel
  "music_max_queue": 100,
  "music_max_playlist_import": 50,
  "music_default_volume": 60,
  "music_ffmpeg_path": "ffmpeg",       full path if ffmpeg isn't on PATH
  "music_cookies_file": ""             optional cookies.txt if YouTube starts
                                       asking the bot to "sign in" (use a
                                       throwaway account, never your main one)

Requirements (see README section):
  pip install -U "discord.py[voice]>=2.7.1"      (DAVE voice encryption support)
  pip install -U --pre "yt-dlp[default]"         (nightly yt-dlp + challenge solver)
  FFmpeg and Deno installed and on PATH.

Player state is stored on the bot object (not in this module), so a hot
reload of this file doesn't orphan a song that's currently playing.
"""
import asyncio
import os
import random
import shutil
import subprocess
import sys
from collections import deque

import discord
from discord import app_commands

try:
    import yt_dlp
    YTDLP_AVAILABLE = True
except ImportError:
    yt_dlp = None
    YTDLP_AVAILABLE = False

from bot_instance import bot
from config import FEATURES
from core.features import require_feature
from core.permissions import is_admin_user

FEATURE = "music"

if not YTDLP_AVAILABLE:
    print("⚠️  Music: yt-dlp is not installed — music commands will report an error.")
if shutil.which("deno") is None:
    print("⚠️  Music: Deno not found on PATH — yt-dlp may fail to play YouTube audio.")


# ==================== CONFIG HELPERS ====================

def _cfg(key, default):
    return FEATURES.get(key, default)


def _ffmpeg_path() -> str:
    return _cfg("music_ffmpeg_path", "ffmpeg") or "ffmpeg"


def _ydl_opts(playlist: bool = False, flat: bool = False) -> dict:
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": not playlist,
        "default_search": "ytsearch",
        "skip_download": True,
        "socket_timeout": 15,
    }
    if flat:
        opts["extract_flat"] = "in_playlist"
    if playlist:
        opts["playlistend"] = int(_cfg("music_max_playlist_import", 50))
    cookies = _cfg("music_cookies_file", "")
    if cookies and os.path.isfile(cookies):
        opts["cookiefile"] = cookies
    return opts


# ==================== TRACKS + EXTRACTION ====================

class Track:
    __slots__ = ("title", "url", "duration", "requester_id", "thumbnail")

    def __init__(self, title, url, duration, requester_id, thumbnail=None):
        self.title = title or "Unknown title"
        self.url = url
        self.duration = int(duration) if duration else 0
        self.requester_id = requester_id
        self.thumbnail = thumbnail

    @property
    def length(self) -> str:
        return _fmt_time(self.duration)


def _fmt_time(seconds: int) -> str:
    if not seconds:
        return "live/?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _entry_url(entry: dict) -> str | None:
    url = entry.get("webpage_url") or entry.get("url")
    if url and url.startswith("http"):
        return url
    vid = entry.get("id")
    return f"https://www.youtube.com/watch?v={vid}" if vid else None


def _is_playlist_link(query: str) -> bool:
    # A plain playlist link. "watch?v=...&list=RD..." (auto-mixes, which are
    # endless) is treated as just that one video.
    return "list=" in query and "watch?v=" not in query


def _resolve_sync(query: str, requester_id: int) -> list[Track]:
    """Turn user input into Track objects. Runs in a worker thread."""
    is_url = query.startswith(("http://", "https://"))
    playlist = is_url and _is_playlist_link(query)
    target = query if is_url else f"ytsearch1:{query}"

    with yt_dlp.YoutubeDL(_ydl_opts(playlist=playlist, flat=True)) as ydl:
        info = ydl.extract_info(target, download=False)

    if info is None:
        return []
    entries = info.get("entries")
    if entries is None:
        entries = [info]
    tracks = []
    for e in entries:
        if not e:
            continue
        url = _entry_url(e)
        if not url:
            continue
        thumb = e.get("thumbnail")
        if not thumb and e.get("thumbnails"):
            thumb = e["thumbnails"][-1].get("url")
        tracks.append(Track(e.get("title"), url, e.get("duration"), requester_id, thumb))
    return tracks


def _stream_sync(url: str) -> tuple[str, str | None, dict]:
    """Get a fresh direct audio URL for a track (they expire, so this runs
    right before playback). Returns (stream_url, user_agent, info)."""
    with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
        info = ydl.extract_info(url, download=False)
    if info.get("entries"):
        info = next(e for e in info["entries"] if e)
    stream_url = info.get("url")
    if not stream_url:
        # Some extractions return the chosen format list instead of "url".
        for f in reversed(info.get("requested_formats") or []):
            if f.get("url"):
                stream_url = f["url"]
                break
    if not stream_url:
        raise RuntimeError("no playable audio stream found")
    ua = (info.get("http_headers") or {}).get("User-Agent")
    return stream_url, ua, info


def _short_error(e: Exception) -> str:
    text = str(e).replace("ERROR: ", "").strip()
    low = text.lower()
    if "sign in to confirm" in low or "not a bot" in low:
        return ("YouTube is asking the bot to sign in. Try `.ytupdate`, or set "
                "`music_cookies_file` in features.json (throwaway account).")
    if "private video" in low or "unavailable" in low:
        return "That video is private or unavailable."
    if "age" in low and "restrict" in low:
        return "That video is age-restricted and can't be played without an account."
    return text[:300] or e.__class__.__name__


# ==================== PER-SERVER PLAYER ====================

class GuildPlayer:
    def __init__(self, guild: discord.Guild, text_channel):
        self.guild = guild
        self.text_channel = text_channel
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.loop_mode = "off"          # off | track | queue
        self.volume = max(0, min(150, int(_cfg("music_default_volume", 60)))) / 100
        self._added = asyncio.Event()
        self._next = asyncio.Event()
        self._skip_requested = False
        self._task = asyncio.get_running_loop().create_task(self._run())
        self._alone_task: asyncio.Task | None = None

    @property
    def vc(self) -> discord.VoiceClient | None:
        return self.guild.voice_client

    def add(self, tracks: list[Track]):
        self.queue.extend(tracks)
        self._added.set()

    def skip(self):
        self._skip_requested = True
        if self.vc and (self.vc.is_playing() or self.vc.is_paused()):
            self.vc.stop()

    def set_volume(self, vol: float):
        self.volume = vol
        if self.vc and isinstance(self.vc.source, discord.PCMVolumeTransformer):
            self.vc.source.volume = vol

    async def _say(self, content=None, embed=None):
        try:
            await self.text_channel.send(content=content, embed=embed)
        except Exception:
            pass

    async def _wait_for_track(self) -> Track | None:
        timeout = float(_cfg("music_idle_timeout_minutes", 5)) * 60
        while not self.queue:
            self._added.clear()
            try:
                await asyncio.wait_for(self._added.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
        return self.queue.popleft()

    async def _run(self):
        try:
            while True:
                self._next.clear()

                # Pick the next track according to the loop mode.
                repeating = False
                if self.current and self.loop_mode == "track" and not self._skip_requested:
                    track = self.current
                    repeating = True
                else:
                    if self.current and self.loop_mode == "queue":
                        self.queue.append(self.current)
                    self.current = None
                    track = await self._wait_for_track()
                    if track is None:
                        await self._say("👋 Nothing left to play — leaving the voice channel.")
                        break
                self._skip_requested = False

                if not self.vc or not self.vc.is_connected():
                    break

                try:
                    stream_url, ua, info = await asyncio.to_thread(_stream_sync, track.url)
                except Exception as e:
                    await self._say(f"⚠️ Couldn't play **{discord.utils.escape_markdown(track.title)}**: {_short_error(e)}")
                    self.current = None
                    continue

                if not track.duration and info.get("duration"):
                    track.duration = int(info["duration"])

                before = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin"
                if ua:
                    before += f' -user_agent "{ua}"'
                try:
                    source = discord.PCMVolumeTransformer(
                        discord.FFmpegPCMAudio(
                            stream_url,
                            executable=_ffmpeg_path(),
                            before_options=before,
                            options="-vn -loglevel error",
                        ),
                        volume=self.volume,
                    )
                except Exception as e:
                    await self._say(f"⚠️ FFmpeg couldn't start (is it installed and on PATH?): `{e}`")
                    break

                self.current = track
                loop = asyncio.get_running_loop()

                def _after(err, _loop=loop):
                    if err:
                        print(f"[music] playback error: {err}")
                    _loop.call_soon_threadsafe(self._next.set)

                self.vc.play(source, after=_after)
                if not repeating:  # don't re-announce every repeat in loop-track mode
                    await self._say(embed=_now_playing_embed(self, track, title="🎶 Now playing"))
                await self._next.wait()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[music] player loop crashed: {e!r}")
        finally:
            await self.destroy(from_loop=True)

    async def destroy(self, from_loop: bool = False):
        players = _players()
        if players.get(self.guild.id) is self:
            players.pop(self.guild.id, None)
        self.queue.clear()
        self.current = None
        if self._alone_task:
            self._alone_task.cancel()
        if not from_loop and self._task and not self._task.done():
            self._task.cancel()
        vc = self.vc
        if vc and vc.is_connected():
            try:
                await vc.disconnect(force=True)
            except Exception:
                pass


def _players() -> dict[int, GuildPlayer]:
    # Lives on the bot object so it survives a hot reload of this module.
    if not hasattr(bot, "_kp_music_players"):
        bot._kp_music_players = {}
    return bot._kp_music_players


def _now_playing_embed(player: GuildPlayer, track: Track, title="🎶 Now playing") -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=f"[{discord.utils.escape_markdown(track.title)}]({track.url})",
        color=discord.Color.red(),
    )
    embed.add_field(name="Length", value=track.length)
    embed.add_field(name="Requested by", value=f"<@{track.requester_id}>")
    if player.loop_mode != "off":
        embed.add_field(name="Loop", value=player.loop_mode)
    if player.queue:
        embed.set_footer(text=f"{len(player.queue)} more in queue")
    if track.thumbnail:
        embed.set_thumbnail(url=track.thumbnail)
    return embed


# ==================== SHARED COMMAND LOGIC ====================
# Every function takes (guild, author, channel, send) so the prefix and slash
# versions share one implementation. `send` is an async callable that accepts
# content= / embed=.

def _same_channel_error(guild, author) -> str | None:
    vc = guild.voice_client
    if not vc:
        return "❌ I'm not playing anything right now."
    if not author.voice or author.voice.channel != vc.channel:
        return "❌ You need to be in my voice channel to do that."
    return None


async def do_play(guild, author, channel, send, query: str):
    if not YTDLP_AVAILABLE:
        return await send(content="❌ yt-dlp isn't installed on the bot's machine.")
    if not query or not query.strip():
        return await send(content="❌ Tell me what to play: a song name or a YouTube link.")
    if not author.voice or not author.voice.channel:
        return await send(content="❌ Join a voice channel first.")

    target = author.voice.channel
    vc = guild.voice_client
    if vc and vc.channel != target:
        if vc.is_playing() or vc.is_paused():
            return await send(content=f"❌ I'm already playing in {vc.channel.mention}.")
        await vc.move_to(target)
    elif not vc:
        try:
            await target.connect(self_deaf=True)
        except Exception as e:
            hint = ""
            if "davey" in str(e).lower() or "dave" in str(e).lower():
                hint = ' Update discord.py: `pip install -U "discord.py[voice]>=2.7.1"`.'
            return await send(content=f"❌ Couldn't join the voice channel: `{e}`.{hint}")

    player = _players().get(guild.id)
    if player is None or player._task.done():
        player = GuildPlayer(guild, channel)
        _players()[guild.id] = player
    else:
        player.text_channel = channel

    max_q = int(_cfg("music_max_queue", 100))
    room = max_q - len(player.queue)
    if room <= 0:
        return await send(content=f"❌ The queue is full ({max_q} tracks).")

    try:
        tracks = await asyncio.to_thread(_resolve_sync, query.strip(), author.id)
    except Exception as e:
        return await send(content=f"❌ {_short_error(e)}")
    if not tracks:
        return await send(content="❌ Nothing found for that.")

    tracks = tracks[:room]
    starting_now = player.current is None and not player.queue
    player.add(tracks)

    if len(tracks) > 1:
        await send(content=f"📃 Added **{len(tracks)}** tracks to the queue.")
    elif not starting_now:
        t = tracks[0]
        await send(content=f"➕ Queued **{discord.utils.escape_markdown(t.title)}** ({t.length}) — position {len(player.queue)}.")
    else:
        await send(content=f"🔎 Found **{discord.utils.escape_markdown(tracks[0].title)}**, starting…")


async def do_skip(guild, author, send):
    if err := _same_channel_error(guild, author):
        return await send(content=err)
    player = _players().get(guild.id)
    if not player or not player.current:
        return await send(content="❌ Nothing is playing.")
    title = player.current.title
    player.skip()
    await send(content=f"⏭️ Skipped **{discord.utils.escape_markdown(title)}**.")


async def do_pause(guild, author, send):
    if err := _same_channel_error(guild, author):
        return await send(content=err)
    vc = guild.voice_client
    if vc.is_playing():
        vc.pause()
        return await send(content="⏸️ Paused.")
    await send(content="❌ Nothing is playing.")


async def do_resume(guild, author, send):
    if err := _same_channel_error(guild, author):
        return await send(content=err)
    vc = guild.voice_client
    if vc.is_paused():
        vc.resume()
        return await send(content="▶️ Resumed.")
    await send(content="❌ Nothing is paused.")


async def do_stop(guild, author, send):
    if err := _same_channel_error(guild, author):
        return await send(content=err)
    player = _players().get(guild.id)
    if player:
        await player.destroy()
    elif guild.voice_client:
        await guild.voice_client.disconnect(force=True)
    await send(content="⏹️ Stopped and cleared the queue.")


async def do_queue(guild, send):
    player = _players().get(guild.id)
    if not player or (not player.current and not player.queue):
        return await send(content="📭 The queue is empty.")
    lines = []
    if player.current:
        lines.append(f"**Now:** {discord.utils.escape_markdown(player.current.title)} ({player.current.length})\n")
    for i, t in enumerate(list(player.queue)[:15], start=1):
        lines.append(f"`{i}.` {discord.utils.escape_markdown(t.title)} ({t.length})")
    extra = len(player.queue) - 15
    if extra > 0:
        lines.append(f"…and {extra} more")
    total = sum(t.duration for t in player.queue)
    embed = discord.Embed(title="🎵 Queue", description="\n".join(lines), color=discord.Color.red())
    embed.set_footer(text=f"{len(player.queue)} queued · {_fmt_time(total)} total · loop: {player.loop_mode}")
    await send(embed=embed)


async def do_np(guild, send):
    player = _players().get(guild.id)
    if not player or not player.current:
        return await send(content="❌ Nothing is playing.")
    await send(embed=_now_playing_embed(player, player.current))


async def do_loop(guild, author, send, mode: str | None):
    if err := _same_channel_error(guild, author):
        return await send(content=err)
    player = _players().get(guild.id)
    if not player:
        return await send(content="❌ Nothing is playing.")
    order = ["off", "track", "queue"]
    if mode is None:
        mode = order[(order.index(player.loop_mode) + 1) % 3]
    mode = mode.lower()
    if mode not in order:
        return await send(content="❌ Loop mode must be `off`, `track` or `queue`.")
    player.loop_mode = mode
    icons = {"off": "➡️", "track": "🔂", "queue": "🔁"}
    await send(content=f"{icons[mode]} Loop: **{mode}**")


async def do_shuffle(guild, author, send):
    if err := _same_channel_error(guild, author):
        return await send(content=err)
    player = _players().get(guild.id)
    if not player or len(player.queue) < 2:
        return await send(content="❌ Not enough tracks in the queue to shuffle.")
    items = list(player.queue)
    random.shuffle(items)
    player.queue.clear()
    player.queue.extend(items)
    await send(content=f"🔀 Shuffled {len(items)} tracks.")


async def do_remove(guild, author, send, position: int):
    if err := _same_channel_error(guild, author):
        return await send(content=err)
    player = _players().get(guild.id)
    if not player or not (1 <= position <= len(player.queue)):
        return await send(content="❌ No track at that position. Check `.queue`.")
    items = list(player.queue)
    removed = items.pop(position - 1)
    player.queue.clear()
    player.queue.extend(items)
    await send(content=f"🗑️ Removed **{discord.utils.escape_markdown(removed.title)}**.")


async def do_volume(guild, author, send, level: int | None):
    player = _players().get(guild.id)
    if level is None:
        current = int((player.volume if player else _cfg("music_default_volume", 60) / 100) * 100)
        return await send(content=f"🔊 Volume is **{current}%**.")
    if err := _same_channel_error(guild, author):
        return await send(content=err)
    if not player:
        return await send(content="❌ Nothing is playing.")
    level = max(0, min(150, level))
    player.set_volume(level / 100)
    await send(content=f"🔊 Volume set to **{level}%**.")


def _pip_update_sync() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pip", "install", "-U", "--pre", "yt-dlp[default]"],
        capture_output=True, text=True, timeout=300,
    )
    return proc.returncode, (proc.stdout + proc.stderr)


async def do_ytupdate(author, send):
    if not is_admin_user(author):
        return await send(content="❌ Only server admins can update yt-dlp.")
    old = getattr(getattr(yt_dlp, "version", None), "__version__", "unknown") if yt_dlp else "not installed"
    await send(content="⏳ Updating yt-dlp (nightly channel)…")
    try:
        code, out = await asyncio.to_thread(_pip_update_sync)
    except Exception as e:
        return await send(content=f"❌ Update failed to run: `{e}`")
    if code != 0:
        return await send(content=f"❌ pip failed:\n```{out[-1500:]}```")
    await send(content=(
        f"✅ yt-dlp updated (was `{old}`). **Restart the bot** for the new version "
        f"to load — `/update` or restarting the NSSM service both work."
    ))


# ==================== PREFIX COMMANDS ====================

def _free(*aliases: str) -> list[str]:
    """Only keep aliases no other cog has taken, so loading this file can
    never crash on a name clash."""
    return [a for a in aliases if a not in bot.all_commands]


def _prefix(name: str, *aliases: str):
    """Register a prefix command only if no other cog already owns `name`.
    On a clash the command is skipped (with a console warning) and the
    /music slash version still works."""
    def deco(func):
        if name in bot.all_commands:
            print(f"⚠️  Music: .{name} is already used by another cog — skipped (use /music {name}).")
            return func
        return bot.command(name=name, aliases=_free(*aliases))(func)
    return deco


@_prefix("play", "p")
@require_feature(FEATURE)
async def play_prefix(ctx, *, query: str = None):
    if not ctx.guild:
        return
    async with ctx.typing():
        await do_play(ctx.guild, ctx.author, ctx.channel, ctx.send, query)


@_prefix("skip", "next")
@require_feature(FEATURE)
async def skip_prefix(ctx):
    if ctx.guild:
        await do_skip(ctx.guild, ctx.author, ctx.send)


@_prefix("pause")
@require_feature(FEATURE)
async def pause_prefix(ctx):
    if ctx.guild:
        await do_pause(ctx.guild, ctx.author, ctx.send)


@_prefix("unpause", "resume")  # .resume belongs to pc_control (DGO page)
@require_feature(FEATURE)
async def resume_prefix(ctx):
    if ctx.guild:
        await do_resume(ctx.guild, ctx.author, ctx.send)


@_prefix("stop", "leave", "dc")
@require_feature(FEATURE)
async def stop_prefix(ctx):
    if ctx.guild:
        await do_stop(ctx.guild, ctx.author, ctx.send)


@_prefix("queue", "q")
@require_feature(FEATURE)
async def queue_prefix(ctx):
    if ctx.guild:
        await do_queue(ctx.guild, ctx.send)


@_prefix("np", "nowplaying")
@require_feature(FEATURE)
async def np_prefix(ctx):
    if ctx.guild:
        await do_np(ctx.guild, ctx.send)


@_prefix("loop")
@require_feature(FEATURE)
async def loop_prefix(ctx, mode: str = None):
    if ctx.guild:
        await do_loop(ctx.guild, ctx.author, ctx.send, mode)


@_prefix("shuffle")
@require_feature(FEATURE)
async def shuffle_prefix(ctx):
    if ctx.guild:
        await do_shuffle(ctx.guild, ctx.author, ctx.send)


@_prefix("remove")
@require_feature(FEATURE)
async def remove_prefix(ctx, position: int = 0):
    if ctx.guild:
        await do_remove(ctx.guild, ctx.author, ctx.send, position)


@_prefix("volume", "vol")
@require_feature(FEATURE)
async def volume_prefix(ctx, level: int = None):
    if ctx.guild:
        await do_volume(ctx.guild, ctx.author, ctx.send, level)


@_prefix("ytupdate")
@require_feature(FEATURE)
async def ytupdate_prefix(ctx):
    if ctx.guild:
        await do_ytupdate(ctx.author, ctx.send)


# ==================== SLASH COMMANDS (/music ...) ====================
# Grouped under /music so they can't clash with any existing slash command.

music_group = app_commands.Group(name="music", description="Play music from YouTube in voice", guild_only=True)


def _followup(interaction: discord.Interaction):
    async def send(content=None, embed=None):
        kwargs = {}
        if content is not None:
            kwargs["content"] = content
        if embed is not None:
            kwargs["embed"] = embed
        await interaction.followup.send(**kwargs)
    return send


@music_group.command(name="play", description="Play a song name, YouTube link or playlist link")
@app_commands.describe(query="Song name, YouTube / YouTube Music link, or playlist link")
@require_feature(FEATURE)
async def play_slash(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    await do_play(interaction.guild, interaction.user, interaction.channel, _followup(interaction), query)


@music_group.command(name="skip", description="Skip the current track")
@require_feature(FEATURE)
async def skip_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await do_skip(interaction.guild, interaction.user, _followup(interaction))


@music_group.command(name="pause", description="Pause playback")
@require_feature(FEATURE)
async def pause_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await do_pause(interaction.guild, interaction.user, _followup(interaction))


@music_group.command(name="resume", description="Resume playback")
@require_feature(FEATURE)
async def resume_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await do_resume(interaction.guild, interaction.user, _followup(interaction))


@music_group.command(name="stop", description="Clear the queue and leave the voice channel")
@require_feature(FEATURE)
async def stop_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await do_stop(interaction.guild, interaction.user, _followup(interaction))


@music_group.command(name="queue", description="Show the queue")
@require_feature(FEATURE)
async def queue_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await do_queue(interaction.guild, _followup(interaction))


@music_group.command(name="nowplaying", description="Show the current track")
@require_feature(FEATURE)
async def np_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await do_np(interaction.guild, _followup(interaction))


@music_group.command(name="loop", description="Set loop mode")
@app_commands.choices(mode=[
    app_commands.Choice(name="off", value="off"),
    app_commands.Choice(name="track", value="track"),
    app_commands.Choice(name="queue", value="queue"),
])
@require_feature(FEATURE)
async def loop_slash(interaction: discord.Interaction, mode: app_commands.Choice[str]):
    await interaction.response.defer()
    await do_loop(interaction.guild, interaction.user, _followup(interaction), mode.value)


@music_group.command(name="shuffle", description="Shuffle the queue")
@require_feature(FEATURE)
async def shuffle_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await do_shuffle(interaction.guild, interaction.user, _followup(interaction))


@music_group.command(name="remove", description="Remove a track from the queue")
@app_commands.describe(position="Position shown in /music queue")
@require_feature(FEATURE)
async def remove_slash(interaction: discord.Interaction, position: app_commands.Range[int, 1, 1000]):
    await interaction.response.defer()
    await do_remove(interaction.guild, interaction.user, _followup(interaction), position)


@music_group.command(name="volume", description="Set the volume (0-150)")
@require_feature(FEATURE)
async def volume_slash(interaction: discord.Interaction, level: app_commands.Range[int, 0, 150]):
    await interaction.response.defer()
    await do_volume(interaction.guild, interaction.user, _followup(interaction), level)


@music_group.command(name="ytupdate", description="(Admins) update yt-dlp when YouTube playback breaks")
@require_feature(FEATURE)
async def ytupdate_slash(interaction: discord.Interaction):
    await interaction.response.defer()
    await do_ytupdate(interaction.user, _followup(interaction))


bot.tree.add_command(music_group, override=True)


# ==================== VOICE STATE: auto-leave + cleanup ====================

async def _leave_if_still_alone(player: GuildPlayer, delay: float):
    try:
        await asyncio.sleep(delay)
    except asyncio.CancelledError:
        return
    vc = player.vc
    if vc and vc.is_connected() and not any(not m.bot for m in vc.channel.members):
        await player._say("👋 Everyone left — stopping the music.")
        await player.destroy()


@bot.listen("on_voice_state_update")
async def _music_voice_state(member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
    player = _players().get(member.guild.id)
    if player is None:
        return

    # The bot itself was disconnected or kicked from voice.
    if member.id == bot.user.id and after.channel is None:
        await player.destroy()
        return

    vc = player.vc
    if not vc or not vc.is_connected():
        return
    humans = [m for m in vc.channel.members if not m.bot]
    if not humans:
        if player._alone_task is None or player._alone_task.done():
            delay = float(_cfg("music_alone_timeout_seconds", 60))
            player._alone_task = asyncio.create_task(_leave_if_still_alone(player, delay))
    elif player._alone_task and not player._alone_task.done():
        player._alone_task.cancel()
