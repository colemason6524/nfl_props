#!/usr/bin/env python3
"""Grade forecast-first snapshots: forecast quality + reference ROI.

Separate from the legacy grade.py. Reads outputs/forecast_history/
nfl_forecast_*.json, selects the latest pre-kickoff forecast per stable
forecast id, and reports:

  forecast  : winner accuracy (ties excluded), Brier, log loss, margin/total MAE
  reference : flat-1u ROI at captured prices, by family / source / label

Forecast accuracy and reference value are never pooled. NFL ties settle the
moneyline reference as a push (stake returned), never a loss.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from nfl_props import config
from nfl_props.config import FORECAST_BACKTESTS_DIR, FORECAST_HISTORY_DIR
from nfl_props.utils import log
from nfl_props.version import (FORECAST_GRADING_VERSION,
                               FORECAST_MODEL_VERSION)


def _parse_ts(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def select_latest_pregame(paths: List[Path]) -> Dict[str, dict]:
    chosen: Dict[str, dict] = {}
    for path in sorted(paths):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log(f"[grade_forecast] skipping {path.name}: {exc}")
            continue
        if payload.get("model_version") != FORECAST_MODEL_VERSION:
            continue
        generated = _parse_ts(payload.get("generated_at_utc"))
        if generated is None:
            continue
        for fc in payload.get("forecasts", []):
            if not fc.get("available"):
                continue
            kickoff = _parse_ts(fc.get("kickoff_utc"))
            if kickoff is None or generated > kickoff:
                continue
            fid = fc.get("forecast_id")
            prev = chosen.get(fid)
            if prev is None or generated > prev["_generated"]:
                row = dict(fc)
                row["_generated"] = generated
                row["_kickoff"] = kickoff
                chosen[fid] = row
    return chosen


def match_final(games: pd.DataFrame, away: str, home: str,
                kickoff: datetime) -> Optional[pd.Series]:
    for delta in (0, -1, 1):
        day = (kickoff + timedelta(days=delta)).date().isoformat()
        rows = games[(games["away_team"] == away) & (games["home_team"] == home)
                     & (games["gameday"].astype(str).str[:10] == day)]
        if len(rows):
            return rows.iloc[0]
    return None


def grade_reference(ref: dict, home_pts: float, away_pts: float
                    ) -> Optional[str]:
    family, side, line = ref.get("family"), ref.get("side"), ref.get("line")
    if family == "moneyline":
        if side not in ("home", "away"):
            return None
        team, opp = ((home_pts, away_pts) if side == "home"
                     else (away_pts, home_pts))
        if team == opp:
            return "push"
        return "win" if team > opp else "loss"
    if family == "spread" and line is not None and side in ("home", "away"):
        team, opp = ((home_pts, away_pts) if side == "home"
                     else (away_pts, home_pts))
        adj = team + float(line)
        if adj == opp:
            return "push"
        return "win" if adj > opp else "loss"
    if family == "total" and line is not None and side in ("over", "under"):
        total = home_pts + away_pts
        if total == float(line):
            return "push"
        hit = total > float(line) if side == "over" else total < float(line)
        return "win" if hit else "loss"
    return None


def units(result: str, decimal: float) -> float:
    if result == "win":
        return decimal - 1.0
    if result == "loss":
        return -1.0
    return 0.0


def _edge_band(edge: Optional[float]) -> str:
    if edge is None:
        return "unknown"
    if edge < 0:
        return "negative"
    if edge < 0.03:
        return "0-3%"
    if edge < 0.06:
        return "3-6%"
    if edge < 0.10:
        return "6-10%"
    return "10%+"


def grade_all(since: str = "") -> dict:
    from nfl_props.sources.nflverse import load_raw_games
    games = load_raw_games()

    paths = sorted(FORECAST_HISTORY_DIR.glob("nfl_forecast_*.json"))
    if since:
        floor = datetime.fromisoformat(since).replace(tzinfo=timezone.utc)
        paths = [p for p in paths
                 if datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
                 >= floor]
    chosen = select_latest_pregame(paths)

    winner_n = winner_correct = 0
    brier_terms: List[float] = []
    logloss_terms: List[float] = []
    margin_err: List[float] = []
    total_err: List[float] = []
    pending = 0
    ref_rows: List[dict] = []

    for fc in chosen.values():
        final = match_final(games, fc["away"], fc["home"], fc["_kickoff"])
        if final is None or pd.isna(final["home_score"]) \
                or pd.isna(final["away_score"]):
            pending += 1
            continue
        hp, ap = float(final["home_score"]), float(final["away_score"])
        p_home = fc.get("p_home_win")
        if p_home is not None and hp != ap:
            y = 1.0 if hp > ap else 0.0
            winner_n += 1
            winner_correct += int((p_home >= 0.5) == bool(y))
            brier_terms.append((p_home - y) ** 2)
            p = min(max(p_home, 1e-9), 1 - 1e-9)
            logloss_terms.append(-(y * np.log(p) + (1 - y) * np.log(1 - p)))
        if fc.get("projected_margin") is not None:
            margin_err.append(abs((hp - ap) - float(fc["projected_margin"])))
        if fc.get("projected_total") is not None:
            total_err.append(abs((hp + ap) - float(fc["projected_total"])))
        for ref in fc.get("references", []):
            if ref.get("decimal") is None:
                continue
            result = grade_reference(ref, hp, ap)
            if result is None:
                continue
            ref_rows.append({
                "family": ref.get("family"), "source": ref.get("source"),
                "status": ref.get("status"),
                "edge_band": _edge_band(ref.get("edge")),
                "result": result,
                "units": units(result, float(ref["decimal"])),
            })

    def _ref_report() -> dict:
        out: Dict[str, dict] = {}
        for key in ("family", "source", "status", "edge_band"):
            groups: Dict[str, List[dict]] = {}
            for row in ref_rows:
                groups.setdefault(str(row[key]), []).append(row)
            out[key] = {
                name: {
                    "n": len(rows),
                    "wins": sum(1 for r in rows if r["result"] == "win"),
                    "losses": sum(1 for r in rows if r["result"] == "loss"),
                    "pushes": sum(1 for r in rows if r["result"] == "push"),
                    "units": round(sum(r["units"] for r in rows), 2),
                    "roi": round(sum(r["units"] for r in rows) / len(rows), 4),
                }
                for name, rows in sorted(groups.items())
            }
        return out

    return {
        "grading_version": FORECAST_GRADING_VERSION,
        "model_version": FORECAST_MODEL_VERSION,
        "graded_at_utc": datetime.now(timezone.utc).isoformat(),
        "snapshots": len(paths),
        "forecasts_considered": len(chosen),
        "pending": pending,
        "winner": {
            "n": winner_n,
            "correct": winner_correct,
            "accuracy": round(winner_correct / winner_n, 4) if winner_n else None,
            "brier": round(float(np.mean(brier_terms)), 4) if brier_terms else None,
            "log_loss": (round(float(np.mean(logloss_terms)), 4)
                         if logloss_terms else None),
        },
        "projection": {
            "margin_mae": (round(float(np.mean(margin_err)), 3)
                           if margin_err else None),
            "total_mae": (round(float(np.mean(total_err)), 3)
                          if total_err else None),
        },
        "references": _ref_report(),
    }


def render_report(stats: dict) -> str:
    lines = [
        f"NFL forecast grade — {FORECAST_GRADING_VERSION} "
        f"({FORECAST_MODEL_VERSION})",
        f"generated {stats['graded_at_utc']} | snapshots {stats['snapshots']} "
        f"| forecasts {stats['forecasts_considered']} | pending "
        f"{stats['pending']}",
        "",
        "Forecast quality:",
        f"  winner n={stats['winner']['n']} "
        f"accuracy={stats['winner']['accuracy']} "
        f"brier={stats['winner']['brier']} "
        f"log_loss={stats['winner']['log_loss']}",
        f"  margin MAE={stats['projection']['margin_mae']} "
        f"total MAE={stats['projection']['total_mae']}",
        "",
        "Reference ROI (flat 1u at captured price; descriptive only):",
    ]
    for key, groups in stats["references"].items():
        lines.append(f"  by {key}:")
        for name, g in groups.items():
            lines.append(
                f"    {name:<12} n={g['n']:<4} {g['wins']}-{g['losses']}-"
                f"{g['pushes']} units={g['units']:+.2f} roi={g['roi']:+.1%}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="grade forecast-first snapshots")
    ap.add_argument("--since", default="", help="ISO date lower bound")
    ap.add_argument("--no-export", action="store_true")
    args = ap.parse_args()

    config.ensure_dirs()
    stats = grade_all(args.since)
    text = render_report(stats)
    print(text)
    if not args.no_export:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        FORECAST_BACKTESTS_DIR.mkdir(parents=True, exist_ok=True)
        (FORECAST_BACKTESTS_DIR / f"grade_forecast_{stamp}.txt").write_text(
            text, encoding="utf-8")
        (FORECAST_BACKTESTS_DIR / f"grade_forecast_{stamp}.json").write_text(
            json.dumps(stats, indent=2), encoding="utf-8")
        log(f"[grade_forecast] wrote {stamp} report")
    return 0


if __name__ == "__main__":
    sys.exit(main())
