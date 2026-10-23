"""Terminal board rendering + history JSON export (legacy Core/Lean path).

House format (see nfl_props/house.py): brand header, slate line, then the
two user-facing tiers only -- Core prints as PLAYS, Lean prints as LEANS,
and Watch is deliberately omitted: watch rows are parlay-leg material and
the parlay-legs section is on hold, so they never appear on the page. Any
other tier (opposite side, unmatched, gated out) never appears either.

Every printed price is derived from `c.decimal`, the number grading settles
on -- price shown is price graded.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from . import house
from .config import HISTORY_DIR
from .tiers import Candidate
from .version import HISTORY_SCHEMA_VERSION, MODEL_VERSION, TIER_POLICY_VERSION

# Internal tier -> user-facing section. Watch is absent on purpose: its
# material belongs to parlay legs, which are on hold for this project.
TIER_SECTIONS = {"Core": "plays", "Lean": "leans"}


def _action_label(c: Candidate) -> Optional[str]:
    """'<who/what> <market> <line>' in the house grammar."""
    if c.decimal is None or c.p_model is None:
        return None
    if c.market == "MONEYLINE":
        return f"{c.side} ML"
    if c.market == "SPREAD":
        if c.line is None:
            return None
        return f"{c.side} {c.line:+g}"
    if c.market in ("TEAM_TOTAL", "GAME_TOTAL"):
        if c.line is None:
            return None
        over = str(c.side).upper().startswith("O")
        return f"{'Over' if over else 'Under'} {c.line:g}"
    return None


def pick_rows(candidates: List[Candidate]):
    """(plays, leans) pick lines; tiers not in TIER_SECTIONS are dropped."""
    plays: List[str] = []
    leans: List[str] = []
    for c in candidates:
        section = TIER_SECTIONS.get(c.tier)
        if section is None:
            continue
        action = _action_label(c)
        if action is None:
            continue
        line = house.pick_line(action, c.decimal, c.p_model)
        (plays if section == "plays" else leans).append(line)
    return plays, leans


def render_board(candidates: List[Candidate], summary: dict,
                 watch_limit: Optional[int] = 30,
                 now: Optional[datetime] = None,
                 results: Optional[dict] = None) -> str:
    now = now or datetime.now(timezone.utc)
    pairs = sorted({(c.away, c.home) for c in candidates})
    plays, leans = pick_rows(candidates)
    # watch_limit stays in the signature for callers; Watch never prints.
    return house.render_page(
        plays, leans, results,
        slate=house.slate_line(now, house.slate_note(pairs)))


def export_history(candidates: List[Candidate], summary: dict,
                   games_raw: List[dict],
                   diagnostics: Optional[dict] = None,
                   v2_shadow: Optional[list] = None) -> Path:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "history_schema_version": HISTORY_SCHEMA_VERSION,
        "model_version": MODEL_VERSION,
        "tier_policy_version": TIER_POLICY_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "candidates": [asdict(c) for c in candidates],
        "bovada_games": games_raw,
        "source_diagnostics": diagnostics or {},
        "v2_shadow": v2_shadow,
    }
    path = HISTORY_DIR / f"nfl_board_{stamp}.json"
    path.write_text(json.dumps(payload, indent=1))
    return path
