"""
core/agentai_provider.py — AI provider routing layer.

Reads ``FEATURES["ai_provider"]`` at **call time** (not import time) so that
editing features.json + using /reload switches the active backend without a
bot restart.

Supported providers
───────────────────
  "gemini"   → delegates to the original query_gemini_api (KP Oli personality, no tools)
  "agentai"  → delegates to AgentAIRuntime (Agentic AI with tools and Nepali language support)

Both paths return a string that is ready to be sanitized and sent to Discord.
"""
from typing import Optional

from config import FEATURES
from core.agentai_runtime import AgentAIRuntime, get_agentai_runtime

# ── second runtime instance (lazy-created) ─────────────
_agentai_v2_runtime: Optional[AgentAIRuntime] = None

def get_agentai_v2_runtime() -> AgentAIRuntime:
    """Get or create the *agentai* provider runtime instance."""
    global _agentai_v2_runtime
    model = FEATURES.get("agentai_model", "gemini-2.5-flash-lite")
    if _agentai_v2_runtime is None or _agentai_v2_runtime.model != model:
        # (Re-)create if model changed via features.json reload
        _agentai_v2_runtime = AgentAIRuntime(model=model)
    return _agentai_v2_runtime

# ── public API ──────────────────────────────────────────────────────────────



def get_active_provider_name() -> str:
    """Human-readable name of the currently active AI provider."""
    provider = FEATURES.get("ai_provider", "gemini")
    if provider == "agentai":
        model = FEATURES.get("agentai_model", "gemini-2.5-flash-lite")
        return f"agentAI ({model})"
    return "Gemini (default KP Oli)"
