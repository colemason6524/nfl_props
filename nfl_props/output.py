"""Terminal board rendering + history JSON export."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .config import HISTORY_DIR
from .tiers import Candidate
from .version import HISTORY_SCHEMA_VERSION, MODEL_VERSION, TIER_POLICY_VERSION


def _fmt_line(c: Candidate) -> str:
    if c.market == "SPREAD":
        return f"{c.line:+.1f}"
    if c.market in ("TEAM_TOTAL", "GAME_TOTAL"):
        return f"{c.line:.1f}"
    return "-"


def render_board(candidates: List[Candidate], summary: dict) -> str:
    lines = []
    lines.append(f"NFL board — {summary['games_screened']} games | "
                 f"state as of {summary.get('state_as_of')} | "
                 f"Core {summary['core']} / Lean {summary['lean']} / "
                 f"Watch {summary['watch']}")
    if summary.get("unmatched_games"):
        lines.append(f"UNMATCHED (skipped): {summary['unmatched_games']}")

    lines.append("\nProjections (model, before market):")
    lines.append(f"{'matchup':<14} {'away':>5} {'home':>5} {'margin':>7} {'total':>6}")
    for p in summary.get("projections", []):
        lines.append(f"{p['away'] + ' @ ' + p['home']:<14} {p['mu_away']:>5.1f} "
                     f"{p['mu_home']:>5.1f} {p['margin']:>+7.1f} {p['total']:>6.1f}")

    for tier in ("Core", "Lean", "Watch"):
        rows = [c for c in candidates if c.tier == tier]
        if not rows:
            continue
        lines.append(f"\n{tier} ({len(rows)}):")
        lines.append(f"{'matchup':<14} {'market':<10} {'side':<10} {'line':>6} "
                     f"{'odds':>6} {'p_mod':>6} {'p_mkt':>6} {'edge':>6} "
                     f"{'EV':>7} {'flags'}")
        for c in rows:
            lines.append(
                f"{c.away + ' @ ' + c.home:<14} {c.market:<10} {c.side:<10} "
                f"{_fmt_line(c):>6} {c.american:>+6d} {c.p_model:>6.3f} "
                f"{c.p_market:>6.3f} {c.edge:>+6.3f} {c.ev:>+7.2%} "
                f"{','.join(c.flags)}")
    return "\n".join(lines)


def export_history(candidates: List[Candidate], summary: dict,
                   games_raw: List[dict],
                   diagnostics: Optional[dict] = None) -> Path:
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
    }
    path = HISTORY_DIR / f"nfl_board_{stamp}.json"
    path.write_text(json.dumps(payload, indent=1))
    return path
