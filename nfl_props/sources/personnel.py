"""Personnel / injury context adapter (fail-open, cache-first).

The product must never be blocked by unavailable injury or inactive
information, so this adapter is intentionally passive: it reads a local cache
(`data/processed/personnel.json`) if present and otherwise returns neutral
context. No reliable no-key live feed is wired yet; a future scraper or manual
update can populate the cache in the same shape:

    {
      "ARI": {"qb_status": "out", "key_out": ["WR1"], "updated_at": "...",
               "source": "..."},
      ...
    }

`qb_status` is one of `confirmed`, `questionable`, `out`, `unknown`. Until a
source is evaluated and populated, forecasts are published unchanged with
`personnel_available=false`.
"""
from __future__ import annotations

import json
from typing import Dict, Optional

from ..config import PROCESSED_DIR, RAW_DIR

PERSONNEL_PATH = PROCESSED_DIR / "personnel.json"
PERSONNEL_RAW_DIR = RAW_DIR / "personnel"

NEUTRAL = {"qb_status": "unknown", "key_out": [], "updated_at": None,
           "source": None}


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
    ctx = dict(NEUTRAL)
    if team and team in data and isinstance(data[team], dict):
        for key in NEUTRAL:
            if key in data[team]:
                ctx[key] = data[team][key]
        ctx["personnel_available"] = True
    else:
        ctx["personnel_available"] = False
    return ctx
