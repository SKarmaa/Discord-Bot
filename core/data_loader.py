"""
core/data_loader.py — loads and caches JSON data files from the data/ directory.

Every module that used to hardcode dicts/lists of Nepali responses, festivals,
etc. now calls `get_data("nepali_responses")` (or whichever filename, minus
the .json extension) and gets the parsed dict back.

On `/reload`, call `reload_data()` to re-read everything from disk so edits
to the JSON files take effect without a bot restart.
"""
import json
import os
from typing import Any, Dict

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")

# In-memory cache: filename (without .json) → parsed dict/list
_cache: Dict[str, Any] = {}


def _load_file(name: str) -> Any:
    """Read and parse a single JSON file from the data/ directory."""
    path = os.path.join(_DATA_DIR, f"{name}.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print(f"⚠️  data/{name}.json not found — returning empty dict")
        return {}
    except json.JSONDecodeError as e:
        print(f"⚠️  data/{name}.json parse error: {e} — returning empty dict")
        return {}


def get_data(name: str) -> Any:
    """Get cached data for *name* (lazy-loaded on first call)."""
    if name not in _cache:
        _cache[name] = _load_file(name)
    return _cache[name]


def reload_data():
    """Re-read every previously loaded JSON file from disk."""
    for name in list(_cache.keys()):
        _cache[name] = _load_file(name)
    print(f"✅ Reloaded {len(_cache)} data file(s) from data/")


def preload(*names: str):
    """Eagerly load a list of data files into the cache."""
    for name in names:
        _cache[name] = _load_file(name)
