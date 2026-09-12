"""
core/mention_safety.py
======================
Central "safety net" for mass-ping protection (@everyone / @here / @role).

There are TWO independent layers here, on purpose:

1. STRUCTURAL layer (the important one) — `RESTRICTED_MENTIONS` is passed as
   the bot's *global default* `allowed_mentions` in bot_instance.py. This
   tells Discord itself to never resolve @everyone / @here / role pings on
   ANY message the bot sends, no matter what text ends up in the message
   content — a mute reason, a giveaway prize, an AI reply, a reminder, a
   kpannounce message, anything. This works even if a user smuggles
   "@everyone" into a `reason` field on `.mute`, `.kick`, `.ban`, `/remind`,
   `/afk`, etc. Only code that explicitly opts back in with
   `allowed_mentions=EVERYONE_PING` (see below) can ever trigger a real
   @everyone/@here ping, and that is reserved for the one deliberate
   feature that wants it (giveaway announcements), gated by a config toggle.

2. COSMETIC layer — `sanitize_ai_response()` / `neutralize_mentions()`
   additionally rewrite the *visible text* so "@everyone" doesn't even
   LOOK like a working mention (zero-width space inserted), and strip raw
   mention/channel syntax and non-whitelisted links out of AI output. This
   matters mainly for free-form AI text, which can otherwise contain all
   sorts of things we don't want rendered verbatim.

Both layers are applied independently — even if one is misconfigured or
bypassed by a coding mistake elsewhere, the other still holds.
"""
import re
import urllib.parse

import discord

# ── Layer 1: structural AllowedMentions presets ─────────────────────────────

# The safe default used almost everywhere: never ping @everyone/@here/roles,
# but still allow pinging specific users (needed for kick/ban/mute target
# pings, winner announcements, replies, etc.) and the person being replied to.
RESTRICTED_MENTIONS = discord.AllowedMentions(
    everyone=False,
    roles=False,
    users=True,
    replied_user=True,
)

# Explicit opt-in for the rare, deliberate case where a real @everyone/@here
# ping is wanted (e.g. giveaway announcements). Only ever pass this on a
# single .send() call that is itself gated behind a feature toggle and a
# permission check — never make this the bot-wide default.
EVERYONE_PING = discord.AllowedMentions(
    everyone=True,
    roles=False,
    users=True,
    replied_user=True,
)


# ── Layer 2: cosmetic text scrubbing ────────────────────────────────────────

ALLOWED_URL_DOMAINS = {
    'youtube.com', 'youtu.be', 'wikipedia.org', 'en.wikipedia.org',
    'github.com', 'stackoverflow.com', 'google.com', 'imgur.com',
}

BLOCKED_PROMPT_PATTERNS = [
    r'@everyone',
    r'@here',
    r'discord\.gg',
    r'say exactly',
    r'repeat after',
    r'repeat this',
    r'copy this',
    r'copy and paste',
    r'pretend you are an admin',
    r'pretend to be an admin',
    r'ignore (your|all|previous) (rules|instructions|prompt)',
    r'forget (your|all|previous) (rules|instructions|prompt)',
    r'jailbreak',
    r'dan mode',
    r'do anything now',
    r'you are now',
    r'new persona',
    r'act as if',
]


def _is_safe_url(url: str) -> bool:
    try:
        host = urllib.parse.urlparse(url).netloc.lower()
        host = host.lstrip('www.')
        return any(host == d or host.endswith('.' + d) for d in ALLOWED_URL_DOMAINS)
    except Exception:
        return False


def is_prompt_safe(prompt: str) -> bool:
    """Return False if the prompt contains known abuse patterns."""
    prompt_lower = prompt.lower()
    return not any(re.search(p, prompt_lower) for p in BLOCKED_PROMPT_PATTERNS)


def neutralize_mentions(text: str) -> str:
    """
    Cosmetically break @everyone / @here / raw mention syntax in arbitrary
    user-supplied text (e.g. a mute/kick/ban reason, a reminder body, an AFK
    reason) before it is echoed back in a message. This is defense-in-depth
    on top of RESTRICTED_MENTIONS — it makes sure the text doesn't even
    *look* like a working ping, regardless of which allowed_mentions a given
    send() call ends up using.
    """
    if not text:
        return text
    text = re.sub(r'@everyone', '@\u200beveryone', text, flags=re.IGNORECASE)
    text = re.sub(r'@here', '@\u200bhere', text, flags=re.IGNORECASE)
    text = re.sub(r'<@&(\d+)>', r'[role-\1]', text)      # role mentions
    text = re.sub(r'<@!?(\d+)>', r'[user-\1]', text)     # user mentions
    return text


def sanitize_ai_response(text: str) -> str:
    """Remove or neutralize anything dangerous from AI output before sending to Discord."""
    text = re.sub(r'@everyone', '@\u200beveryone', text, flags=re.IGNORECASE)
    text = re.sub(r'@here', '@\u200bhere', text, flags=re.IGNORECASE)
    text = re.sub(r'<@[!&]?\d+>', '[mention removed]', text)
    text = re.sub(r'<#\d+>', '[channel removed]', text)
    text = re.sub(r'<@&\d+>', '[role removed]', text)
    text = re.sub(
        r'(https?://)?(www\.)?(discord\.gg|discord\.com/invite)/\S+',
        '[invite link removed]', text, flags=re.IGNORECASE
    )

    def replace_url(match):
        url = match.group(0)
        return url if _is_safe_url(url) else '[link removed]'

    text = re.sub(r'https?://[^\s]+', replace_url, text)
    if len(text) > 1800:
        text = text[:1797] + '...'
    return text
