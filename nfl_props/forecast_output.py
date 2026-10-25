"""Terminal rendering + history export for the forecast-first board.

The page is the house format (see nfl_props/house.py): brand header, one
slate line naming the day and games, then starred PLAYS and eyed LEANS only.
A reference is eligible only when it is PLAYABLE and priced; NO_VALUE,
UNPRICED, vetoed and gated rows never reach the page -- the board speaks in
user actions, never in internal labels. Every printed price is derived from
the same decimal the grader settles on (`ref.decimal`), so price shown is
price graded.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from . import house
from .config import FORECAST_HISTORY_DIR
from .forecast import GameForecast
from .references import PLAYABLE
from .version import (FORECAST_HISTORY_SCHEMA_VERSION, FORECAST_MODEL_VERSION,
                      PRODUCT_POLICY_VERSION, VALUE_POLICY_VERSION)

SECTIONS = (
    ("moneyline", "Moneylines"),
    ("spread", "Spreads"),
    ("total", "Totals"),
)

# Tier split, locked house copy: PLAYS = best value in the proven range;
# LEANS = smaller value, or confidence too high to trust at face value.
# These are display buckets only -- eligibility is `status == PLAYABLE`, so a
# row can move between the two sections but can never appear without being a
# playable, priced reference.
LEAN_EDGE_FLOOR = 0.03          # below this the value is "smaller"
LEAN_CONFIDENCE_CEILING = 0.75  # this confident is not trusted at face value


def ref_for(fc: GameForecast, family: str):
    for ref in fc.references:
        if ref.family == family:
            return ref
    return None


def _side_label(fc: GameForecast, family: str, ref) -> str:
    if family == "moneyline":
        return fc.home if ref.side == "home" else fc.away
    if family == "spread":
        team = fc.home if ref.side == "home" else fc.away
        return f"{team} {ref.line:+g}"
    direction = "Over" if ref.side == "over" else "Under"
    return f"{fc.away}@{fc.home} {direction} {ref.line:g}"


def _projection(fc: GameForecast, family: str) -> str:
    if family == "total":
        return f"proj total {fc.projected_total:.1f}"
    return f"proj margin {fc.projected_margin:+.1f}"


def action_label(fc: GameForecast, family: str, ref) -> Optional[str]:
    """'<who/what> <market> <line>' half of the house pick line."""
    if ref.side is None:
        return None
    if family != "moneyline" and ref.line is None:
        return None
    label = _side_label(fc, family, ref)
    if family == "moneyline":
        return f"{label} ML"
    return label


def tier_for(ref) -> Optional[str]:
    """'plays' / 'leans' / None (None = never printed on the page)."""
    if ref.status != PLAYABLE or ref.decimal is None or ref.p_model is None:
        return None
    if ref.p_model >= LEAN_CONFIDENCE_CEILING:
        return "leans"
    edge = ref.edge if ref.edge is not None else 0.0
    if edge < LEAN_EDGE_FLOOR:
        return "leans"
    return "plays"


def pick_rows(forecasts: List[GameForecast]
              ) -> Tuple[List[str], List[str]]:
    """(plays, leans) rendered pick lines, in slate/family order."""
    plays: List[str] = []
    leans: List[str] = []
    for fc in forecasts:
        if not fc.available:
            continue
        for family, _label in SECTIONS:
            ref = ref_for(fc, family)
            if ref is None:
                continue
            tier = tier_for(ref)
            if tier is None:
                continue
            action = action_label(fc, family, ref)
            if action is None:
                continue
            line = house.pick_line(action, ref.decimal, ref.p_model)
            (plays if tier == "plays" else leans).append(line)
    return plays, leans


def _change_lines(changes: Optional[List[dict]]) -> List[str]:
    moved = [c for c in changes or [] if c.get("kind") == "MATERIAL_CHANGE"]
    if not moved:
        return []
    lines = [f"\u21bb Updated from early board ({len(moved)}):"]
    for c in moved:
        reasons = "; ".join(c.get("reasons", []))
        lines.append(f"  - {c.get('matchup')}: {reasons}")
    return lines


def render_board(forecasts: List[GameForecast], summary: dict,
                 now: Optional[datetime] = None, wave: bool = False,
                 changes: Optional[List[dict]] = None,
                 results: Optional[dict] = None) -> str:
    now = now or datetime.now(timezone.utc)
    extra = None
    if wave:
        et = now.astimezone(ZoneInfo("America/New_York"))
        stamp = et.strftime("%-I:%M%p").lower()
        extra = f"\U0001f30a WAVE \u2014 Remaining games ({stamp} ET)"
    pairs = [(fc.away, fc.home) for fc in forecasts]
    note = (house.slate_note(pairs, extra=extra) if pairs
            else (extra or "no scheduled games"))
    plays, leans = pick_rows(forecasts)
    return house.render_page(
        plays, leans, results,
        slate=house.slate_line(now.astimezone(ZoneInfo("America/New_York")),
                               note),
        notes=_change_lines(changes) if wave else None)


def export_history(forecasts: List[GameForecast], summary: dict,
                   diagnostics: Optional[dict] = None,
                   changes: Optional[List[dict]] = None) -> Path:
    FORECAST_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "forecast_history_schema_version": FORECAST_HISTORY_SCHEMA_VERSION,
        "model_version": FORECAST_MODEL_VERSION,
        "product_policy_version": PRODUCT_POLICY_VERSION,
        "value_policy_version": VALUE_POLICY_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "changes": changes or [],
        "forecasts": [fc.as_dict() for fc in forecasts],
        "source_diagnostics": diagnostics or {},
    }
    path = FORECAST_HISTORY_DIR / f"nfl_forecast_{stamp}.json"
    path.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    return path


def latest_history(path: Optional[Path] = None) -> Optional[List[dict]]:
    import json as _json
    directory = path or FORECAST_HISTORY_DIR
    files = sorted(directory.glob("nfl_forecast_*.json"))
    if not files:
        return None
    try:
        payload = _json.loads(files[-1].read_text(encoding="utf-8"))
        return payload.get("forecasts")
    except (ValueError, OSError):
        return None


def append_ledger(forecasts: List[GameForecast], summary: dict) -> Path:
    """Append a print log of the forecasts this run exported.

    `outputs/ledger/forecast_ledger.jsonl` is a write-only audit trail of what
    each board/pregame-refresh run printed. It is NOT the performance record and
    nothing reads it: the record is `outputs/forecast_history/` as graded by
    `grade_forecast.py`, and every ledger row duplicates the history payload from
    that same run. Treat it as a cold log, never as an independent source.
    """
    from .config import LEDGER_DIR
    LEDGER_DIR.mkdir(parents=True, exist_ok=True)
    path = LEDGER_DIR / "forecast_ledger.jsonl"
    stamp = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as fh:
        for fc in forecasts:
            fh.write(json.dumps({
                "generated_at_utc": stamp,
                "forecast": fc.as_dict(),
            }) + "\n")
    return path
