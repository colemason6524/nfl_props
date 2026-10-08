"""Personnel / injury context adapter (fail-open, cache-first).

The product must never be blocked by unavailable injury or inactive
information, so this adapter is intentionally passive: it reads a local cache
(`data/processed/personnel.json`) if present and otherwise returns neutral
context. No reliable no-key live feed is wired yet; a future scraper or manual
update can populate the cache in the same shape:

    {
      "ARI": {"qb_status": "out",
               "key_out": [{"position": "WR1", "player_id": None}],
               "backup_qb_id": None,
               "updated_at": "...",
               "source": "..."},
      ...
    }

`qb_status` is one of `confirmed`, `questionable`, `out`, `unknown`.
`key_out` entries are `{"position", "player_id"}` dicts; legacy plain-string
entries (e.g. `"WR1"`) are still accepted and normalized on read. Until a
source is evaluated and populated, forecasts are published unchanged with
`personnel_available=false`.
"""
from __future__ import annotations

import json
from typing import Dict, List, Optional

from ..config import PROCESSED_DIR, RAW_DIR

PERSONNEL_PATH = PROCESSED_DIR / "personnel.json"
PERSONNEL_RAW_DIR = RAW_DIR / "personnel"

VALID_QB_STATUSES = ("confirmed", "questionable", "out", "unknown")

NEUTRAL = {"qb_status": "unknown", "key_out": [], "updated_at": None,
           "source": None, "backup_qb_id": None}


def normalize_key_out(entries) -> List[dict]:
    """Normalize `key_out` entries to `{"position", "player_id"}` dicts.

    Legacy plain-string entries (e.g. `"WR1"`) become
    `{"position": "WR1", "player_id": None}`. Dict entries keep their
    position and optional player_id. Non-string/non-dict entries
    (e.g. numbers, None) are dropped (fail-open).
    """
    if entries is None:
        return []
    if isinstance(entries, str):
        entries = [entries]
    try:
        items = list(entries)
    except TypeError:
        return []
    normalized = []
    for entry in items:
        if isinstance(entry, str):
            normalized.append({"position": entry, "player_id": None})
        elif isinstance(entry, dict):
            normalized.append({"position": entry.get("position"),
                               "player_id": entry.get("player_id")})
        # else: drop non-string/non-dict entries (fail-open)
    return normalized


def load_personnel() -> Dict[str, dict]:
    """Load the local personnel cache, or an empty dict (fail-open)."""
    if not PERSONNEL_PATH.exists():
        return {}
    try:
        payload = json.loads(PERSONNEL_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def personnel_context(team: Optional[str],
                      cache: Optional[Dict[str, dict]] = None) -> dict:
    """Neutral-by-default context for one team."""
    data = cache if cache is not None else load_personnel()
    ctx = {k: (list(v) if isinstance(v, list) else v)
           for k, v in NEUTRAL.items()}
    if team and team in data and isinstance(data[team], dict):
        for key in NEUTRAL:
            if key in data[team]:
                ctx[key] = data[team][key]
        ctx["key_out"] = normalize_key_out(ctx["key_out"])
        ctx["personnel_available"] = True
    else:
        ctx["personnel_available"] = False
    return ctx
